import asyncio
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import xml.etree.ElementTree as ET

import httpx
from app import search as provider
from app.main import app
from app.schemas import PaperOut


def paper(n, **values):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", sources=["arxiv"], **values)


class RegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3")})
        self.env.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)
        for cache in (provider._CACHE, provider._PAPERS, provider._GATES, provider._NEXT, provider._COOLDOWN):
            cache.clear()

    async def test_concurrent_identical_requests_share_cache(self):
        count = 0
        async def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom" />')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await asyncio.gather(*[provider.request(client, "https://test/query", {"q": "deep learning"}, {}, "arXiv") for _ in range(5)])
        self.assertEqual(count, 1)

    async def test_retry_after_blocks_other_queries_without_request(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(429, headers={"Retry-After": "120"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for q in ("one", "two"):
                with self.assertRaises(provider.SourceFailure):
                    await provider.request(client, "https://test/query", {"q": q}, {}, "arXiv")
        self.assertEqual(count, 1)

    async def test_rate_limit_does_not_burst_retry_and_exposes_wait(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(429)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(provider.SourceFailure) as error:
                await provider.request(client, "https://test", {}, {}, "arXiv")
            self.assertEqual(error.exception.retry_after, 60)
            self.assertEqual(calls, 1)

    async def test_no_key_all_attempts_s2_and_admin_can_disable(self):
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "", "SEMANTIC_SCHOLAR_ENABLED": "true"}), patch.object(provider, "search_arxiv", new=AsyncMock(return_value=([paper(1)], 1))), patch.object(provider, "search_semantic_scholar", new=AsyncMock(return_value=([paper(2)], 1))) as s2:
            result = await provider.search_papers("test", 1, 20, "relevance", "all", None, None)
            self.assertEqual(result["source_errors"], {})
            self.assertEqual(len(result["papers"]), 2)
            s2.assert_awaited_once()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail("must not call upstream"))) as client:
            with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_ENABLED": "false"}), self.assertRaises(provider.SourceFailure):
                await provider.request(client, "https://test", {}, {}, "Semantic Scholar")

    async def test_duplicate_versions_refill_and_pages_do_not_overlap(self):
        rows = [paper(i) for i in range(19)] + [paper(18)] + [paper(i) for i in range(19, 130)]
        async def fetch(client, query, start, size, *args):
            return rows[start:start + size], len(rows)
        with patch.object(provider, "search_arxiv", side_effect=fetch):
            first = await provider.search_papers("test", 1, 20, "relevance", "arxiv", None, None)
            second = await provider.search_papers("test", 2, 20, "relevance", "arxiv", None, None)
            sixth = await provider.search_papers("test", 6, 20, "relevance", "arxiv", None, None)
        self.assertEqual([len(r["papers"]) for r in (first, second, sixth)], [20, 20, 20])
        self.assertFalse({p.id for p in first["papers"]} & {p.id for p in second["papers"]})
        self.assertEqual([p.id for p in sixth["papers"]], [f"arxiv:{i}" for i in range(100, 120)])

    async def test_partial_failure_retains_arxiv_and_detail_metadata(self):
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "test"}), patch.object(provider, "search_arxiv", new=AsyncMock(return_value=([paper(1, venue="arXiv（预印本）")], 1))), patch.object(provider, "search_semantic_scholar", new=AsyncMock(side_effect=provider.SourceFailure("限流冷却"))):
            result = await provider.search_papers("test", 1, 20, "relevance", "all", None, None)
        self.assertIn("semantic_scholar", result["source_errors"])
        self.assertEqual(await provider.fetch_paper(result["papers"][0].id), result["papers"][0])

    async def test_merged_citation_and_venue_persist_into_detail(self):
        original = paper(1, venue="arXiv（预印本）")
        extra = PaperOut(id="s2:abc", arxiv_id="1", semantic_scholar_id="abc", title="Paper 1", citation_count=5, venue="Example Conference", sources=["semantic_scholar"])
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "test"}), patch.object(provider, "search_arxiv", new=AsyncMock(return_value=([original], 1))), patch.object(provider, "search_semantic_scholar", new=AsyncMock(return_value=([extra], 1))):
            result = await provider.search_papers("test", 1, 20, "relevance", "all", None, None)
        self.assertEqual(len(result["papers"]), 1)
        listed = result["papers"][0]
        self.assertEqual(listed.citation_count, 5)
        self.assertEqual(listed.venue, "Example Conference")
        self.assertEqual(await provider.fetch_paper(listed.id), listed)

    async def test_s2_prefix_never_exceeds_provider_batch_and_result_limits(self):
        calls = []
        async def fetch(offset, size):
            calls.append((offset, size))
            return [paper(i) for i in range(offset, offset + size)], 50000
        rows, total = await provider.unique_prefix(fetch, 1100, 1000)
        self.assertEqual(len(rows), 1000)
        self.assertTrue(all(size <= 100 and offset + size <= 1000 for offset, size in calls))

    async def test_chinese_mapping_validation_health_and_cors(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/api/papers/search", "/api/papers/detail"):
                response = await client.get(path)
                self.assertEqual(response.status_code, 422)
                self.assertIsInstance(response.json()["detail"], str)
                self.assertNotIn("loc", response.text)
            invalid = await client.get("/api/papers/search", params={"q": "test", "year_from": 2025, "year_to": 2020})
            self.assertEqual(invalid.status_code, 400)
            with patch("app.main.search_papers", new=AsyncMock(return_value={"query": "deep learning", "page": 1, "page_size": 10, "total": 0, "total_estimated": False, "papers": [], "source_counts": {}, "source_errors": {}})) as fetch:
                response = await client.get("/api/papers/search", params={"q": "深度学习"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(fetch.call_args.args[0], "deep learning")
            self.assertEqual((await client.get("/api/health")).json(), {"status": "ok"})
            for method, status in [("GET", 200), ("DELETE", 400), ("POST", 200), ("PATCH", 400)]:
                response = await client.options("/api/papers/search", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": method})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["access-control-allow-methods"], "GET, POST")
            self.assertEqual((await client.get("/docs")).status_code, 404)

    def test_venue_is_not_arxiv_category(self):
        entry = ET.fromstring('<entry><id>https://arxiv.org/abs/1234.56789v2</id><title>Test</title><primary_category term="q-bio.OT"/></entry>')
        self.assertEqual(provider.map_arxiv_entry(entry).venue, "arXiv（预印本）")


if __name__ == "__main__":
    unittest.main()

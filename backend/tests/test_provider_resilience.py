import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import provider_cache, search
from app.schemas import PaperOut


PAYLOAD = {"data": [{"paperId": "a" * 40, "title": "Deep learning", "year": 2023,
                      "externalIds": {"ArXiv": "2301.12345"}, "authors": []}], "total": 1}


class ProviderResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                          "SEMANTIC_SCHOLAR_ENABLED": "true", "SEMANTIC_SCHOLAR_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)
        self.clear_memory()

    def clear_memory(self):
        for value in (search._CACHE, search._PAPERS, search._GATES, search._NEXT, search._COOLDOWN):
            value.clear()

    def age_cache(self, days):
        with provider_cache.connection() as db, db:
            db.execute("UPDATE responses SET saved_at = ?", (time.time() - days * 86400,))
        self.clear_memory()

    async def fetch(self, client, query="deep learning"):
        return await search.run_source("semantic_scholar", lambda: search.search_s2_bulk(client, query, None, None, "relevance"))

    async def test_cache_survives_memory_reset_without_upstream(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD))) as client:
            first = await self.fetch(client)
        self.clear_memory()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail("must read disk cache"))) as client:
            second = await self.fetch(client)
        self.assertEqual(first.papers, second.papers)
        self.assertNotIn("历史缓存", second.warning)

    async def test_large_bulk_response_is_persisted_not_only_kept_in_memory(self):
        large = {**PAYLOAD, "data": [{**PAYLOAD["data"][0], "abstract": "paper " * 400000}]}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=large))) as client:
            await self.fetch(client)
        self.clear_memory()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail("large payload must survive restart"))) as client:
            cached = await self.fetch(client)
        self.assertEqual(cached.papers[0].abstract, large["data"][0]["abstract"].strip())

    async def test_stale_results_survive_429_with_timestamp_and_persisted_deadline(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD))) as client:
            await self.fetch(client)
        self.age_cache(2)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "300"}))) as client:
            stale = await self.fetch(client)
        self.assertEqual(len(stale.papers), 1)
        self.assertIsNone(stale.error)
        self.assertIn("历史缓存", stale.warning)
        self.assertTrue(stale.cached_at)
        self.assertEqual(stale.retry_after, 300)
        self.clear_memory()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail("persisted cooldown must prevent request"))) as client:
            cached = await self.fetch(client)
            different = await self.fetch(client, "transformer")
        self.assertEqual(len(cached.papers), 1)
        self.assertEqual(different.papers, [])
        self.assertGreater(different.retry_after, 290)

    async def test_timeout_preserves_same_query_history(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD))) as client:
            await self.fetch(client)
        self.age_cache(2)
        def handler(request):
            raise httpx.ReadTimeout("upstream unavailable")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await self.fetch(client)
        self.assertEqual(len(result.papers), 1)
        self.assertIn("历史缓存", result.warning)

    async def test_expired_history_is_not_used(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=PAYLOAD))) as client:
            await self.fetch(client)
        self.age_cache(8)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as client:
            result = await self.fetch(client)
        self.assertEqual(result.papers, [])
        self.assertIn("429", result.error)

    async def test_bulk_query_sort_and_source_provenance(self):
        def handler(request):
            self.assertTrue(request.url.path.endswith("/search/bulk"))
            self.assertEqual(request.url.params["sort"], "publicationDate:desc")
            self.assertEqual(request.url.params["year"], "2022-2023")
            return httpx.Response(200, json=PAYLOAD)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            papers, total = await search.search_semantic_scholar(client, "deep learning", 0, 10, 2022, 2023, "date")
        self.assertEqual(papers[0].sources, ["semantic_scholar"])

    async def test_arxiv_outage_uses_only_indexed_arxiv_papers_with_explicit_notice(self):
        indexed = PaperOut(id="s2:" + "a" * 40, arxiv_id="2301.12345", title="Deep learning", sources=["semantic_scholar"])
        other = PaperOut(id="s2:" + "b" * 40, title="Other publisher", sources=["semantic_scholar"])
        with patch.object(search, "search_arxiv", new=AsyncMock(side_effect=search.SourceFailure("arXiv HTTP 429", 60))), \
             patch.object(search, "search_s2_bulk", new=AsyncMock(return_value=([indexed, other], 2))):
            result = await search.search_papers("deep learning", 1, 10, "relevance", "arxiv", None, None)
        self.assertEqual(len(result["papers"]), 1)
        self.assertEqual(result["papers"][0].sources, ["semantic_scholar"])
        self.assertIn("索引", result["source_warnings"]["arxiv"])
        self.assertEqual(result["source_retry_after"]["arxiv"], 60)
        self.assertTrue(result["total_estimated"])


if __name__ == "__main__":
    unittest.main()

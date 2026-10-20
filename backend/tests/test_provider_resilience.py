import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import provider_cache
from app.services.retrieval import search
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
        self.assertIn("429", stale.error)
        self.assertEqual(stale.failure["status"], "rate_limited")
        self.assertEqual(stale.failure["http_status"], 429)
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
        with patch.object(search.asyncio, "sleep", new=AsyncMock()):
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
        self.assertEqual(total, 1)

    async def test_relevance_search_uses_paper_search_page(self):
        def handler(request):
            self.assertTrue(request.url.path.endswith("/paper/search"))
            self.assertEqual(request.url.params["offset"], "10")
            self.assertEqual(request.url.params["limit"], "10")
            self.assertEqual(request.url.params["query"], "deep learning")
            self.assertNotIn("token", request.url.params)
            return httpx.Response(200, json={**PAYLOAD, "total": 14902})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            papers, total = await search.search_semantic_scholar(client, "deep learning", 10, 10, None, None, "relevance")
        self.assertEqual(papers[0].sources, ["semantic_scholar"])
        self.assertEqual(total, 14902)

    async def test_relevance_search_stops_after_1000(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: self.fail("must not call upstream"))) as client:
            result = await search.run_source("semantic_scholar", lambda: search.search_semantic_scholar(client, "deep learning", 1000, 10, None, None))
        self.assertEqual(result.papers, [])
        self.assertIn("1000", result.warning)

    async def test_short_429_then_success_does_not_cool_down(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429)
            return httpx.Response(200, json=PAYLOAD)
        with patch.object(search.asyncio, "sleep", new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                papers, total = await search.search_semantic_scholar(client, "deep learning", 0, 10, None, None)
        self.assertEqual(calls, 2)
        self.assertEqual(len(papers), 1)
        self.assertEqual(total, 1)
        self.assertEqual(search._COOLDOWN.get("Semantic Scholar", 0), 0)
        self.assertEqual(provider_cache.retry_at("Semantic Scholar"), 0)

    async def test_keyed_retry_respects_quota_even_with_zero_retry_after(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            self.assertEqual(request.headers.get("x-api-key"), "test-approved-key")
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json=PAYLOAD)

        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "test-approved-key"}), \
             patch.object(search.asyncio, "sleep", new=AsyncMock()) as sleep:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                papers, _ = await search.search_semantic_scholar(client, "deep learning", 0, 10, None, None)
        self.assertEqual(calls, 2)
        self.assertEqual(len(papers), 1)
        self.assertGreaterEqual(sleep.await_args_list[-1].args[0], 1.1)

    async def test_keyed_requests_do_not_inherit_anonymous_cooldown(self):
        provider_cache.set_retry_at("Semantic Scholar", time.time() + 900)
        search._COOLDOWN["Semantic Scholar"] = time.monotonic() + 900
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.headers.get("x-api-key"), "approved-key")
            return httpx.Response(200, json=PAYLOAD)
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "approved-key"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                rows, _ = await search.search_semantic_scholar(client, "fresh keyed query", 0, 10, None, None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 1)
        self.assertGreater(provider_cache.retry_at("Semantic Scholar"), time.time())

    async def test_keyed_cooldown_survives_restart_but_not_key_rotation(self):
        first = search.quota_scope("Semantic Scholar", {"x-api-key": "first-key"})
        second = search.quota_scope("Semantic Scholar", {"x-api-key": "second-key"})
        self.assertNotEqual(first, second)
        self.assertNotIn("first-key", first)
        provider_cache.set_retry_at(first, time.time() + 900)
        self.clear_memory()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail("same key must honor its cooldown"))) as client:
            with self.assertRaises(search.SourceFailure):
                await search.request(client, "https://api.semanticscholar.org/graph/v1/paper/search", {"query": "blocked"}, {"x-api-key": "first-key"}, "Semantic Scholar")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=PAYLOAD))) as client:
            result = await search.request(client, "https://api.semanticscholar.org/graph/v1/paper/search", {"query": "new key"}, {"x-api-key": "second-key"}, "Semantic Scholar")
        self.assertEqual(result.status_code, 200)

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

    async def test_openalex_supplies_arxiv_records_when_the_index_has_none(self):
        journal = PaperOut(id="s2:" + "b" * 40, title="A journal article", sources=["semantic_scholar"])
        preprint = PaperOut(id="arxiv:2406.11267", arxiv_id="2406.11267", title="Faithful finetuning", sources=["openalex"])
        with patch.object(search, "search_arxiv", new=AsyncMock(side_effect=search.SourceFailure("arXiv 上游返回 429（限流），约 60 秒后可重试", 60))), \
             patch.object(search, "search_s2_bulk", new=AsyncMock(return_value=([journal], 1))), \
             patch.object(search, "search_openalex_arxiv", new=AsyncMock(return_value=[preprint])):
            result = await search.search_papers("hallucination", 1, 10, "relevance", "arxiv", None, None)
        self.assertEqual(result["papers"][0].arxiv_id, "2406.11267")
        self.assertIn("OpenAlex", result["source_warnings"]["arxiv"])
        self.assertIn("429", result["source_errors"]["arxiv"])

    async def test_chinese_topic_does_not_spend_an_arxiv_request(self):
        preprint = PaperOut(id="arxiv:2406.11267", arxiv_id="2406.11267", title="Faithful finetuning", sources=["semantic_scholar"])
        live = AsyncMock(side_effect=AssertionError("must not call arXiv for an untranslated Chinese query"))
        with patch.object(search, "search_arxiv", new=live), \
             patch.object(search, "search_s2_bulk", new=AsyncMock(return_value=([preprint], 1))):
            result = await search.search_papers("基层大模型", 1, 10, "relevance", "arxiv", None, None)
        live.assert_not_awaited()
        self.assertEqual(result["source_errors"], {})
        self.assertEqual(result["papers"][0].arxiv_id, "2406.11267")

    async def test_arxiv_rate_limit_keeps_results_from_the_other_source(self):
        paper = PaperOut(id="s2:" + "c" * 40, title="Kept", sources=["semantic_scholar"])
        with patch.object(search, "arxiv_with_index_fallback", new=AsyncMock(side_effect=search.SourceFailure("arXiv 上游返回 429（限流），约 60 秒后可重试", 60))), \
             patch.object(search, "search_semantic_scholar", new=AsyncMock(return_value=([paper], 1))):
            result = await search.search_papers("hallucination", 1, 10, "relevance", "all", None, None)
        self.assertIn("429", result["source_errors"]["arxiv"])
        self.assertEqual(result["papers"][0].title, "Kept")
        self.assertNotIn("arxiv", result["source_counts"])

    def test_openalex_landing_url_becomes_an_arxiv_id(self):
        paper = search.map_openalex_work({
            "display_name": "Faithful Finetuning",
            "publication_year": 2024,
            "cited_by_count": 3,
            "authorships": [{"author": {"display_name": "Ada"}}],
            "primary_location": {"landing_page_url": "http://arxiv.org/abs/2406.11267v1"},
            "abstract_inverted_index": {"Faithful": [0], "tuning": [1]},
            "doi": "https://doi.org/10.48550/arxiv.2406.11267",
        })
        self.assertEqual(paper.arxiv_id, "2406.11267")
        self.assertEqual(paper.abstract, "Faithful tuning")
        self.assertIsNone(paper.doi)
        self.assertEqual(paper.sources, ["openalex"])

    async def test_s2_500_retries_then_success(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return httpx.Response(500)
            return httpx.Response(200, json=PAYLOAD)

        with patch.object(search.asyncio, "sleep", new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                papers, _ = await search.search_semantic_scholar(client, "deep learning", 0, 10, None, None)
        self.assertEqual(calls, 3)
        self.assertEqual(len(papers), 1)

    async def test_s2_500_exhausts_and_raises(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        with patch.object(search.asyncio, "sleep", new=AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(search.SourceFailure) as caught:
                    await search.search_semantic_scholar(client, "deep learning", 0, 10, None, None)
        self.assertEqual(calls, 3)
        self.assertIn("500", str(caught.exception))

    async def test_arxiv_500_is_not_retried(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(search.SourceFailure):
                await search.request(client, "https://export.arxiv.org/api/query", {"search_query": "all:x"}, {"User-Agent": "test"}, "arXiv")
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()

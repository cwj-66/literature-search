"""Source errors stay classified while partial results remain usable."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.schemas import PaperOut
from app.services.retrieval import search
from app.tools import finding_tools


class FindingSourceStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH":str(Path(self.temp.name)/"papers.sqlite3"), "SEMANTIC_SCHOLAR_ENABLED":"true"})
        self.env.start()
        search._CACHE.clear(); search._NEXT.clear(); search._COOLDOWN.clear(); search._GATES.clear()

    def tearDown(self):
        search._CACHE.clear(); search._NEXT.clear(); search._COOLDOWN.clear(); search._GATES.clear()
        self.env.stop(); self.temp.cleanup()

    async def test_http_errors_classified_without_parsing_message(self):
        for status, code in [(401,"unauthorized"), (403,"unauthorized"), (400,"bad_request"), (503,"upstream"), (429,"rate_limited")]:
            with self.subTest(status=status):
                search._NEXT.clear(); search._COOLDOWN.clear()
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, headers={"Retry-After":"300"}))) as client:
                    async def fetch():
                        await search.request(client, "https://example.test/"+str(status), {}, {}, "arXiv")
                        return [], 0
                    result = await search.run_source("arxiv", fetch)
                self.assertEqual(result.failure["status"], code)
                self.assertEqual(result.failure["http_status"], status)
                self.assertFalse(result.failure["retryable"])

    async def test_transport_errors_classified(self):
        for exc, code in [(httpx.ReadTimeout("synthetic"),"timeout"), (httpx.ConnectError("synthetic"),"network")]:
            with patch.object(search, "search_arxiv", new=AsyncMock(side_effect=exc)):
                result = await search.run_source("arxiv", lambda: search.search_arxiv(None))
            self.assertEqual(result.failure["status"], code)

    async def test_one_failed_source_keeps_real_candidates_and_error(self):
        candidate = PaperOut(id="arxiv:2401.00001", title="Synthetic image generation", year=2024, sources=["arxiv"])
        with patch.object(search, "arxiv_with_index_fallback", new=AsyncMock(return_value=([candidate], 1))), \
             patch.object(search, "search_semantic_scholar", new=AsyncMock(side_effect=search.SourceFailure("S2 quota",120,code="rate_limited",http_status=429))):
            result = await finding_tools.search_candidates(finding_tools.SearchArgs(query="image generation", year_from=2023))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["papers"][0]["id"], candidate.id)
        failed = next(r for r in result["sources"] if r["source"] == "semantic_scholar")
        self.assertEqual(failed["http_status"], 429)
        self.assertEqual(failed["retry_after_seconds"], 120)

    async def test_all_sources_failed_is_not_successful_zero_results(self):
        with patch.object(search, "arxiv_with_index_fallback", new=AsyncMock(side_effect=httpx.ReadTimeout("synthetic"))), \
             patch.object(search, "search_semantic_scholar", new=AsyncMock(side_effect=search.SourceFailure("S2 unauthorized", code="unauthorized", http_status=401))):
            result = await finding_tools.search_candidates(finding_tools.SearchArgs(query="image generation"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["papers"], [])
        self.assertEqual({r["status"] for r in result["sources"]}, {"timeout", "unauthorized"})

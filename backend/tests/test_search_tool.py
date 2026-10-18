import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from app.tools import TOOLS
from app.tools import search_papers as tool


def snapshot(**overrides):
    return {
        "query": "deep learning", "page": 1, "page_size": 10,
        "total": 0, "total_estimated": False, "papers": [],
        "source_counts": {"arxiv": 0}, "source_errors": {}, **overrides,
    }


class SearchToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_query_and_preserves_filters(self):
        provider = AsyncMock(return_value=snapshot())
        with patch.object(tool.search, "search_papers", provider):
            result = await tool.search_papers(" 深度学习 ", year_from=2020, year_to=2024)
        provider.assert_awaited_once_with("deep learning", 1, 10, "relevance", "all", 2020, 2024)
        self.assertEqual(result["input_query"], "深度学习")
        self.assertEqual(result["query"], "deep learning")
        self.assertEqual(result["status"], "completed")
        self.assertIn("search_papers", [item["name"] for item in TOOLS])

    async def test_invalid_arguments_never_reach_provider(self):
        provider = AsyncMock()
        with patch.object(tool.search, "search_papers", provider):
            for args in ({"query": " "}, {"query": "RAG", "page_size": 21},
                         {"query": "RAG", "year_from": 2025, "year_to": 2020},
                         {"query": "RAG", "source": "unknown"}):
                with self.subTest(args=args), self.assertRaises(ValidationError):
                    await tool.search_papers(**args)
        provider.assert_not_awaited()

    async def test_partial_results_keep_papers_and_source_diagnostics(self):
        paper = {"id": "arxiv:2401.00001", "title": "RAG", "authors": [], "sources": ["arxiv"]}
        data = snapshot(papers=[paper], total=1, source_counts={"arxiv": 1},
                        source_errors={"semantic_scholar": "限流"}, source_retry_after={"semantic_scholar": 30})
        with patch.object(tool.search, "search_papers", AsyncMock(return_value=data)):
            result = await tool.search_papers("RAG")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["papers"][0]["id"], paper["id"])
        self.assertEqual(result["source_retry_after"], {"semantic_scholar": 30})

    async def test_failed_sources_differ_from_successful_empty_results(self):
        for data, expected in (
            (snapshot(source_counts={}, source_errors={"arxiv": "超时"}), "failed"),
            (snapshot(source_errors={"semantic_scholar": "超时"}), "partial"),
            (snapshot(source_warnings={"arxiv": "使用缓存"}), "partial"),
            (snapshot(), "completed"),
        ):
            with patch.object(tool.search, "search_papers", AsyncMock(return_value=data)):
                self.assertEqual((await tool.search_papers("RAG"))["status"], expected)

    async def test_post_endpoint_and_request_validation(self):
        from app.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.object(tool.search, "search_papers", AsyncMock(return_value=snapshot())):
                response = await client.post("/api/tools/search_papers", json={"query": "深度学习"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["tool"], "search_papers")
            response = await client.post("/api/tools/search_papers", json={"query": " "})
            self.assertEqual(response.status_code, 422)

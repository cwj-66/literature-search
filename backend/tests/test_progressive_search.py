import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.services.retrieval import search
from app.main import app
from app.schemas import PaperOut


class ProgressiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_source_arrives_before_slow_source_and_final_order_is_stable(self):
        gate = asyncio.Event()
        fast = PaperOut(id="arxiv:2005.11401", arxiv_id="2005.11401", title="fast", year=2020, sources=["arxiv"])
        slow = PaperOut(id="s2:test", title="slow", year=2021, sources=["semantic_scholar"])
        async def arxiv(*_args): return [fast], 1
        async def s2(*_args):
            await gate.wait()
            return [slow], 1
        with patch("app.services.retrieval.search.arxiv_with_index_fallback", arxiv), patch("app.services.retrieval.search.search_semantic_scholar", s2), patch("app.services.retrieval.search.semantic_enabled", return_value=True):
            stream = search.search_papers_stream("test", 1, 10, "relevance", "all", None, None)
            initial = await anext(stream)
            self.assertEqual(initial["pending_sources"], ["arxiv", "semantic_scholar"])
            first = await asyncio.wait_for(anext(stream), 1)
            self.assertEqual([p.id for p in first["papers"]], [fast.id])
            self.assertEqual(first["pending_sources"], ["semantic_scholar"])
            gate.set()
            final = await anext(stream)
            self.assertEqual(final["pending_sources"], [])
            ordinary = await search.search_papers("test", 1, 10, "relevance", "all", None, None)
            self.assertEqual(final, ordinary)
            await stream.aclose()

    async def test_disconnecting_cancels_pending_provider(self):
        stopped = asyncio.Event()
        async def arxiv(*_args): return [], 0
        async def s2(*_args):
            try: await asyncio.Event().wait()
            finally: stopped.set()
        with patch("app.services.retrieval.search.arxiv_with_index_fallback", arxiv), patch("app.services.retrieval.search.search_semantic_scholar", s2), patch("app.services.retrieval.search.semantic_enabled", return_value=True):
            stream = search.search_papers_stream("test", 1, 10, "relevance", "all", None, None)
            await anext(stream)
            await anext(stream)
            await stream.aclose()
            self.assertTrue(stopped.is_set())

    async def test_stream_uses_same_validation_and_emits_complete_json_snapshots(self):
        async def arxiv(*_args): return [], 0
        with patch("app.services.retrieval.search.arxiv_with_index_fallback", arxiv):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                invalid = await client.get("/api/papers/search", params={"q": "test", "stream": "true", "year_from": 2025, "year_to": 2023})
                self.assertEqual(invalid.status_code, 400)
                response = await client.get("/api/papers/search", params={"q": "深度学习", "stream": "true", "source": "arxiv"})
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        values = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(values[0]["pending_sources"], ["arxiv"])
        self.assertEqual(values[-1]["pending_sources"], [])
        self.assertEqual(values[-1]["query"], "deep learning")

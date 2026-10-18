"""QA requires an explicit selection and keeps retrieval inside it."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.main import app
from app.schemas import PaperOut
from app.services.papers import library
from app.services.retrieval import hybrid


class QAScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3")})
        self.env.start()
        self.a, _ = library.save_paper(PaperOut(id="arxiv:2401.12345", title="Alpha", abstract="Alpha retrieval evidence."), "arxiv:2401.12345")
        self.b, _ = library.save_paper(PaperOut(id="arxiv:2401.12346", title="Beta", abstract="Beta private evidence."), "arxiv:2401.12346")
        hybrid.index_abstracts([self.a, self.b])

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def post(self, body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/qa", json=body)

    async def test_missing_or_empty_selection_never_calls_answer(self):
        with patch("app.services.analysis.attribution.answer_question", new_callable=AsyncMock) as answer:
            for body in ({"question": "Evidence?"}, {"question": "Evidence?", "paper_ids": []}):
                self.assertEqual((await self.post(body)).status_code, 422)
            answer.assert_not_awaited()

    async def test_unknown_paper_rejects_entire_request(self):
        with patch("app.services.analysis.attribution.answer_question", new_callable=AsyncMock) as answer:
            response = await self.post({"question": "Evidence?", "paper_ids": [self.a.local_id, "missing"]})
            self.assertEqual(response.status_code, 400)
            answer.assert_not_awaited()

    async def test_aliases_deduplicate_and_do_not_depend_on_recent_list(self):
        with patch("app.services.papers.library.list_recent", side_effect=AssertionError("must resolve any selected paper")), patch(
            "app.services.analysis.attribution.answer_question", new=AsyncMock(return_value={"answer": "ok"})
        ) as answer:
            response = await self.post({"question": "Evidence?", "paper_ids": [self.a.local_id, self.a.id]})
            self.assertEqual(response.status_code, 200)
            answer.assert_awaited_once_with("Evidence?", [self.a.id])

    async def test_real_retrieval_excludes_unselected_papers(self):
        # Keep real SQL/keyword filtering; replace external ranking services only.
        async def ranked(query, documents, top_n):
            return [(i, 1.0) for i in range(min(top_n, len(documents)))]

        async def answer(question, ids):
            hits = await hybrid.retrieve(question, ids)
            return {"paper_ids": sorted({hit["paper_id"] for hit in hits})}

        with patch("app.services.retrieval.hybrid.vector_search", new=AsyncMock(return_value=[])), patch(
            "app.services.retrieval.hybrid.rerank.rerank", new=ranked
        ), patch("app.services.analysis.attribution.answer_question", new=answer):
            for picked, expected in [([self.a.local_id], [self.a.id]), ([self.a.local_id, self.b.local_id], sorted([self.a.id, self.b.id]))]:
                response = await self.post({"question": "evidence", "paper_ids": picked})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["paper_ids"], expected)

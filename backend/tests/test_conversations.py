import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.main import app
from app.schemas import PaperOut
from app.services.papers import conversations, library, workspaces
from app.services.retrieval import hybrid


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3")})
        self.env.start()
        workspaces.register("a", "Topic A")
        workspaces.register("b", "Topic B")
        self.p, _ = library.save_paper(PaperOut(id="arxiv:2401.12345", title="Alpha", abstract="Alpha retrieval evidence."), "arxiv:2401.12345")
        self.q, _ = library.save_paper(PaperOut(id="arxiv:2401.12346", title="Beta", abstract="Beta private evidence."), "arxiv:2401.12346")
        workspaces.add("a", self.p.local_id)
        workspaces.add("b", self.q.local_id)
        self.chat = conversations.create("a", "新对话", [self.p.local_id])
        self.base = f'/api/workspaces/a/conversations/{self.chat["id"]}'

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def request(self, method, url, body=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, url, json=body)

    async def ask(self, request_id="r1", refs=None, question="What evidence?"):
        return await self.request("POST", self.base + "/messages", {
            "question": question, "paper_ids": refs if refs is not None else [self.p.local_id], "request_id": request_id,
        })

    def answer(self):
        return {"answer": "Alpha [E0001]", "evidence": [{"evidence_id": "E0001", "paper_id": self.p.id, "quote": "Alpha retrieval evidence."}], "reviews": [], "coverage_gap": False}

    async def test_workspace_and_selection_boundaries(self):
        with patch("app.services.analysis.attribution.answer_question", new_callable=AsyncMock) as model:
            self.assertEqual((await self.ask(refs=[])).status_code, 422)
            self.assertEqual((await self.ask(refs=[self.q.local_id])).status_code, 400)
            self.assertEqual((await self.ask(refs=[self.p.id, self.q.id])).status_code, 400)
            self.assertEqual((await self.request("GET", self.base.replace("/a/", "/b/"))).status_code, 404)
            self.assertEqual((await self.request("POST", self.base.replace("/a/", "/b/"), {"paper_ids": []})).status_code, 404)
            model.assert_not_awaited()

    async def test_saved_messages_scope_and_evidence_survive_reload(self):
        with patch("app.services.analysis.attribution.answer_question", new=AsyncMock(return_value=self.answer())) as model:
            result = await self.ask(refs=[self.p.id])
            self.assertEqual(result.status_code, 200)
            model.assert_awaited_once_with("What evidence?", [self.p.id], history=[], paper_titles={self.p.id: "Alpha"})
        saved = (await self.request("GET", self.base)).json()
        self.assertEqual(saved["messages"][0]["response"], self.answer())
        self.assertEqual(saved["paper_ids"], [self.p.local_id])
        self.assertEqual(saved["title"], "What evidence?")
        self.assertEqual(saved["messages"][0]["papers"][0]["title"], "Alpha")

    async def test_each_conversation_keeps_own_scope_and_title(self):
        second = (await self.request("POST", "/api/workspaces/a/conversations", {"title": "Methods"})).json()
        self.assertEqual(second["paper_ids"], [])
        await self.request("POST", self.base, {"title": "My notes", "paper_ids": []})
        first = (await self.request("GET", self.base)).json()
        self.assertEqual(first["title"], "My notes")
        self.assertEqual(first["paper_ids"], [])
        self.assertEqual(len((await self.request("GET", "/api/workspaces/a/conversations")).json()), 2)
        self.assertEqual((await self.request("GET", "/api/workspaces/b/conversations")).json(), [])
        self.assertEqual((await self.request("POST", self.base, {"title": "  "})).status_code, 422)

    async def test_duplicate_requests_and_parallel_send_are_safe(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return self.answer()

        with patch("app.services.analysis.attribution.answer_question", new=AsyncMock(side_effect=delayed)) as model:
            running = asyncio.create_task(self.ask())
            await asyncio.wait_for(entered.wait(), 3)
            self.assertEqual((await self.ask("parallel")).status_code, 409)
            release.set()
            self.assertEqual((await running).status_code, 200)
            replay = await self.ask()
            self.assertEqual(replay.json()["status"], "completed")
            self.assertEqual(len(conversations.get("a", self.chat["id"])["messages"]), 1)
            self.assertEqual(model.await_count, 1)

    async def test_range_change_excludes_old_question_context_and_keeps_old_evidence(self):
        workspaces.add("a", self.q.local_id)
        with patch("app.services.analysis.attribution.answer_question", new=AsyncMock(return_value=self.answer())) as model:
            await self.ask()
            await self.ask("r2", question="Explain further")
            self.assertEqual(model.call_args.kwargs["history"], ["What evidence?"])
            await self.ask("r3", refs=[self.q.local_id], question="What about Beta?")
            self.assertEqual(model.call_args.kwargs["history"], [])
        saved = conversations.get("a", self.chat["id"])
        self.assertEqual(saved["paper_ids"], [self.q.local_id])
        self.assertEqual(saved["messages"][0]["response"]["evidence"][0]["paper_id"], self.p.id)
        self.assertEqual(saved["messages"][-1]["papers"][0]["paper_id"], self.q.id)

    async def test_real_retrieval_and_model_context_are_scoped(self):
        hybrid.index_abstracts([self.p, self.q])

        async def ranked(query, documents, top_n):
            return [(i, 1.0) for i in range(min(top_n, len(documents)))]

        with patch("app.services.retrieval.hybrid.vector_search", new=AsyncMock(return_value=[])), patch(
            "app.services.retrieval.hybrid.rerank.rerank", new=ranked
        ), patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[
            '{"answer":"Alpha [E0001]", "claim_texts":[]}', '{"reviews":[]}'
        ])) as model:
            result = await self.ask(question="evidence")
        self.assertEqual(result.json()["status"], "completed")
        evidence = result.json()["response"]["evidence"]
        self.assertTrue(evidence)
        self.assertEqual({e["paper_id"] for e in evidence}, {self.p.id})
        self.assertNotIn("Beta private", str(model.call_args_list))

    async def test_failures_and_server_restart_are_recoverable(self):
        with patch("app.services.analysis.attribution.answer_question", new=AsyncMock(side_effect=RuntimeError("offline"))):
            self.assertEqual((await self.ask()).json()["status"], "failed")
        conversations.begin("a", self.chat["id"], "Next question", [self.p.local_id], "restart")
        conversations.recover()
        self.assertEqual(conversations.get("a", self.chat["id"])["messages"][-1]["status"], "failed")

    async def test_removed_paper_and_deleted_workspace_are_not_queryable(self):
        workspaces.remove("a", [self.p.local_id])
        self.assertEqual((await self.ask()).status_code, 400)
        workspaces.delete("a")
        self.assertEqual((await self.request("GET", self.base)).status_code, 404)

    async def test_delete_conversation_does_not_delete_another(self):
        second = conversations.create("a", "Keep", [])
        self.assertEqual((await self.request("POST", self.base + "/delete", {})).status_code, 200)
        self.assertEqual((await self.request("GET", self.base)).status_code, 404)
        self.assertEqual(conversations.get("a", second["id"])["title"], "Keep")

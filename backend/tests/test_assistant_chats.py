import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from app.assistant_chats import router
from app.services.papers import workspaces, library
from app.schemas import PaperOut


class AssistantChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "data.sqlite3")})
        self.env.start()
        workspaces.register("a", "Alpha")
        workspaces.register("b", "Beta")
        self.app = FastAPI(); self.app.include_router(router)

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    async def post(self, path, body):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            return await client.post(path, json=body)

    async def listing(self, workspace="a"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            return await client.get(f"/api/workspaces/{workspace}/assistant-chats")

    async def test_save_reload_pin_archive_and_version_conflict(self):
        path = "/api/workspaces/a/assistant-chats/save"
        chat = {"id": "first", "title": "Research", "turns": [{"id": 1, "question": "RAG?"}], "selectedIds": ["p1"]}
        first = await self.post(path, chat)
        self.assertEqual(first.status_code, 200, first.text)
        updated = {**first.json(), "pinned": True, "archived": True, "title": "Renamed"}
        second = await self.post(path, updated)
        self.assertEqual(second.status_code, 200)
        saved = (await self.listing()).json()["chats"][0]
        self.assertEqual(saved["title"], "Renamed")
        self.assertTrue(saved["pinned"] and saved["archived"])
        self.assertEqual(saved["turns"], chat["turns"])
        self.assertEqual((await self.post(path, updated)).status_code, 409)

    async def test_import_is_idempotent_and_deleted_chat_cannot_resurrect(self):
        path = "/api/workspaces/a/assistant-chats"
        body = {"chats": [{"id": "legacy", "title": "Old", "turns": []}]}
        imported = (await self.post(path + "/import", body)).json()["chats"][0]
        again = (await self.post(path + "/import", body)).json()["chats"][0]
        self.assertEqual(imported["version"], again["version"])
        deleted = await self.post(path + "/legacy/delete", {"version": imported["version"]})
        self.assertEqual(deleted.status_code, 200)
        result = (await self.post(path + "/import", body)).json()
        self.assertEqual(result["chats"], [])
        self.assertEqual(result["deleted_ids"], ["legacy"])
        self.assertEqual((await self.post(path + "/save", imported)).status_code, 409)

    async def test_workspace_isolation_and_papers_survive_chat_delete(self):
        paper, _ = library.save_paper(PaperOut(id="arxiv:1234.56789", title="Shared paper"), "arxiv:1234.56789")
        workspaces.add("a", paper.local_id)
        a = (await self.post("/api/workspaces/a/assistant-chats/save", {"id": "first", "title": "A"})).json()
        b = (await self.post("/api/workspaces/b/assistant-chats/save", {"id": "first", "title": "B"})).json()
        await self.post("/api/workspaces/a/assistant-chats/first/delete", {"version": a["version"]})
        self.assertEqual((await self.listing("b")).json()["chats"][0]["title"], b["title"])
        self.assertEqual(len(workspaces.list_papers("a")["papers"]), 1)
        self.assertIsNotNone(library.get_paper(paper.local_id))
        workspaces.delete("b")
        workspaces.register("b", "B again")
        self.assertEqual((await self.listing("b")).json()["chats"], [])

    async def test_empty_name_and_missing_workspace_are_rejected(self):
        self.assertEqual((await self.post("/api/workspaces/a/assistant-chats/save", {"id": "x", "title": "  "})).status_code, 422)
        self.assertEqual((await self.post("/api/workspaces/missing/assistant-chats/save", {"id": "x", "title": "X"})).status_code, 404)


if __name__ == "__main__":
    unittest.main()

import asyncio
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.main import app
from app.infrastructure import demo
from app.infrastructure.db import connection
from app.services.papers import workspaces
from app.agents import agent, store
from app.tools.index_paper import faiss_path


class PortfolioDemoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(self.root / "papers.sqlite3"),
            "PAPER_DEMO": "1", "PAPER_PORTFOLIO_DB": str(self.root / "visitors.sqlite3"),
            "DASHSCOPE_API_KEY": "server-secret-must-not-leak"})
        self.env.start()
        workspaces.register("sample", "示例研究")
        seed = self.root / "demo-seed"
        seed.mkdir()
        with closing(sqlite3.connect(self.root / "papers.sqlite3")) as source, closing(sqlite3.connect(seed / "papers.sqlite3")) as target:
            source.backup(target)
        (seed / "vectors.faiss").write_bytes(b"immutable-vectors")
        with closing(sqlite3.connect(self.root / "visitors.sqlite3")) as db, db:
            db.executescript("CREATE TABLE visitors(id TEXT PRIMARY KEY,blocked INTEGER); CREATE TABLE portfolio_sessions(token_hash TEXT,visitor TEXT,expires REAL);")
            for ident in ("a" * 32, "b" * 32):
                db.execute("INSERT INTO visitors VALUES(?,0)", (ident,))
                db.execute("INSERT INTO portfolio_sessions VALUES(?,?,?)", (hashlib.sha256(ident.encode()).hexdigest(), ident, time.time() + 3600))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://chenwj.click")

    async def asyncTearDown(self):
        await self.client.aclose()
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    def cookie(self, ident="a" * 32):
        return {"Cookie": f"portfolio_session={ident}; gsa_portfolio_visitor={ident}.existing-signature"}

    async def get(self, path="/api/demo/bootstrap", ident="a" * 32):
        return await self.client.get(path, headers=self.cookie(ident))

    async def test_requires_real_portfolio_session(self):
        self.assertEqual((await self.client.get("/api/demo/bootstrap")).status_code, 401)
        bad = self.cookie(); bad["Cookie"] = bad["Cookie"].replace("portfolio_session=" + "a" * 32, "portfolio_session=forged")
        self.assertEqual((await self.client.get("/api/demo/bootstrap", headers=bad)).status_code, 401)

    async def test_revoked_expired_and_blocked_sessions(self):
        for sql in ("UPDATE portfolio_sessions SET expires=0", "UPDATE visitors SET blocked=1", "DELETE FROM portfolio_sessions"):
            with closing(sqlite3.connect(self.root / "visitors.sqlite3")) as db, db:
                db.execute(sql)
            self.assertEqual((await self.get()).status_code, 401)

    async def test_cookie_cannot_select_another_visitor(self):
        headers = {"Cookie": f"portfolio_session={'a'*32}; gsa_portfolio_visitor={'b'*32}.signature"}
        self.assertEqual((await self.client.get("/api/demo/bootstrap", headers=headers)).status_code, 401)

    async def test_workspace_mutations_are_private_and_durable(self):
        first = (await self.get()).json()
        result = await self.client.post("/api/workspaces/register", json={"workspace_id": "private", "name": "A 的研究"}, headers=self.cookie())
        self.assertEqual(result.status_code, 200)
        again = (await self.get()).json()
        other = (await self.get(ident="b" * 32)).json()
        self.assertEqual([p["id"] for p in first["projects"]], ["sample"])
        self.assertEqual({p["id"] for p in again["projects"]}, {"sample", "private"})
        self.assertEqual([p["id"] for p in other["projects"]], ["sample"])
        self.assertEqual(first["generation"], again["generation"])
        self.assertEqual((await self.get("/api/workspaces/private/papers", "b" * 32)).status_code, 404)
        with closing(sqlite3.connect(self.root / "demo-seed/papers.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workspaces").fetchone()[0], 1)

    async def test_no_keys_and_cannot_modify_server_configuration(self):
        response = await self.get()
        self.assertNotIn("server-secret", response.text)
        self.assertTrue(response.json()["model_configured"])
        response = await self.client.post("/api/config/semantic-scholar", json={"api_key": "replace"}, headers=self.cookie())
        self.assertEqual(response.status_code, 403)

    async def test_cross_origin_writes_rejected(self):
        response = await self.client.post("/api/workspaces/register", json={"workspace_id": "x", "name": "x"}, headers={**self.cookie(), "Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)

    async def test_quota_is_separate_and_atomic(self):
        for ident in ("a" * 32, "b" * 32):
            await self.get(ident=ident)
        with demo.scope("a" * 32):
            results = await asyncio.gather(*(asyncio.to_thread(demo.reserve_request, "POST", "/api/research/tasks") for _ in range(35)))
            self.assertEqual(sum(results), 30)
            self.assertTrue(demo.reserve_request("GET", "/api/research/tasks"))
        with demo.scope("b" * 32):
            self.assertTrue(demo.reserve_request("POST", "/api/research/tasks"))

    async def test_paths_context_threads_and_vectors_are_isolated(self):
        await self.get(); await self.get(ident="b" * 32)
        async def paths(ident):
            with demo.scope(ident):
                return await asyncio.to_thread(lambda: (demo.database_path(), store.checkpoint_path(), faiss_path()))
        a, b = await asyncio.gather(paths("a" * 32), paths("b" * 32))
        self.assertTrue(all(x != y for x, y in zip(a, b)))
        self.assertEqual(a[2].read_bytes(), b[2].read_bytes())
        a[2].write_bytes(b"A-only-vector")
        self.assertEqual(b[2].read_bytes(), b"immutable-vectors")
        self.assertEqual(demo.database_path(), self.root / "papers.sqlite3")

    async def test_live_channels_with_same_task_id_do_not_cross_visitors(self):
        with demo.scope("a" * 32):
            agent._claim_local("same-task")
            a = agent._CHANNELS[agent._runtime_id("same-task")]
        with demo.scope("b" * 32):
            agent._claim_local("same-task")
            b = agent._CHANNELS[agent._runtime_id("same-task")]
            self.assertIsNot(a, b)
            agent._release_local("same-task")
        with demo.scope("a" * 32):
            self.assertFalse(a.closed)
            agent._release_local("same-task")

    async def test_graph_checkpointers_are_per_visitor(self):
        with patch.object(agent, "make_async_checkpointer", new=AsyncMock(side_effect=[object(), object()])), patch.object(agent, "build", side_effect=lambda saver: object()):
            with demo.scope("a" * 32):
                a = await agent.graph()
                self.assertIs(await agent.graph(), a)
            with demo.scope("b" * 32):
                self.assertIsNot(await agent.graph(), a)

    async def test_task_snapshots_not_visible_across_visitors(self):
        await self.get(); await self.get(ident="b" * 32)
        with demo.scope("a" * 32):
            store.save_task("private-task", "private-task", "done", {"answer": "A-private"})
        self.assertEqual((await self.get("/api/research/tasks/private-task")).status_code, 200)
        self.assertEqual((await self.get("/api/research/tasks/private-task", "b" * 32)).status_code, 404)
        self.assertEqual((await self.get("/api/research/tasks/private-task/events", "b" * 32)).status_code, 404)

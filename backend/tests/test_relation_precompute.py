import asyncio
import json
import os
import tempfile
import unittest
import httpx
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.papers import library
from app.services.retrieval import relation_precompute as store, recommend


def payload(status="completed", empty=False):
    return {"recommendations": {"computed_at": datetime.now(timezone.utc).isoformat(),
        "groups": [{"key": key, "status": status, "items": [] if empty else
                    [{"id": key + "-first", "title": "First"}, {"id": key + "-second", "title": "Second"}]}
                   for key in ("citation", "semantic", "keyword")], "items": []}, "network": {}}


class PrecomputeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.tmp.name) / "test.sqlite3")})
        env.start(); self.addCleanup(env.stop)
        self.paper = PaperOut(id="arxiv:2401.00001", arxiv_id="2401.00001", title="Seed", year=2024, sources=["arxiv"])
        self.saved, _ = library.save_paper(self.paper, self.paper.id)

    async def test_import_enqueues_once_and_worker_does_not_import_candidates(self):
        library.save_paper(self.paper, self.paper.id)
        with patch.object(recommend, "bundle", new=AsyncMock(return_value=payload())) as compute:
            self.assertTrue(await store.run_one())
            self.assertFalse(await store.run_one())
        compute.assert_awaited_once()
        snapshot = store.read(self.saved.local_id)
        self.assertEqual(snapshot["status"], "ready")
        self.assertTrue(all(len(g["items"]) == 1 for g in snapshot["payload"]["recommendations"]["groups"]))
        self.assertEqual(library.list_papers(1, 100)["total"], 1)

    async def test_import_automatically_queues_analysis_once_and_worker_reads_durable_job(self):
        from app.services.analysis import analysis, analysis_store
        library.save_paper(self.paper, self.paper.id)
        with connection() as db:
            rows = db.execute("SELECT * FROM analysis_tasks WHERE local_id=?", (self.saved.local_id,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(analysis_store.next_pending(), rows[0]["task_id"])
        reached = asyncio.Event()

        async def run(task_id):
            self.assertEqual(task_id, rows[0]["task_id"])
            reached.set()
            await asyncio.Future()

        with patch.object(analysis, "run_task", new=run):
            worker = asyncio.create_task(analysis.worker(asyncio.Queue()))
            try:
                await asyncio.wait_for(reached.wait(), 5)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

    async def test_analysis_enqueue_failure_rolls_back_import_and_relation_job(self):
        from app.services.analysis import analysis_store
        other = PaperOut(id="arxiv:2401.00009", title="Other")
        with patch.object(analysis_store, "create_task_in_transaction", side_effect=RuntimeError("enqueue failed")):
            with self.assertRaises(RuntimeError):
                library.save_paper(other, other.id)
        self.assertIsNone(library.get_paper(other.id))
        with connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM relation_jobs").fetchone()[0], 1)

    async def test_new_import_does_not_invalidate_existing_snapshot(self):
        job = store.claim(); store.finish(job, payload())
        before = store.read(self.saved.local_id)
        library.save_paper(PaperOut(id="arxiv:2401.00002", arxiv_id="2401.00002", title="Second"), "arxiv:2401.00002")
        self.assertEqual(store.read(self.saved.local_id), before)
        self.assertEqual(store.claim()["local_id"], library.get_paper("arxiv:2401.00002").local_id)

    async def test_failure_retries_with_backoff_and_preserves_previous_edges(self):
        store.finish(store.claim(), payload())
        store.ensure_job(self.saved.local_id, True)
        job = store.claim(); store.finish(job, payload("unavailable", empty=True))
        snapshot = store.read(self.saved.local_id)
        self.assertEqual(snapshot["status"], "queued")
        self.assertTrue(all(g["items"] for g in snapshot["payload"]["recommendations"]["groups"]))
        self.assertIsNone(store.claim(), "retry cannot immediately hammer the provider")

    async def test_terminal_failure_stops_automatic_retry(self):
        job = store.claim(); job["attempts"] = store.MAX_ATTEMPTS
        store.finish(job, None, "TimeoutError")
        self.assertEqual(store.read(self.saved.local_id)["status"], "failed")
        self.assertIsNone(store.claim())
        store.ensure_job(self.saved.local_id, True)
        self.assertEqual(store.claim()["attempts"], 1)

    async def test_running_refresh_is_coalesced_and_restart_recovers_it(self):
        first = store.claim()
        store.ensure_job(self.saved.local_id, True)
        self.assertIsNone(store.claim())
        store.recover()
        restored = store.claim()
        self.assertEqual(restored["local_id"], first["local_id"])
        self.assertEqual(restored["revision"], first["revision"])

    async def test_existing_recommendation_cache_is_adopted_without_network(self):
        cached = payload()
        with connection() as db, db:
            db.execute("INSERT INTO paper_relations VALUES (?, ?, ?, ?)",
                       (self.saved.local_id, "old-key", json.dumps(cached), cached["recommendations"]["computed_at"]))
        store.recover()
        self.assertEqual(store.read(self.saved.local_id)["status"], "ready")
        self.assertIsNone(store.claim())

    async def test_completed_empty_is_not_treated_as_provider_failure(self):
        store.finish(store.claim(), payload(empty=True))
        snapshot = store.read(self.saved.local_id)
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["payload"]["recommendations"]["items"], [])

    async def test_worker_error_does_not_leave_job_stuck_running(self):
        with patch.object(recommend, "bundle", new=AsyncMock(side_effect=TimeoutError("test"))):
            with self.assertLogs(store.log, level="ERROR"):
                await store.run_one()
        self.assertEqual(store.read(self.saved.local_id)["status"], "queued")

    async def test_endpoint_reads_pending_then_saved_result_without_doing_the_work(self):
        from app.main import app
        with patch.object(recommend, "bundle", new=AsyncMock(return_value=payload())) as compute:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = (await client.post("/api/papers/relate", json={"ids": [self.saved.local_id]})).json()
                self.assertEqual(first["pending_ids"], [self.saved.local_id])
                self.assertEqual(len(first["centers"]), 1)
                compute.assert_not_awaited()
                await store.run_one()
                ready = (await client.post("/api/papers/relate", json={"ids": [self.saved.local_id]})).json()
                self.assertEqual(ready["pending_ids"], [])
                self.assertTrue(all(len(g["items"]) == 1 for g in ready["satellites"]))
                compute.assert_awaited_once()

"""Step 4 tests: external-call caching, restart recovery, and the research task API."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent, workflow
from app.infrastructure import llm
from app.main import app
from app.schemas import PaperOut


def paper(n, **values):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", sources=["arxiv"], **values)


def planned_queries():
    return {"queries": ["q1"], "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"}


def assess_synthesize():
    return json.dumps({"coverage": "sufficient", "missing": [], "next_action": "synthesize", "rationale": "证据充分", "new_query": None})


EMPTY_SEARCH = {"papers": [], "source_errors": {}, "source_retry_after": {}}


class AgentCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_plan_decompose_cached_across_replay(self):
        decompose = AsyncMock(return_value={"queries": ["q1"], "exclusions": [], "rationale": "按主题拆分检索"})
        with patch("app.services.analysis.attribution.decompose", new=decompose):
            first = await agent.plan_node({**agent.initial_state("同一个问题", auto_approve=True)})
            second = await agent.plan_node({**agent.initial_state("同一个问题", auto_approve=True)})
            other = await agent.plan_node({**agent.initial_state("另一个问题", auto_approve=True)})
        self.assertEqual(decompose.call_count, 2)
        self.assertEqual(first["queries"], ["q1"])
        self.assertEqual(second["queries"], ["q1"])
        self.assertEqual(first["llm_calls"], 1)
        self.assertEqual(second["llm_calls"], 0)

    async def test_search_cache_hit_does_not_rebill_api_calls(self):
        search_mock = AsyncMock(return_value=dict(EMPTY_SEARCH))
        with patch("app.services.retrieval.search.search_papers", new=search_mock):
            first = await workflow.search_node({**agent.initial_state("goal"), "queries": ["q1"]})
            second = await workflow.search_node({**agent.initial_state("goal"), "queries": ["q1"]})
        self.assertEqual(search_mock.call_count, 1)
        self.assertEqual(first["api_calls"], 1)
        self.assertEqual(second.get("api_calls", 0), 0)
        self.assertTrue(second["events"][-1]["cached"])
        self.assertFalse(first["events"][-1]["cached"])

    async def test_search_error_responses_are_not_cached(self):
        limited = {"papers": [], "source_errors": {"arxiv": "arXiv 上游返回 429（限流）"}, "source_retry_after": {"arxiv": 60}}
        search_mock = AsyncMock(return_value=limited)
        with patch("app.services.retrieval.search.search_papers", new=search_mock):
            await workflow.search_node({**agent.initial_state("goal"), "queries": ["q1"]})
            await workflow.search_node({**agent.initial_state("goal"), "queries": ["q1"]})
        self.assertEqual(search_mock.call_count, 2)

    async def test_assess_cache_hit_avoids_llm_call_and_counting(self):
        llm_mock = AsyncMock(side_effect=[assess_synthesize()])
        state = {**agent.initial_state("goal"), "papers": [paper(1).model_dump()]}
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            first = await workflow.assess_node(state)
            second = await workflow.assess_node(state)
        self.assertEqual(llm_mock.call_count, 1)
        self.assertEqual(first["llm_calls"], 1)
        self.assertEqual(second.get("llm_calls", 0), 0)
        self.assertEqual(second["coverage"]["next_action"], "synthesize")

    async def test_llm_failures_are_never_cached(self):
        llm_mock = AsyncMock(side_effect=llm.AnalysisError("模型调用失败"))
        state = {**agent.initial_state("goal"), "papers": [paper(1).model_dump()]}
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            await workflow.assess_node(state)
            await workflow.assess_node(state)
        self.assertEqual(llm_mock.call_count, 2)


class AgentRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "sqlite",
        })
        self.env.start()
        self.llm_guard = patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败")))
        self.llm_guard.start()
        await agent.reset_graph()

    async def asyncTearDown(self):
        await agent.reset_graph()
        await asyncio.sleep(0.05)  # 让 aiosqlite 后台线程落定，避免 Windows 下偶发访问冲突
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    async def _restart(self):
        await agent.reset_graph()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def test_waiting_task_recovers_after_restart_and_resumes(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned_queries())):
            first = await agent.run("如何降低RAG幻觉", task_id="recover-plan")
        self.assertEqual(first["status"], "waiting")

        await self._restart()
        listed = agent.list_tasks()
        row = next(item for item in listed if item["task_id"] == "recover-plan")
        self.assertEqual(row["status"], "waiting")
        self.assertEqual(row["interrupt"], "review_plan")

        recovered = await agent.recover_state("recover-plan")
        self.assertEqual(recovered["status"], "waiting")
        self.assertEqual(recovered["interrupt"]["type"], "review_plan")
        self.assertEqual(recovered["state"]["goal"], "如何降低RAG幻觉")

        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            resumed = await agent.resume_task("recover-plan", {"approved": True})
        self.assertEqual(resumed["status"], "done")
        self.assertTrue(any(event["step"] == "recover" for event in resumed["events"]))

    async def test_paused_task_recovers_after_restart_and_resumes(self):
        agent.request_pause("recover-pause")
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned_queries())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            paused = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="recover-pause")
        self.assertEqual(paused["status"], "waiting")
        self.assertEqual(paused["interrupt"]["type"], "paused")

        await self._restart()
        recovered = await agent.recover_state("recover-pause")
        self.assertEqual(recovered["status"], "waiting")
        self.assertEqual(recovered["interrupt"]["type"], "paused")

        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            resumed = await agent.resume_task("recover-pause", True)
        self.assertEqual(resumed["status"], "done")
        self.assertIsNone(agent._flag_of("recover-pause"))

    async def test_recover_state_reports_done_and_unknown(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned_queries())), \
             patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            done = await agent.run("如何降低RAG幻觉", auto_approve=True, task_id="recover-done")
        self.assertEqual(done["status"], "done")
        recovered = await agent.recover_state("recover-done")
        self.assertEqual(recovered["status"], "done")
        self.assertIsNone(recovered["interrupt"])
        self.assertTrue(recovered["state"]["stop_reason"])
        self.assertIsNone(await agent.recover_state("recover-missing"))

    async def test_resume_value_passthrough_for_both_interrupt_types(self):
        # review_plan 中断：恢复值携带编辑后的 queries
        edited = {"queries": ["edited"], "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"}
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=edited)):
            waiting = await agent.run("如何降低RAG幻觉", task_id="recover-edit")
        self.assertEqual(waiting["status"], "waiting")
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            resumed = await agent.resume_task("recover-edit", {"approved": True, "queries": ["edited"]})
        self.assertEqual(resumed["status"], "done")
        self.assertIn("edited", resumed["queries"])


class ResearchApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        self.llm_guard = patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败")))
        self.llm_guard.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await agent.reset_graph()
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    async def test_cancel_endpoint_then_resume_finishes_as_user_cancel(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned_queries())):
            started = await self.client.post("/api/research/tasks", json={"goal": "如何降低RAG幻觉"})
        self.assertEqual(started.status_code, 200)
        task_id = started.json()["task_id"]
        self.assertEqual(started.json()["status"], "waiting")

        listed = await self.client.get("/api/research/tasks")
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(any(item["task_id"] == task_id and item["status"] == "waiting" for item in listed.json()))

        state = await self.client.get(f"/api/research/tasks/{task_id}/state")
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["interrupt"]["type"], "review_plan")

        cancelled = await self.client.post(f"/api/research/tasks/{task_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertTrue(cancelled.json()["cancelled"])

        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            resumed = await self.client.post(f"/api/research/tasks/{task_id}/resume", json={})
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json()["stop_reason"], "用户取消")

    async def test_pause_endpoint_blocks_resume_until_second_resume(self):
        with patch("app.services.analysis.attribution.decompose", new=AsyncMock(return_value=planned_queries())):
            started = await self.client.post("/api/research/tasks", json={"goal": "如何降低RAG幻觉"})
        task_id = started.json()["task_id"]

        paused = await self.client.post(f"/api/research/tasks/{task_id}/pause")
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.json()["paused"])

        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            first = await self.client.post(f"/api/research/tasks/{task_id}/resume", json={})
        self.assertEqual(first.json()["status"], "waiting")
        self.assertEqual(first.json()["interrupt"]["type"], "paused")

        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=dict(EMPTY_SEARCH))):
            second = await self.client.post(f"/api/research/tasks/{task_id}/resume", json={})
        self.assertEqual(second.json()["status"], "done")

    async def test_unknown_task_returns_404(self):
        missing = await self.client.post("/api/research/tasks/nope/cancel")
        self.assertEqual(missing.status_code, 404)
        missing_state = await self.client.get("/api/research/tasks/nope/state")
        self.assertEqual(missing_state.status_code, 404)

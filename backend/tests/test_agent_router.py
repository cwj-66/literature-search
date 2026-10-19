"""入口 Router 与特权工具（HITL 确认）测试。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents import agent
from app.infrastructure import llm
from app.schemas import PaperOut


def paper(n, **values):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", sources=["arxiv"], **values)


def found(n):
    papers = [paper(i, abstract=f"abstract {i} about RAG hallucination mitigation.").model_dump() for i in range(1, n + 1)]
    return {"papers": papers, "source_errors": {}, "source_retry_after": {}}


def route_json(mode, rationale="r"):
    return json.dumps({"mode": mode, "rationale": rationale})


def rj(*tool_calls, answer=None, thought="t"):
    payload = {"thought": thought}
    if tool_calls:
        payload["tool_calls"] = [{"name": name, "args": args} for name, args in tool_calls]
    else:
        payload["answer"] = answer
    return json.dumps(payload, ensure_ascii=False)


def claims_json(*claims):
    return json.dumps({"claims": [{"text": text, "evidence_ids": eids} for text, eids in claims]})


def judge_keep(*ids):
    return json.dumps({"reviews": [{"claim_id": cid, "verdict": "supported", "issue": None, "action": "keep", "quote": ""} for cid in ids]})


def planned():
    return {"queries": ["q1"], "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"}


def manager(target, mock):
    return patch(target, new=mock)


class RouterTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.llm_guard.stop()
        self.env.stop()
        self.temp.cleanup()

    def managers(self, llm_side_effect, search=None, answer=None, extra=None):
        chunk = {"text": "Retrieval reduces hallucination with citations.", "chunk_id": "c1", "section": "intro", "paragraph_ids": ["p1"]}
        managers = [
            manager("app.services.analysis.attribution.plan_search", AsyncMock(return_value=planned())),
            manager("app.infrastructure.llm.complete", AsyncMock(side_effect=llm_side_effect)),
            manager("app.services.retrieval.search.search_papers", search or AsyncMock(return_value=found(5))),
            manager("app.services.retrieval.hybrid.retrieve", AsyncMock(return_value=[chunk])),
            manager("app.services.analysis.attribution.answer_question", AsyncMock(return_value=answer or {"answer": "unused", "evidence": [], "reviews": []})),
        ]
        for target, mock in (extra or {}).items():
            managers.append(manager(target, mock))
        return managers


class AgentRouterTests(RouterTestBase):
    async def test_complex_goal_routes_to_plan_and_interrupts(self):
        """复杂调研问题 → LLM 判 plan → 走既有计划流并触发审批中断。"""
        side = [route_json("plan", "多子问题调研")]
        managers = self.managers(side)
        mocks = [m.start() for m in managers]
        try:
            result = await agent.run("梳理检索增强在大模型幻觉抑制上的研究脉络并对比主要方法", task_id="rt-plan")
        finally:
            for m in reversed(managers):
                m.stop()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["interrupt"]["type"], "review_plan")
        self.assertEqual(result["route"]["mode"], "plan")
        self.assertEqual(result["route"]["source"], "llm")
        self.assertEqual(mocks[0].call_count, 1)  # 计划分解被调用

    async def test_simple_goal_routes_to_react_without_plan(self):
        """Simple research skips planning but still executes the evidence workflow."""
        search = AsyncMock(return_value={"papers": [], "source_errors": {}})
        side = [route_json("react"), json.dumps({"coverage": "insufficient", "missing": ["papers"], "next_action": "stop"})]
        managers = self.managers(side, search=search)
        mocks = [m.start() for m in managers]
        try:
            result = await agent.run("搜索检索增强论文", auto_approve=True, task_id="rt-react")
        finally:
            for m in reversed(managers):
                m.stop()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["mode"], "react")
        self.assertEqual(mocks[0].call_count, 1)
        self.assertLessEqual(search.await_count, 3)
        self.assertTrue({"prepare", "search", "select"} <= {e["step"] for e in result["events"]})
        self.assertFalse(any(e["step"] == "plan" for e in result["events"]))
        self.assertIn("未筛选到", result["answer"])

    async def test_override_plan_forces_interrupt_for_simple_goal(self):
        """override：简单问题强制 plan → 仍触发审批中断。"""
        managers = self.managers([route_json("react")])  # 即便 LLM 认为 react
        mocks = [m.start() for m in managers]
        try:
            result = await agent.run("什么是检索增强生成", task_id="rt-ov-plan", mode="plan")
        finally:
            for m in reversed(managers):
                m.stop()
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["route"]["source"], "override")
        self.assertEqual(result["route"]["mode"], "plan")
        self.assertEqual(mocks[1].call_count, 0)  # override 不调用路由 LLM

    async def test_override_react_skips_interrupt_for_complex_goal(self):
        """Forcing simple execution skips plan review, not mandatory evidence stages."""
        side = [json.dumps({"coverage": "insufficient", "missing": [], "next_action": "stop"})]
        managers = self.managers(side, search=AsyncMock(return_value={"papers": [], "source_errors": {}}))
        mocks = [m.start() for m in managers]
        try:
            result = await agent.run("比较相关论文", task_id="rt-ov-react", mode="react")
        finally:
            for m in reversed(managers):
                m.stop()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["route"]["source"], "override")
        self.assertIsNone(result["interrupt"])
        self.assertEqual(mocks[0].call_count, 0)
        self.assertEqual(mocks[1].call_count, 1)

    async def test_router_llm_failure_falls_back_to_plan(self):
        """路由 LLM 失败 → 回退 plan（更彻底），事件记录回退，失败尝试计 1 次调用。"""
        managers = self.managers([llm.AnalysisError("模型调用失败")])
        mocks = [m.start() for m in managers]
        try:
            result = await agent.run("某个研究问题", task_id="rt-fallback")
        finally:
            for m in reversed(managers):
                m.stop()
        self.assertEqual(result["status"], "waiting")  # 回退到计划流并等待审批
        self.assertEqual(result["route"]["source"], "rule")
        self.assertEqual(result["route"]["mode"], "plan")
        route_event = next(e for e in result["events"] if e["step"] == "route")
        self.assertIn("回退", route_event["reason"])
        self.assertEqual(result["llm_calls"], 1)  # 失败的尝试仍计费一次

    async def test_router_decision_cached(self):
        """同一问题二次路由命中缓存：LLM 只调用一次，第二次不计数。"""
        llm_mock = AsyncMock(side_effect=[route_json("react", "简单")])
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            first = await agent.router_node(agent.initial_state("同一个简单问题"))
            second = await agent.router_node(agent.initial_state("同一个简单问题"))
        self.assertEqual(llm_mock.call_count, 1)
        self.assertEqual(first["llm_calls"], 1)
        self.assertEqual(second["llm_calls"], 0)
        self.assertEqual(second["route"]["source"], "llm")
        self.assertEqual(second["mode"], "react")

    async def test_arxiv_id_shortcut_reacts_without_llm(self):
        """捷径：arXiv 号形态的目标直接 react，不调用 LLM（多调一次即 StopIteration）。"""
        llm_mock = AsyncMock(side_effect=[])
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            result = await agent.router_node(agent.initial_state("arxiv:2401.12345"))
        self.assertEqual(llm_mock.call_count, 0)
        self.assertEqual(result["mode"], "react")
        self.assertEqual(result["route"]["source"], "shortcut")
        self.assertNotIn("queries", result)  # router 只决策，检索准备由 react_node 完成

    async def test_doi_shortcut_reacts_without_llm(self):
        llm_mock = AsyncMock(side_effect=[])
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            result = await agent.router_node(agent.initial_state("10.1145/3583780.3615213"))
        self.assertEqual(llm_mock.call_count, 0)
        self.assertEqual(result["mode"], "react")
        self.assertEqual(result["route"]["source"], "shortcut")

    async def test_router_decides_only_react_node_prepares_queries(self):
        """拆分契约：router_node 只决策（不改 queries/plan），react_node 负责检索准备且不计 llm。"""
        llm_mock = AsyncMock(side_effect=[route_json("react", "简单问题")])
        with patch("app.infrastructure.llm.complete", new=llm_mock):
            routed = await agent.router_node(agent.initial_state("什么是RAG"))
        self.assertNotIn("queries", routed)
        self.assertNotIn("plan", routed)
        self.assertEqual(routed["llm_calls"], 1)

        prepared = agent.react_node({**agent.initial_state("什么是RAG"), **routed})
        self.assertEqual(prepared["queries"], ["什么是RAG"])
        self.assertEqual(prepared["plan"], [{"action": "search", "query": "什么是RAG"}])
        self.assertEqual(prepared["plan_version"], 1)
        self.assertNotIn("llm_calls", prepared)  # react_node 不额外计费（无增量即不返回该键）
        react_event = next(e for e in prepared["events"] if e["step"] == "react")
        self.assertIn("跳过计划", react_event["reason"])

    async def test_react_end_to_end_saves_llm_budget(self):
        """A simple research request avoids the planning call, without bypassing evidence."""
        assess = json.dumps({"coverage": "insufficient", "missing": [], "next_action": "stop"})
        search = AsyncMock(return_value={"papers": [], "source_errors": {}})
        managers = self.managers([route_json("react"), assess, assess], search=search)
        mocks = [m.start() for m in managers]
        try:
            simple = await agent.run("分析这类论文的检索增强方法", auto_approve=True, task_id="simple-budget")
            self.assertEqual(mocks[0].call_count, 0)
            planned = await agent.run("分析这类论文的检索增强方法", auto_approve=True, task_id="planned-budget", mode="plan")
        finally:
            for m in reversed(managers):
                m.stop()
        self.assertEqual(simple["status"], "done")
        self.assertEqual(planned["status"], "done")
        self.assertEqual(mocks[0].call_count, 1)
        self.assertTrue(any(e["step"] == "prepare" for e in simple["events"]))
        self.assertTrue(any(e["step"] == "plan" for e in planned["events"]))

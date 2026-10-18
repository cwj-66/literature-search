"""Ordinary chat takes its own branch without tools or paper-evidence judging."""

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent
from app.infrastructure import llm
from app.main import app


def routed(mode):
    return json.dumps({"mode": mode, "rationale": "按用户请求分类"})


class DirectChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                          "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    def no_research(self):
        stack = ExitStack()
        for node in ("plan_node", "react_agent", "tool_node", "claims_node", "judge_node"):
            stack.enter_context(patch.object(agent, node, new=AsyncMock(side_effect=AssertionError(f"must skip {node}"))))
        return stack

    async def test_concept_and_greeting_complete_without_research(self):
        for goal, answer in (("RAG 是什么？", "RAG 会先检索相关材料，再结合材料生成回答。"), ("你好", "你好，有什么可以帮你？")):
            with self.subTest(goal=goal), self.no_research(), \
                 patch.object(llm, "complete", new=AsyncMock(side_effect=[routed("direct"), json.dumps({"answer": answer})])) as complete:
                result = await agent.run(goal)
            self.assertEqual(result["status"], "done")
            self.assertEqual(result["mode"], "direct")
            self.assertEqual(result["answer"], answer)
            self.assertEqual(result["llm_calls"], 2)
            self.assertEqual(result["api_calls"], 0)
            self.assertEqual(result["judge_round"], 0)
            self.assertIsNone(result["review"])
            self.assertEqual(result["evidence_ids"], [])
            self.assertEqual(result["queries"], [])
            self.assertEqual(complete.await_count, 2)
            self.assertEqual([event["step"] for event in result["events"]], ["route", "direct", "stop"])
            await agent.reset_graph()

    async def test_general_concept_with_selected_paper_still_direct(self):
        with self.no_research(), patch.object(llm, "complete", new=AsyncMock(side_effect=[
            routed("direct"), json.dumps({"answer": "向量数据库保存向量并支持相似度检索。"})])) as complete:
            result = await agent.run("向量数据库是什么？", context={"selected_paper_ids": ["arxiv:2401.12345"]})
        self.assertEqual(result["mode"], "direct")
        router_messages = complete.await_args_list[0].args[0]
        self.assertIn("arxiv:2401.12345", router_messages[0]["content"])

    async def test_explicit_paper_questions_cannot_bypass_evidence_flow(self):
        for goal in ("这篇论文的 RAG 是怎么实现的？", "查看当前论文的实验结果", "工作区有哪些论文？", "分析 arxiv:2401.12345 的局限性"):
            with self.subTest(goal=goal), patch.object(llm, "complete", new=AsyncMock(return_value=routed("direct"))):
                result = await agent.router_node(agent.initial_state(goal))
            self.assertEqual(result["mode"], "react")
            self.assertEqual(agent.route_route(result), "react")

    async def test_research_and_complex_tasks_keep_existing_routes(self):
        for goal, mode in (("帮我找十篇 RAG 论文", "react"), ("对比近三年幻觉抑制方法并写综述", "plan")):
            with self.subTest(goal=goal), patch.object(llm, "complete", new=AsyncMock(return_value=routed(mode))):
                result = await agent.router_node(agent.initial_state(goal))
            self.assertEqual(agent.route_route(result), mode)
        with patch.object(llm, "complete", new=AsyncMock(side_effect=AssertionError("identifier shortcut"))):
            result = await agent.router_node(agent.initial_state("arxiv:2401.12345"))
        self.assertEqual(result["mode"], "react")

    async def test_router_failure_remains_conservative(self):
        with patch.object(llm, "complete", new=AsyncMock(side_effect=llm.AnalysisError("unavailable"))):
            result = await agent.router_node(agent.initial_state("一个不明确的请求"))
        self.assertEqual(result["mode"], "plan")

    async def test_direct_failure_does_not_launch_tools_or_invent_answer(self):
        for index, output in enumerate((llm.AnalysisError("provider failed"), "invalid json", '{"answer":"  "}')):
            with self.subTest(output=str(output)), self.no_research(), \
                 patch.object(llm, "complete", new=AsyncMock(side_effect=[routed("direct"), output])):
                result = await agent.run(f"请解释 Embedding，问题编号 {index}")
            self.assertEqual(result["stop_reason"], "普通聊天回复失败")
            self.assertIn("稍后重试", result["answer"])
            self.assertEqual(result["api_calls"], 0)
            self.assertEqual(result["llm_calls"], 2)
            await agent.reset_graph()

    async def test_budget_and_cancellation_prevent_direct_generation(self):
        for overrides in ({"max_llm_calls": 0}, {"cancelled": True}):
            with self.subTest(overrides=overrides), patch.object(llm, "complete", new=AsyncMock()) as complete:
                result = await agent.direct_answer_node({**agent.initial_state("你好"), **overrides})
            self.assertTrue(result["stop_reason"])
            complete.assert_not_awaited()

    async def test_router_cache_separates_research_context(self):
        with patch.object(llm, "complete", new=AsyncMock(return_value=routed("react"))) as complete:
            a = agent.initial_state("解释它的方法", context={"selected_paper_ids": ["arxiv:2401.12345"]})
            b = agent.initial_state("解释它的方法", context={"selected_paper_ids": ["arxiv:2401.54321"]})
            await agent.router_node(a)
            cached = await agent.router_node(a)
            await agent.router_node(b)
        self.assertEqual(complete.await_count, 2)
        self.assertEqual(cached["llm_calls"], 0)

    async def test_api_returns_direct_answer_and_stream_uses_same_branch(self):
        with self.no_research(), patch.object(llm, "complete", new=AsyncMock(side_effect=[
            routed("direct"), '{"answer":"你好！"}', routed("direct"), '{"answer":"RAG 是检索增强生成。"}'])):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/research/tasks", json={"goal": "你好"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["answer"], "你好！")
            self.assertEqual(response.json()["mode"], "direct")
            events = [event async for event in agent.stream_run("RAG是什么", task_id="direct-stream")]
        nodes = [event["node"] for event in events if event["type"] == "node"]
        self.assertEqual(nodes, ["router", "gate_direct", "direct_answer", "gate_done", "finish"])
        self.assertEqual(events[-1]["task"]["answer"], "RAG 是检索增强生成。")

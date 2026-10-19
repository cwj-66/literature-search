"""图级集成：新增 state 字段必须经 StateGraph(AgentState) 完整保留。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import agent
from app.agents.budgets import resolve_budget


def route_json(**extra):
    payload = {"mode": "plan", "rationale": "复合指令", "domains": ["*"], "missing_slots": [],
               "research_intent": "discover", "post_actions": ["analyze"]}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class GraphStateFieldsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_budget_fields_survive_graph_run(self):
        fast = resolve_budget("fast")
        async def fake_complete(messages):
            if "任务路由员" in str(messages[0].get("content", "")):
                return json.dumps({"mode": "direct", "rationale": "按用户请求分类"})
            return json.dumps({"answer": "你好"})
        with patch.object(agent.llm, "complete", new=AsyncMock(side_effect=fake_complete)):
            result = await agent.run("你好", mode="auto", budget="fast")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["budget"], "fast")
        self.assertEqual(result["max_react"], fast.max_react)
        self.assertEqual(result["max_judge_rounds"], fast.max_judge_rounds)

    async def test_post_actions_survive_graph_run(self):
        with patch.object(agent.llm, "complete", new=AsyncMock(return_value=route_json())):
            result = await agent.run("找几篇RAG论文并分析", auto_approve=False)
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["post_actions"], ["analyze"])

"""复合指令路由：plan 模式携带 post_actions（分析/存工作区），驱动 workflow 尾部节点。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import agent
from app.agents.state import TaskRoute, initial_state


def route_json(**extra):
    payload = {"mode": "plan", "rationale": "复合指令", "domains": ["*"], "missing_slots": [],
               "research_intent": "discover", "post_actions": []}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class TaskRouteSchemaTests(unittest.TestCase):
    def test_post_actions_defaults_empty(self):
        route = TaskRoute.model_validate(json.loads(route_json()))
        self.assertEqual(route.post_actions, [])

    def test_post_actions_accepts_known_values(self):
        route = TaskRoute.model_validate(json.loads(route_json(post_actions=["analyze", "save_to_workspace"])))
        self.assertEqual(route.post_actions, ["analyze", "save_to_workspace"])


class RouterPostActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                          "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_router_propagates_post_actions(self):
        decision = route_json(post_actions=["analyze", "save_to_workspace"])
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=decision)):
            updates = await agent.router_node(initial_state("找几篇RAG论文并分析后存进工作区"))
        self.assertEqual(updates["mode"], "plan")
        self.assertEqual(updates["post_actions"], ["analyze", "save_to_workspace"])

    async def test_router_without_post_actions_stays_empty(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=route_json())):
            updates = await agent.router_node(initial_state("梳理RAG研究脉络"))
        self.assertEqual(updates["post_actions"], [])

    async def test_initial_state_has_post_actions(self):
        self.assertEqual(initial_state("goal")["post_actions"], [])


if __name__ == "__main__":
    unittest.main()

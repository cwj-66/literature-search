"""workflow 尾部节点：复合指令 post_actions 驱动逐篇分析与存工作区。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.agents.state import initial_state
from app.agents import workflow


def paper(i, title=None):
    return {"id": f"arxiv:{i}", "title": title or f"Paper {i}", "abstract": "a", "year": 2023}


class PostActionsRouteTests(unittest.TestCase):
    def test_route_prefers_analyze_then_save(self):
        state = {**initial_state("goal"), "post_actions": ["analyze", "save_to_workspace"]}
        self.assertEqual(workflow.post_actions_route(state), "analyze")

    def test_route_save_only(self):
        state = {**initial_state("goal"), "post_actions": ["save_to_workspace"]}
        self.assertEqual(workflow.post_actions_route(state), "save")

    def test_route_no_actions_goes_judge(self):
        state = initial_state("goal")
        self.assertEqual(workflow.post_actions_route(state), "judge")

    def test_after_analyze_route(self):
        state = {**initial_state("goal"), "post_actions": ["analyze", "save_to_workspace"]}
        self.assertEqual(workflow.after_analyze_route(state), "save")
        state["post_actions"] = ["analyze"]
        self.assertEqual(workflow.after_analyze_route(state), "judge")


class AnalyzePapersNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_top_candidates_and_marks_stage(self):
        state = {**initial_state("goal"), "papers": [paper(i) for i in range(1, 8)], "result_limit": 3,
                 "post_actions": ["analyze"]}
        with patch("app.agents.tooling._tool_analyze_paper", new=AsyncMock(return_value=("ok", {"llm_calls": 1}))) as analyze:
            updates = await workflow.analyze_papers_node(state)
        self.assertEqual(analyze.await_args[0][1]["paper_ids"], ["arxiv:1", "arxiv:2", "arxiv:3"])
        self.assertEqual(updates["workflow_stage"], "analyze")
        self.assertEqual(updates["llm_calls"], 1)

    async def test_analyze_without_candidates_skips(self):
        state = {**initial_state("goal"), "papers": [], "post_actions": ["analyze"]}
        updates = await workflow.analyze_papers_node(state)
        self.assertEqual(updates["workflow_stage"], "analyze")
        self.assertNotIn("llm_calls", updates)


class SaveToWorkspaceNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_calls_add_tool_via_catalog(self):
        state = {**initial_state("goal"), "papers": [paper(1), paper(2)], "result_limit": 5,
                 "workspace_id": "ws:1", "post_actions": ["save_to_workspace"]}
        with patch("app.agents.tooling._tool_catalog", new=AsyncMock(return_value=("saved", {}))) as catalog:
            updates = await workflow.save_to_workspace_node(state)
        self.assertEqual(catalog.await_args[0][1], "add_papers_to_workspace")
        self.assertEqual(set(catalog.await_args[0][2]["paper_ids"]), {"arxiv:1", "arxiv:2"})
        self.assertEqual(updates["workflow_stage"], "save")

    async def test_save_requires_workspace(self):
        state = {**initial_state("goal"), "papers": [paper(1)], "post_actions": ["save_to_workspace"]}
        updates = await workflow.save_to_workspace_node(state)
        self.assertIn("缺少工作区", updates["gaps"][0])


if __name__ == "__main__":
    unittest.main()

"""Chat capabilities stay isolated from relationship and graph module tools."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents import agent, tooling
from app.main import app
from app.schemas import PaperOut
from app.services.knowledge_graph import tools as knowledge_tools
from app.services.papers import library, workspaces
from app.services.retrieval import tools as relation_tools
from app.tools import research


CHAT = {"decompose_question", "search_papers", "rerank_papers", "assess_coverage"}
RELATIONS = {"get_paper_details", "find_related_papers", "get_paper_references", "get_paper_citations"}
KNOWLEDGE = {"query_knowledge_graph", "analyze_research_trends"}
LEGACY = {"save_papers_to_workspace", "request_deep_analysis"}


class ToolModuleBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        workspaces.register("boundary", "Boundary")
        self.paper, _ = library.save_paper(PaperOut(id="arxiv:2401.12345", title="Boundary paper", year=2024), "arxiv:2401.12345")
        workspaces.add("boundary", self.paper.local_id)
        self.context = {"workspace_id": "boundary", "scope": "workspace", "accessible_paper_ids": [self.paper.id]}

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    def test_allowlist_cannot_expand_by_domain_operation_or_legacy_context(self):
        self.assertEqual(set(research.CATALOG), CHAT)
        self.assertEqual(set(tooling.TOOL_REGISTRY), CHAT)
        self.assertEqual(set(relation_tools.CATALOG), RELATIONS)
        self.assertEqual(set(knowledge_tools.CATALOG), KNOWLEDGE)
        for domains in ([], ["*"], ["knowledge"], ["workspace"], ["analysis"]):
            for grant in (None, [], [self.paper.id]):
                for kind in ("research", "operation"):
                    state = {"domains": domains, "accessible_paper_ids": grant, "task_kind": kind}
                    self.assertEqual(tooling.allowed_tools(state), CHAT)
                    self.assertEqual({t["name"] for t in tooling.catalog_for(state)}, CHAT)
                    system = agent._react_system(state)
                    for name in RELATIONS | KNOWLEDGE | LEGACY:
                        self.assertNotIn(name, system)

    async def test_chat_api_and_executor_cannot_dispatch_module_or_legacy_tools(self):
        state = agent.initial_state("question", context=self.context)
        with patch.object(relation_tools, "execute", new=AsyncMock()) as relations, \
             patch.object(knowledge_tools, "execute", new=AsyncMock()) as knowledge:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for name in RELATIONS | KNOWLEDGE | LEGACY:
                    with self.subTest(tool=name):
                        with self.assertRaises(ValueError):
                            await research.execute(name, {}, None)
                        result, updates = await tooling._run_business_tool(state, name, {})
                        self.assertIn("error", json.loads(result))
                        self.assertEqual(updates, {})
                        for prefix in ("/api/tools/", "/api/research/tools/"):
                            response = await client.post(prefix + name, json={"arguments": {}, "context": self.context})
                            self.assertEqual(response.status_code, 404, response.text)
            relations.assert_not_awaited()
            knowledge.assert_not_awaited()

    async def test_tool_node_rejects_stale_model_calls_without_side_effects(self):
        state = agent.initial_state("question", context=self.context)
        state["messages"] = [{"role": "assistant", "content": "", "tool_calls": [
            {"id": name, "name": name, "args": {"paper_id": self.paper.id}, "type": "tool_call"}
            for name in sorted(RELATIONS | KNOWLEDGE | LEGACY)
        ]}]
        with patch.object(relation_tools, "execute", new=AsyncMock()) as relations, \
             patch.object(knowledge_tools, "execute", new=AsyncMock()) as knowledge:
            result = await tooling.tool_node(state)
            self.assertEqual(len(result["messages"]), 8)
            self.assertTrue(all("error" in json.loads(m["content"]) for m in result["messages"]))
            self.assertFalse(result.get("operations"))
            relations.assert_not_awaited()
            knowledge.assert_not_awaited()

    async def test_module_endpoints_keep_data_and_scope_checks(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual({t["name"] for t in (await client.get("/api/tools")).json()["chat_tools"]}, CHAT)
            for prefix, names in (("/api/paper-relations/tools", RELATIONS), ("/api/knowledge-graph/tools", KNOWLEDGE)):
                self.assertEqual({t["name"] for t in (await client.get(prefix)).json()["tools"]}, names)
            detail = await client.post("/api/paper-relations/tools/get_paper_details", json={
                "arguments": {"paper_id": self.paper.id}, "context": self.context})
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["paper"]["title"], self.paper.title)
            graph = await client.post("/api/knowledge-graph/tools/query_knowledge_graph", json={"context": self.context})
            self.assertEqual(graph.status_code, 200, graph.text)
            self.assertEqual(graph.json()["total_papers"], 1)
            denied = await client.post("/api/knowledge-graph/tools/query_knowledge_graph", json={
                "arguments": {"paper_ids": [self.paper.id]}, "context": {**self.context, "accessible_paper_ids": []}})
            self.assertEqual(denied.status_code, 400, denied.text)
            for path in ("/api/paper-relations/tools/query_knowledge_graph", "/api/knowledge-graph/tools/get_paper_details"):
                self.assertEqual((await client.post(path, json={})).status_code, 404)

    async def test_router_cannot_enable_old_graph_or_write_workflows(self):
        old_decision = {"mode": "plan", "research_intent": "mixed", "domains": ["knowledge"],
                        "post_actions": ["analyze", "save_to_workspace"]}
        failed_plan = {"status": "failed", "llm_calls": 1, "error": "Stopped before search"}
        with patch.object(agent, "_llm_route", new=AsyncMock(return_value=old_decision)), \
             patch("app.agents.finding.decompose_question", new=AsyncMock(return_value=failed_plan)), \
             patch("app.agents.workflow.prepare_node", new=AsyncMock(side_effect=AssertionError("old workflow"))), \
             patch("app.agents.workflow.analyze_papers_node", new=AsyncMock(side_effect=AssertionError("analysis"))), \
             patch("app.agents.workflow.save_to_workspace_node", new=AsyncMock(side_effect=AssertionError("write"))):
            result = await agent.run("search request", task_id="boundary-route", context=self.context)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["research_intent"], "discover")
        self.assertEqual(result["task_kind"], "research")
        self.assertEqual(result["domains"], ["discovery"])
        self.assertFalse(result.get("operations"))

    async def test_coverage_tool_does_not_schedule_fulltext_reading(self):
        assessment = {"coverage": "partial", "missing": [], "next_action": "read_next", "rationale": "legacy fallback"}
        state = agent.initial_state("find papers", context=self.context)
        with patch.object(tooling, "run_coverage_assessment", new=AsyncMock(return_value=(assessment, 1, True))):
            content, updates = await tooling._run_business_tool(state, "assess_coverage", {})
        self.assertEqual(json.loads(content)["next_action"], "synthesize")
        self.assertEqual(updates["llm_calls"], 1)

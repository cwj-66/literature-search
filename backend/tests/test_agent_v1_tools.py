"""Agent handlers for decompose_question / rerank_papers: state adaptation layer."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import tooling
from app.services.retrieval import ranking
from app.tools import research


PLAN = {
    "queries": ['("text embedding")', '("sentence embeddings") AND ("E5")'],
    "exclusions": [],
    "search_plan": {"core_concepts": ["text embedding"], "must_have_facets": [["text embedding"]]},
    "_llm_used": 1,
}

EMBEDDING_PAPER = {"id": "arxiv:emb1", "title": "Text Embedding Models for Dense Retrieval",
                   "abstract": "text embedding models study", "year": 2024}
IR_PAPER = {"id": "arxiv:ir1", "title": "Web Search Ranking", "abstract": "information retrieval", "year": 2022}


def score_all(papers, query, *, min_candidates=20):
    return [{**p, "rank_score": 9} for p in papers], 1


class AgentV1ToolHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_decompose_handler_writes_state(self):
        state = {"goal": "帮我找5篇有关向量大模型的论文", "messages": [], "queries": [],
                 "search_plan": {}, "llm_calls": 0, "year_from": None, "year_to": None}
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)):
            content, updates = await tooling._tool_decompose_question(state, {"question": state["goal"]})
        report = json.loads(content)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(updates["llm_calls"], 1)
        self.assertEqual(len(updates["queries"]), 2)
        self.assertTrue(all("text embedding" in query or "sentence embeddings" in query for query in updates["queries"]))
        self.assertEqual(updates["search_plan"]["resolved_question"], "帮我找5篇有关向量大模型的论文")
        self.assertEqual(updates["search_plan"]["target_count"], 5)
        self.assertEqual(updates["search_plan"]["core_concepts"], ["text embedding"])

    async def test_decompose_handler_clarification_writes_nothing(self):
        state = {"goal": "那继续找", "messages": [], "queries": [], "search_plan": {}, "llm_calls": 0}
        content, updates = await tooling._tool_decompose_question(state, {"question": "那继续找"})
        report = json.loads(content)
        self.assertTrue(report["needs_clarification"])
        self.assertNotIn("queries", updates)
        self.assertNotIn("search_plan", updates)

    async def test_rerank_handler_updates_recommendation_state(self):
        state = {"goal": "帮我找5篇有关向量大模型的论文", "papers": [EMBEDDING_PAPER, IR_PAPER],
                 "queries": [], "search_plan": {}, "llm_calls": 0, "year_from": None, "year_to": None,
                 "source_errors": {}}
        with patch.object(ranking, "llm_rank", new=AsyncMock(side_effect=score_all)):
            content, updates = await tooling._tool_rerank_papers(
                state, {"resolved_question": "帮我找5篇有关向量大模型的论文"})
        report = json.loads(content)
        self.assertEqual(updates["recommended_ids"], ["arxiv:emb1"])
        self.assertIn("arxiv:emb1", updates["recommendation_reasons"])
        self.assertEqual(report["recommended_ids"], updates["recommended_ids"])
        self.assertEqual(updates["llm_calls"], 1)
        self.assertEqual(updates["papers"][0]["rank_score"], 9)

    async def test_rerank_handler_empty_pool_errors(self):
        state = {"goal": "找论文", "papers": [], "llm_calls": 0, "year_from": None, "year_to": None}
        content, updates = await tooling._tool_rerank_papers(state, {"resolved_question": "找论文"})
        report = json.loads(content)
        self.assertIn("error", report)
        self.assertNotIn("recommended_ids", updates)

    async def test_rerank_handler_shortfall_mentions_source_errors(self):
        state = {"goal": "帮我找5篇有关向量大模型的论文", "papers": [EMBEDDING_PAPER],
                 "queries": [], "search_plan": {}, "llm_calls": 0, "year_from": None, "year_to": None,
                 "source_errors": {"semantic_scholar": "timeout"}}
        with patch.object(ranking, "llm_rank", new=AsyncMock(side_effect=score_all)):
            content, _ = await tooling._tool_rerank_papers(
                state, {"resolved_question": "帮我找5篇有关向量大模型的论文", "target_count": 5})
        report = json.loads(content)
        self.assertFalse(report["enough"])
        self.assertIn("来源", report["shortfall_reason"])

    def test_tools_registered_and_budgeted(self):
        for name in ("decompose_question", "rerank_papers"):
            self.assertIn(name, research.CATALOG)
            self.assertEqual(research.CATALOG[name]["module"], "discovery")
            self.assertIn(name, tooling.TOOL_REGISTRY)
        self.assertIs(tooling.TOOL_REGISTRY["decompose_question"], tooling._tool_decompose_question)
        self.assertIs(tooling.TOOL_REGISTRY["rerank_papers"], tooling._tool_rerank_papers)
        self.assertIn("decompose_question", tooling.LLM_TOOLS)
        self.assertIn("rerank_papers", tooling.LLM_TOOLS)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

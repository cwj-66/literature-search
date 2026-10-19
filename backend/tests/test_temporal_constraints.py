"""Temporal qualifiers (早期/最新) are ordering constraints, not topical facets."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import discovery
from app.infrastructure import llm
from app.services.retrieval import ranking
from app.tools import decompose_question as decompose_tool
from app.tools import rerank_papers as rerank_tool
from finding_fixtures import PLAN, dumps, selection


RAG_PAPER_OLD = {"id": "arxiv:rag2020", "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP",
                 "abstract": "we propose retrieval-augmented generation, combining a pre-trained language model with retrieval.", "year": 2020}
RAG_PAPER_NEW = {"id": "arxiv:rag2024", "title": "Advanced RAG Pipelines in Production",
                 "abstract": "modern retrieval-augmented generation pipelines with language models.", "year": 2024}


def score_all(papers, query, *, min_candidates=20):
    return [{**paper, "rank_score": 9} for paper in papers], 1


class TemporalQualifierTests(unittest.IsolatedAsyncioTestCase):
    def test_temporal_of_detects_direction(self):
        self.assertEqual(discovery.temporal_of("帮我找三个早期研究RAG的论文"), "early")
        self.assertEqual(discovery.temporal_of("最早的RAG论文"), "early")
        self.assertEqual(discovery.temporal_of("最新的RAG论文"), "recent")
        self.assertIsNone(discovery.temporal_of("帮我找三个RAG应用的论文"))

    def test_strip_temporal_removes_and_groups(self):
        query = '(\"retrieval-augmented generation\") AND (\"early\" OR \"seminal\" OR \"original\")'
        self.assertEqual(discovery.strip_temporal(query), '(\"retrieval-augmented generation\")')

    def test_strip_temporal_plain_text(self):
        stripped = discovery.strip_temporal("帮我找三个早期研究RAG的论文")
        self.assertNotIn("早期", stripped)
        self.assertIn("RAG", stripped)

    async def test_decompose_marks_temporal_and_keeps_queries_clean(self):
        plan = {**PLAN, "queries": ['("retrieval-augmented generation" OR "retrieval augmented generation")'],
                "constraints": {"temporal": "early"}}
        with patch.object(llm, "complete", new=AsyncMock(return_value=dumps(plan))) as planner:
            result = await decompose_tool.decompose_question("帮我找三个早期研究RAG的论文")
        self.assertEqual(result["constraints"]["temporal"], "early")
        self.assertTrue(all("early" not in q.lower() and "seminal" not in q.lower() for q in result["queries"]))
        self.assertIn("retrieval-augmented generation", " ".join(result["queries"]).lower())
        self.assertIn("早期", json.loads(planner.await_args.args[0][1]["content"])["question"])

    async def test_rerank_temporal_group_not_a_hard_facet(self):
        groups = [["RAG", "retrieval-augmented generation"], ["early", "seminal", "original"]]
        constraints = {"year_from": None, "year_to": None, "temporal": "early"}
        with patch.object(llm, "complete", new=AsyncMock(return_value=dumps(selection(2, (0, 1))))):
            result = await rerank_tool.rerank_papers(
                "帮我找三个早期研究RAG的论文", [RAG_PAPER_NEW, RAG_PAPER_OLD],
                target_count=3, constraints=constraints, groups=groups)
        self.assertEqual(result["recommended_ids"], ["arxiv:rag2020", "arxiv:rag2024"])

    async def test_rerank_recent_orders_newest_first(self):
        constraints = {"temporal": "recent"}
        groups = [["RAG", "retrieval-augmented generation"]]
        with patch.object(llm, "complete", new=AsyncMock(return_value=dumps(selection(2, (0, 1))))):
            result = await rerank_tool.rerank_papers(
                "帮我找最新的RAG论文", [RAG_PAPER_OLD, RAG_PAPER_NEW],
                target_count=3, constraints=constraints, groups=groups)
        self.assertEqual(result["recommended_ids"], ["arxiv:rag2024", "arxiv:rag2020"])

    def test_select_bibliography_ignores_temporal_facets(self):
        state = {"goal": "帮我找三个早期研究RAG的论文",
                 "search_plan": {"core_concepts": ["Retrieval-Augmented Generation"],
                                 "must_have_facets": [["Retrieval-Augmented Generation", "RAG"],
                                                      ["early", "seminal", "original", "早期"]],
                                 "exclusions": [], "likely_synonyms": []},
                 "year_from": None, "year_to": None, "result_limit": 3,
                 "queries": ['("retrieval-augmented generation")'],
                 "query_index": 0, "workflow_local_ids": [],
                 "papers": [dict(RAG_PAPER_OLD), dict(RAG_PAPER_NEW)]}
        ids, reasons, matched = discovery.select_bibliography(state)
        self.assertEqual(ids, ["arxiv:rag2020", "arxiv:rag2024"])
        self.assertEqual(matched, 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

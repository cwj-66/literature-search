"""证据池/候选池上限、数量词与排序词解析的针对性测试。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import agent
from app.agents.state import (
    MAX_CANDIDATE_PAPERS, MAX_EVIDENCE_ITEMS, add_evidence, initial_state,
)
from app.agents.tooling import _tool_search_papers
from app.schemas import PaperOut


def paper(n):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", year=2000 + n % 20,
                    sources=["arxiv"]).model_dump()


class EvidencePoolCapTests(unittest.TestCase):
    def test_evidence_pool_drops_oldest_beyond_cap(self):
        state = initial_state("goal")
        items = [{"paper_id": f"p:{i}", "quote": f"quote {i:03d} xxxxx", "tool": "t"} for i in range(MAX_EVIDENCE_ITEMS + 10)]
        updates, added = add_evidence(state, items)
        self.assertEqual(len(updates["evidence_items"]), MAX_EVIDENCE_ITEMS)
        self.assertEqual(updates["evidence_ids"][0], "E0011")
        self.assertNotIn("E0001", updates["evidence_items"])
        self.assertEqual(len(added), MAX_EVIDENCE_ITEMS + 10)

    def test_evidence_pool_under_cap_untouched(self):
        state = initial_state("goal")
        items = [{"paper_id": f"p:{i}", "quote": f"quote {i:03d} xxxxx", "tool": "t"} for i in range(5)]
        updates, _ = add_evidence(state, items)
        self.assertEqual(len(updates["evidence_items"]), 5)


class ResultHintTests(unittest.TestCase):
    def test_cn_quantity(self):
        count, order, query = agent._result_hint("给我三篇关于RAG幻觉的论文")
        self.assertEqual(count, 3)
        self.assertIsNone(order)
        self.assertNotIn("三篇", query)

    def test_digit_quantity_with_earliest(self):
        count, order, query = agent._result_hint("找5篇最早的Transformer论文")
        self.assertEqual(count, 5)
        self.assertEqual(order, "asc")
        self.assertNotIn("5篇", query)

    def test_no_hint(self):
        count, order, _ = agent._result_hint("如何降低RAG幻觉")
        self.assertIsNone(count)
        self.assertIsNone(order)


class CandidatePoolCapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_candidate_pool_drops_oldest_beyond_cap(self):
        state = initial_state("goal")
        state["papers"] = [paper(i) for i in range(1, MAX_CANDIDATE_PAPERS + 6)]
        search = AsyncMock(return_value={"papers": [], "source_errors": {}, "source_retry_after": {}})
        with patch("app.services.retrieval.search.search_papers", new=search):
            _, updates = await _tool_search_papers(state, {"query": "rag"})
        papers = updates["papers"]
        self.assertEqual(len(papers), MAX_CANDIDATE_PAPERS)
        self.assertEqual(papers[0]["id"], "arxiv:6")

    async def test_sort_order_asc_maps_to_date_asc(self):
        state = initial_state("goal")
        state["sort_order"] = "asc"
        search = AsyncMock(return_value={"papers": [paper(1)], "source_errors": {}, "source_retry_after": {}})
        with patch("app.services.retrieval.search.search_papers", new=search):
            await _tool_search_papers(state, {"query": "rag", "sort": "date"})
        self.assertEqual(search.await_args[0][3], "date_asc")

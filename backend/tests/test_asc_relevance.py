"""date_asc 升序检索的相关性过滤、排序词剥离与入池过滤测试。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents import agent
from app.agents.state import initial_state
from app.agents.tooling import _tool_search_papers
from app.schemas import PaperOut
from app.services.retrieval import search


def paper(title, year, abstract=None):
    return PaperOut(id=f"s2:{abs(hash(title)) % 100000}", title=title, year=year,
                    abstract=abstract, sources=["semantic_scholar"]).model_dump()


class ResultHintStripTests(unittest.TestCase):
    def test_strips_sort_words(self):
        _, order, query = agent._result_hint("找5篇最早的Transformer论文")
        self.assertEqual(order, "asc")
        self.assertNotIn("最早", query)
        self.assertNotIn("5篇", query)

    def test_earliest_without_count(self):
        count, order, query = agent._result_hint("最早的GAN论文是哪篇")
        self.assertIsNone(count)
        self.assertEqual(order, "asc")
        self.assertNotIn("最早", query)

    def test_latest_stripped_keeps_default_order(self):
        count, order, query = agent._result_hint("最新的Transformer综述")
        self.assertIsNone(count)
        self.assertIsNone(order)
        self.assertNotIn("最新", query)


class OldestRelevantTests(unittest.TestCase):
    def test_drops_irrelevant_and_none_year_last(self):
        papers = [
            paper("watershed ecology survey", 1990, "river basin management"),
            paper("old gan survey", 2014, "generative adversarial networks"),
            paper("gan training tricks", None, "generative adversarial networks"),
            paper("gan evaluation", 2020, "metrics for generative adversarial networks"),
        ]
        picked = search.oldest_relevant(papers, ["gan"], 10)
        self.assertEqual([p["year"] for p in picked], [2014, 2020, None])

    def test_all_irrelevant_falls_back_to_window(self):
        papers = [paper("ecology only", 1990, "rivers"), paper("ecology two", 1980, "forests")]
        picked = search.oldest_relevant(papers, ["gan"], 10)
        self.assertEqual(len(picked), 2)


class MergePapersDateAscTests(unittest.TestCase):
    def test_none_year_sorted_last(self):
        groups = [[PaperOut(id="s2:a", title="t", year=None), PaperOut(id="s2:b", title="t", year=2010)]]
        merged = search.merge_papers(groups, "date_asc")
        self.assertEqual([p.year for p in merged], [2010, None])


class SemanticScholarDateAscTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_and_orders_oldest_first(self):
        items = [
            {"paperId": "p1", "title": "Old GAN survey", "abstract": "generative adversarial networks", "year": 2014},
            {"paperId": "p2", "title": "Watershed ecology", "abstract": "river basin", "year": 1990},
            {"paperId": "p3", "title": "GAN training tricks", "abstract": "generative adversarial networks", "year": 2017},
        ]
        response = MagicMock()
        response.json.return_value = {"data": items, "total": 3, "token": None}
        with patch("app.services.retrieval.search.request", new=AsyncMock(return_value=response)):
            papers, _ = await search.search_semantic_scholar(None, "gan", 0, 3, None, None, "date_asc")
        self.assertEqual([p.title for p in papers], ["Old GAN survey", "GAN training tricks"])


class MergeFilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_date_asc_drops_zero_overlap_candidates(self):
        state = initial_state("goal")
        state["sort_order"] = "asc"
        raw = {"papers": [
            paper("GAN training tricks", 2017, "generative adversarial networks"),
            paper("Watershed ecology survey", 1990, "river basin management"),
        ], "source_errors": {}, "source_retry_after": {}}
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=raw)):
            _, updates = await _tool_search_papers(state, {"query": "gan", "sort": "date"})
        titles = [p["title"] for p in updates["papers"]]
        self.assertIn("GAN training tricks", titles)
        self.assertNotIn("Watershed ecology survey", titles)

    async def test_relevance_search_untouched_by_filter(self):
        state = initial_state("goal")
        raw = {"papers": [paper("Watershed ecology survey", 1990, "river basin management")],
               "source_errors": {}, "source_retry_after": {}}
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value=raw)):
            _, updates = await _tool_search_papers(state, {"query": "gan"})
        self.assertEqual(len(updates["papers"]), 1)

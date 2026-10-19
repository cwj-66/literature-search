import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import agent, discovery, workflow
from app.agents.state import initial_state, public_state
from app.schemas import PaperOut


PLAN = {"queries": ['("RAG") AND ("application")', '("retrieval-augmented generation") AND ("applied")'],
        "search_plan": {"core_concepts": ["RAG"], "must_have_facets": [["RAG"], ["application"]]}, "_llm_used": 1}


def paper(i, relevant=True):
    return PaperOut(id=f"arxiv:2401.{i:05d}", title=f"RAG application in education {i}" if relevant else f"Unrelated biology {i}",
                    abstract="Retrieval-augmented generation supports educational question answering." if relevant else "Protein structure and cell growth.",
                    year=2024, authors=["Alice"], sources=["arxiv"])


class DiscoveryConstraintsTests(unittest.TestCase):
    def test_vector_model_topic_cannot_be_satisfied_by_generic_ir_results(self):
        state = {**initial_state('帮我找5篇有关向量大模型的论文'),
                 'search_plan': {'must_have_facets': [['IR','search','text embedding']]},
                 'papers': [{'id':'bad','title':'A query suggestion workflow for life science IR-systems.'},
                            {'id':'good','title':'BGE: General Text Embeddings for Semantic Search'}]}
        self.assertEqual(discovery.select_bibliography(state)[0], ['good'])

    def test_core_topic_is_required_even_if_plan_has_overly_generic_facets(self):
        state = {**initial_state('find papers about graph neural networks'),
                 'search_plan': {'core_concepts':['graph neural network'], 'must_have_facets':[['search','IR']]},
                 'papers': [{'id':'bad','title':'Query suggestion for IR systems'},
                            {'id':'good','title':'Graph neural network search'}]}
        self.assertEqual(discovery.select_bibliography(state)[0], ['good'])

    def test_quantity_variants(self):
        for goal, count in [("帮我找三个有关RAG的应用的论文", 3), ("找 3 个关于RAG的论文", 3),
                            ("推荐三份医疗RAG文献", 3), ("找三篇RAG", 3), ("找二十一篇论文", 21),
                            ("top 5 RAG application papers", 5), ("find 3 RAG papers", 3)]:
            with self.subTest(goal=goal):
                self.assertEqual(agent._result_hint(goal)[0], count)
                self.assertIsNone(initial_state(goal)["result_limit"])  # The finding planner supplies the count.
        self.assertIsNone(discovery.requested_count("找2023年的RAG论文"))

    def test_detail_and_review_requests_keep_research_path(self):
        self.assertTrue(discovery.bibliography_goal("找三篇RAG应用论文"))
        for goal in ["搜索论文并分析实验", "找三篇论文并对比", "帮我综述RAG论文", "analyze RAG papers"]:
            self.assertFalse(discovery.bibliography_goal(goal))

    def test_required_facets_survive_query_refinement(self):
        query = discovery.protect_query('("RAG")', "RAG医疗应用", PLAN["search_plan"])
        self.assertIn("application", query)
        self.assertIn("medical", query)
        self.assertIn("retrieval-augmented generation", query)

    def test_rag_gene_deficiency_is_not_a_generation_application(self):
        state = {**initial_state("找三篇RAG应用论文"), "search_plan": PLAN["search_plan"],
                 "papers": [{"id": "gene", "title": "RAG deficiency clinical applications", "abstract": "RAG mutations cause immune disease", "year": 2024},
                            paper(1).model_dump()]}
        ids, _, _ = discovery.select_bibliography(state)
        self.assertEqual(ids, [paper(1).id])

    def test_long_queries_reduce_synonyms_without_dropping_hard_groups(self):
        from app.services.retrieval.search import parse_view_groups
        query = " AND ".join('(' + ' OR '.join(f'"concept {group} synonym {i}"' for i in range(5)) + ')' for group in range(4))
        compact = discovery.protect_query(query, "find papers", {"must_have_facets": [[f"concept {group} synonym 0"] for group in range(4)]})
        self.assertLessEqual(len(compact), 300)
        self.assertEqual(len(parse_view_groups(compact)), 4)

    def test_last_model_allowance_still_returns_existing_bibliography(self):
        state = {**initial_state("找三篇RAG应用论文"), "output_mode": "bibliography", "papers": [paper(i).model_dump() for i in range(3)], "llm_calls": 24}
        result = agent.finish_node(state)
        self.assertEqual(len(result["recommended_ids"]), 3)
        self.assertNotIn("未经核验", result["answer"])

    def test_running_pool_is_not_recommended_and_final_sets_are_disjoint(self):
        state = {**initial_state("找3篇RAG应用论文"), "papers": [paper(i).model_dump() for i in range(24)]}
        self.assertEqual(public_state(state)["papers"], [])
        state["recommended_ids"] = [paper(i).id for i in [8, 10, 12]]
        result = public_state(state)
        self.assertEqual(len(result["recommended"]), 3)
        self.assertEqual(len(result["candidates"]), 21)
        self.assertEqual(result["candidate_count"], 24)
        self.assertEqual(workflow._post_paper_ids(state), state["recommended_ids"])


class BibliographicGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "discovery.sqlite3"), "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def run_discovery(self, mode, papers, errors=None):
        search = AsyncMock(return_value={"papers": papers, "source_errors": errors or {}})
        model = AsyncMock(side_effect=AssertionError("discovery must not extract claims or judge scientific details"))
        read = AsyncMock(side_effect=AssertionError("discovery must not read PDFs"))
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)), \
             patch("app.services.retrieval.search.search_papers", new=search), \
             patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=lambda p, q: (p, 0))), \
             patch("app.infrastructure.llm.complete", new=model), \
             patch("app.agents.workflow._read", new=read):
            result = await agent.run("帮我找三个有关RAG的应用的论文", mode=mode, auto_approve=True, context={"accessible_paper_ids": []})
        read.assert_not_awaited()
        model.assert_not_awaited()
        return result, search

    async def test_both_plan_and_react_return_three_of_twenty_four_and_stop(self):
        for mode in ["plan", "react"]:
            with self.subTest(mode=mode):
                result, search = await self.run_discovery(mode, [paper(i, relevant=i >= 5) for i in range(24)])
                self.assertEqual(result["status"], "done")
                self.assertEqual(result["result_limit"], 3)
                self.assertEqual(len(result["recommended"]), 3)
                self.assertEqual(len(result["candidates"]), 21)
                self.assertTrue(all("RAG application" in p["title"] for p in result["recommended"]))
                self.assertEqual(result["reading_status"], {})
                self.assertFalse({"read", "quotes", "assess", "judge"} & {e["step"] for e in result["events"]})
                self.assertNotIn("不能确认", result["answer"])
                self.assertNotIn("未经核验", result["answer"])
                self.assertLessEqual(search.await_count, 1)
                self.assertEqual(result["search_count"], 1)

    async def test_partial_source_failure_keeps_three_valid_recommendations(self):
        result, _ = await self.run_discovery("react", [paper(i) for i in range(3)], {"arxiv": "HTTP 429 来源限流"})
        self.assertEqual(len(result["recommended"]), 3)
        self.assertIn("arxiv", result["source_errors"])

    async def test_never_fills_three_with_unrelated_candidates(self):
        result, search = await self.run_discovery("react", [paper(1), *[paper(i, False) for i in range(2, 24)]])
        self.assertEqual(len(result["recommended"]), 1)
        self.assertIn("未补入无关候选", result["answer"])
        self.assertLessEqual(search.await_count, 3)
        self.assertTrue(all("RAG" in call.args[0] or "retrieval-augmented generation" in call.args[0] for call in search.await_args_list))

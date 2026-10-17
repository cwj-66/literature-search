import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import agent, llm
from app.fulltext import PreparedText
from app.schemas import PaperOut


def paper(n, **values):
    return PaperOut(id=f"arxiv:{n}", arxiv_id=str(n), title=f"Paper {n}", sources=["arxiv"], **values)


class AgentBranchTests(unittest.TestCase):
    def test_coverage_gap_expands_instead_of_stopping(self):
        state = agent.initial_state("降低RAG幻觉")
        state.update({"papers": [p.model_dump() for p in (paper(1), paper(2))], "queries": ["q1", "q2"], "query_index": 0})
        self.assertEqual(agent.route(state), "expand")

    def test_rate_limit_without_candidates_stops(self):
        state = agent.initial_state("降低RAG幻觉")
        state["source_errors"] = {"arxiv": "arXiv 上游返回 429（限流），约 60 秒后可重试"}
        self.assertEqual(agent.route(state), "stop")

    def test_budget_stop_and_unread_read(self):
        state = agent.initial_state("降低RAG幻觉")
        state["api_calls"] = 8
        self.assertEqual(agent.route(state), "stop")
        state = agent.initial_state("降低RAG幻觉")
        state.update({"papers": [paper(1).model_dump()], "need_evidence": True})
        self.assertEqual(agent.route(state), "read")


class AgentGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "memory",
        })
        self.env.start()
        agent._GRAPH = None
        agent._CHECKPOINTER = None

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_graph_uses_existing_search_tool_and_stops_on_limit(self):
        empty = {
            "papers": [], "source_errors": {"arxiv": "arXiv 上游返回 429（限流），约 60 秒后可重试"},
            "source_retry_after": {"arxiv": 60},
        }
        with patch("app.attribution.decompose", new=AsyncMock(return_value={"queries": ["rag hallucination"], "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"})), \
             patch("app.search.search_papers", new=AsyncMock(return_value=empty)):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True)
        self.assertEqual(result["status"], "done")
        self.assertTrue(result["stop_reason"])
        self.assertEqual(result["api_calls"], 1)
        self.assertTrue(any(event["step"] == "search" for event in result["events"]))
        self.assertTrue(any(event["step"] == "stop" for event in result["events"]))

    async def test_read_keeps_going_when_analysis_unparseable(self):
        state = agent.initial_state("降低RAG幻觉")
        state["papers"] = [paper(1, abstract="Retrieval reduces hallucination.").model_dump()]
        prepared = PreparedText("Retrieval reduces hallucination with citations.", "html", "html", None, None, {"truncated": False})
        with patch("app.library.save_paper", return_value=(paper(1, abstract="Retrieval reduces hallucination."), False)), \
             patch("app.fulltext.prepare", new=AsyncMock(return_value=prepared)), \
             patch("app.hybrid.index_prepared"), \
             patch("app.llm.analyze", new=AsyncMock(side_effect=llm.AnalysisError("模型返回的结果无法解析"))):
            result = await agent.read_node(state)
        self.assertEqual(result["read_ids"], ["arxiv:1"])
        self.assertTrue(any("分析失败" in gap for gap in result["gaps"]))
        self.assertEqual(result["events"][-1]["reason"], "精读解析失败，已保留全文索引")


class AgentSqliteCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "RESEARCH_CHECKPOINT": "sqlite",
        }, clear=False)
        self.env.start()
        await agent.reset_graph()

    async def asyncTearDown(self):
        await agent.reset_graph()
        self.env.stop()
        self.temp.cleanup()

    async def test_async_sqlite_checkpointer_records_thread(self):
        empty = {
            "papers": [], "source_errors": {"arxiv": "arXiv 上游返回 429（限流），约 60 秒后可重试"},
            "source_retry_after": {"arxiv": 60},
        }
        with patch("app.attribution.decompose", new=AsyncMock(return_value={"queries": ["rag hallucination"], "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"})), \
             patch("app.search.search_papers", new=AsyncMock(return_value=empty)):
            result = await agent.run("如何降低RAG幻觉", auto_approve=True)
        self.assertEqual(result["status"], "done")
        self.assertTrue(agent.checkpoint_path().exists())
        self.assertGreater(agent.checkpoint_path().stat().st_size, 0)

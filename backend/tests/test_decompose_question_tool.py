"""decompose_question tool: resolve goals, counts, year constraints and queries."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.infrastructure import llm
from app.tools import decompose_question as tool


PLAN = {
    "queries": ['("retrieval-augmented generation") AND ("application")'],
    "exclusions": [],
    "search_plan": {"core_concepts": ["retrieval-augmented generation"]},
    "_llm_used": 1,
}


class DecomposeQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_count_and_queries(self):
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)) as planner:
            result = await tool.decompose_question("帮我找有关 RAG 应用的论文")
        planner.assert_awaited_once_with("帮我找有关 RAG 应用的论文")
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["needs_clarification"])
        self.assertEqual(result["target_count"], 5)
        self.assertEqual(result["queries"], PLAN["queries"])
        self.assertEqual(result["core_topics"], ["retrieval-augmented generation"])
        self.assertEqual(result["llm_calls"], 1)

    async def test_explicit_count_and_years(self):
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)):
            result = await tool.decompose_question("帮我找3篇2023到2025年RAG应用的论文")
        self.assertEqual(result["target_count"], 3)
        self.assertEqual(result["constraints"], {"year_from": 2023, "year_to": 2025})

    async def test_single_year_becomes_range(self):
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)):
            result = await tool.decompose_question("帮我找2024年的RAG论文")
        self.assertEqual(result["constraints"], {"year_from": 2024, "year_to": 2024})

    async def test_relative_years_not_invented(self):
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)):
            result = await tool.decompose_question("帮我找近三年RAG应用的论文")
        self.assertEqual(result["constraints"], {"year_from": None, "year_to": None})

    async def test_continuation_without_context_needs_clarification(self):
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock()) as planner, \
             patch.object(llm, "complete", new=AsyncMock()) as complete:
            result = await tool.decompose_question("那继续找")
        planner.assert_not_awaited()
        complete.assert_not_awaited()
        self.assertEqual(result["status"], "needs_clarification")
        self.assertTrue(result["needs_clarification"])
        self.assertTrue(result["clarification_question"])

    async def test_continuation_with_context_resolves_topic(self):
        resolved = json.dumps({"resolved_question": "继续找5篇文本向量模型论文", "ambiguous": False})
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)) as planner, \
             patch.object(llm, "complete", new=AsyncMock(return_value=resolved)):
            result = await tool.decompose_question("那继续找", context=["用户：帮我找5篇有关向量大模型的论文"])
        planner.assert_awaited_once_with("继续找5篇文本向量模型论文")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["target_count"], 5)
        self.assertEqual(result["llm_calls"], 2)

    async def test_continuation_with_useless_context_needs_clarification(self):
        ambiguous = json.dumps({"resolved_question": "", "ambiguous": True})
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock()) as planner, \
             patch.object(llm, "complete", new=AsyncMock(return_value=ambiguous)):
            result = await tool.decompose_question("那继续找", context=["用户：你好"])
        planner.assert_not_awaited()
        self.assertTrue(result["needs_clarification"])

    async def test_long_continuation_with_context_resolves_topic(self):
        question = "那继续找，这次只要2023年以后关于医疗场景的论文，请多找一些"
        resolved = json.dumps({"resolved_question": "继续找5篇2023年以后医疗场景的向量模型论文", "ambiguous": False})
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)) as planner, \
             patch.object(llm, "complete", new=AsyncMock(return_value=resolved)) as complete:
            result = await tool.decompose_question(question, context=["用户：帮我找5篇有关向量大模型的论文"])
        complete.assert_awaited_once()
        planner.assert_awaited_once_with("继续找5篇2023年以后医疗场景的向量模型论文")
        self.assertEqual(result["status"], "completed")

    async def test_re_prefixed_new_question_is_not_continuation(self):
        with patch("app.services.analysis.attribution.plan_search", new=AsyncMock(return_value=PLAN)) as planner, \
             patch.object(llm, "complete", new=AsyncMock()) as complete:
            result = await tool.decompose_question("再生医学的最新进展综述")
        complete.assert_not_awaited()
        planner.assert_awaited_once_with("再生医学的最新进展综述")
        self.assertEqual(result["status"], "completed")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

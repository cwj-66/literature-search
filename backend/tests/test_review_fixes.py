"""评审修复回归：plan 调用计数、llm_rank 纯函数、prompt 注入标注。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.infrastructure import llm
from app.services.analysis import attribution
from app.services.retrieval import ranking
from app.agents import prompts

PLAN_PAYLOAD = {
    "question_type": "method_application",
    "core_concepts": [], "must_have_facets": [], "optional_facets": [],
    "likely_synonyms": [], "boundaries": [],
    "query_views": [{"name": "v", "concept_groups": [["a"], ["b"]]}],
    "exclusions": [], "rationale": "r",
}


class PlanLlmAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_reports_actual_calls(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=json.dumps(PLAN_PAYLOAD))):
            result = await attribution.plan_search("q")
        self.assertEqual(result["_llm_used"], 1)

    async def test_retry_reports_two_calls(self):
        good = json.dumps(PLAN_PAYLOAD)
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=["garbage", good])):
            result = await attribution.plan_search("q")
        self.assertEqual(result["_llm_used"], 2)

    async def test_total_failure_reports_all_attempts(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("fail"))):
            result = await attribution.plan_search("某个问题")
        # plan 1 次失败 + decompose 也失败（0 次成功调用），共 1 次。
        self.assertEqual(result["_llm_used"], 1)
        self.assertEqual(result["queries"], ["某个问题"])

    async def test_decompose_fallback_counts_its_call(self):
        side_effect = [llm.AnalysisError("fail"), json.dumps({"queries": ["q1"], "exclusions": [], "rationale": "改写"})]
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=side_effect)):
            result = await attribution.plan_search("某个问题")
        self.assertEqual(result["_llm_used"], 2)  # plan 1 次 + decompose 成功 1 次
        self.assertEqual(result["queries"], ["q1"])


class LlmRankPurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_list_not_mutated(self):
        papers = [{"id": f"p:{i}", "title": f"Paper {i}", "abstract": "a"} for i in range(25)]
        snapshot = [dict(paper) for paper in papers]
        scores = json.dumps({"scores": [[i, 25 - i] for i in range(25)]})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=scores)):
            ranked, _ = await ranking.llm_rank(papers, "q")
        self.assertEqual(papers, snapshot)
        self.assertNotIn("rank_score", papers[0])
        self.assertIn("rank_score", ranked[0])


class PromptInjectionNoteTests(unittest.TestCase):
    def test_rank_system_marks_untrusted_data(self):
        self.assertIn("数据", prompts_ranking_note())


def prompts_ranking_note():
    from app.services.retrieval.ranking import RANK_SYSTEM
    return RANK_SYSTEM


if __name__ == "__main__":
    unittest.main()

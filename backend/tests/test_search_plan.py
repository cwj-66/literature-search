"""SearchPlan 查询计划：结构化检索计划 + 确定性编译 + 两级修复回退。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.infrastructure import llm
from app.services.analysis import attribution

PLAN_PAYLOAD = {
    "question_type": "method_application",
    "core_concepts": ["contrastive learning", "medical image segmentation"],
    "must_have_facets": [["contrastive learning"], ["medical image segmentation"]],
    "optional_facets": [["self-supervised"]],
    "likely_synonyms": [["contrastive", "instance discrimination"]],
    "boundaries": [],
    "query_views": [
        {"name": "direct", "concept_groups": [["contrastive learning"], ["medical image segmentation"]]},
        {"name": "synonym", "concept_groups": [["instance discrimination"], ["medical image segmentation"]]},
    ],
    "exclusions": [],
    "rationale": "按方法与应用视角拆分",
}


class SearchPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_search_compiles_views_to_queries(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=json.dumps(PLAN_PAYLOAD, ensure_ascii=False))):
            result = await attribution.plan_search("对比学习如何做医学影像分割")
        self.assertEqual(result["queries"], [
            '("contrastive learning") AND ("medical image segmentation")',
            '("instance discrimination") AND ("medical image segmentation")',
        ])
        self.assertEqual(result["rationale"], "按方法与应用视角拆分")
        self.assertIn("search_plan", result)
        self.assertEqual(result["search_plan"]["question_type"], "method_application")

    async def test_plan_search_falls_back_to_original_question_on_llm_failure(self):
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败"))):
            result = await attribution.plan_search("某个研究问题")
        self.assertEqual(result["queries"], ["某个研究问题"])
        self.assertEqual(result["exclusions"], [])
        self.assertTrue(result["rationale"].startswith("模型规划不可用"))

    async def test_plan_search_retries_once_on_invalid_json_then_succeeds(self):
        bad = "not json at all"
        good = json.dumps(PLAN_PAYLOAD, ensure_ascii=False)
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=[bad, good])) as complete:
            result = await attribution.plan_search("对比学习如何做医学影像分割")
        self.assertEqual(complete.await_count, 2)
        self.assertEqual(len(result["queries"]), 2)

    async def test_plan_search_drops_duplicate_views_by_jaccard(self):
        payload = dict(PLAN_PAYLOAD)
        payload["query_views"] = [
            {"name": "direct", "concept_groups": [["contrastive learning"], ["segmentation"]]},
            {"name": "dup", "concept_groups": [["segmentation"], ["contrastive learning"]]},
            {"name": "fresh", "concept_groups": [["self-supervised"], ["segmentation"]]},
        ]
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=json.dumps(payload, ensure_ascii=False))):
            result = await attribution.plan_search("q")
        self.assertEqual(len(result["queries"]), 2)
        self.assertIn("self-supervised", result["queries"][1])

    async def test_plan_search_caps_queries_at_four(self):
        payload = dict(PLAN_PAYLOAD)
        payload["query_views"] = [{"name": f"v{i}", "concept_groups": [[f"concept{i}"], ["base"]]} for i in range(6)]
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=json.dumps(payload, ensure_ascii=False))):
            result = await attribution.plan_search("q")
        self.assertEqual(len(result["queries"]), 4)


if __name__ == "__main__":
    unittest.main()

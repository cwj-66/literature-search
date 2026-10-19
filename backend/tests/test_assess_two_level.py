"""评估两级判定：规则先行（标题漂移不调 LLM），LLM 评估回灌 feedback。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.agents import tooling
from app.agents.state import initial_state


class EnvCase:
    """环境隔离：临时库 + 内存检查点，防止命中开发库缓存。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()


def paper(i, title):
    return {"id": f"p:{i}", "title": title, "abstract": "", "year": 2023}


def drifting_state():
    state = initial_state("goal")
    state["papers"] = [paper(i, f"Unrelated biology {i}") for i in range(6)]
    state["queries"] = ["contrastive segmentation model"]
    return state


class RuleFirstAssessTests(EnvCase, unittest.IsolatedAsyncioTestCase):
    async def test_drift_skips_llm_and_rewrites_query(self):
        state = drifting_state()
        with patch("app.infrastructure.llm.complete", new=AsyncMock()) as complete:
            assessment, llm_delta, fallback = await tooling.run_coverage_assessment(state)
        complete.assert_not_awaited()
        self.assertEqual(llm_delta, 0)
        self.assertFalse(fallback)
        self.assertEqual(assessment["next_action"], "search_more")
        self.assertEqual(assessment["new_query"], "contrastive segmentation")

    async def test_no_drift_uses_llm(self):
        state = initial_state("goal")
        state["papers"] = [paper(i, f"Contrastive segmentation {i}") for i in range(6)]
        state["queries"] = ["contrastive segmentation model"]
        payload = json.dumps({"coverage": "sufficient", "missing": [], "next_action": "synthesize", "rationale": "ok", "new_query": None})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=payload)) as complete:
            assessment, llm_delta, fallback = await tooling.run_coverage_assessment(state)
        complete.assert_awaited_once()
        self.assertEqual(llm_delta, 1)
        self.assertEqual(assessment["next_action"], "synthesize")


class AssessFeedbackTests(EnvCase, unittest.IsolatedAsyncioTestCase):
    async def test_feedback_propagates_to_updates(self):
        state = initial_state("goal")
        state["papers"] = [paper(i, f"Contrastive segmentation {i}") for i in range(6)]
        state["queries"] = ["contrastive segmentation model"]
        payload = json.dumps({"coverage": "insufficient", "missing": ["methods"], "next_action": "search_more",
                              "rationale": "缺方法细节", "new_query": "contrastive segmentation method",
                              "feedback": "候选多为应用类，缺少方法论文"})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=payload)):
            _, updates = await tooling._tool_assess_coverage(state, {})
        self.assertEqual(updates["assess_feedback"], "候选多为应用类，缺少方法论文")
        self.assertEqual(updates["coverage"]["new_query"], "contrastive segmentation method")


if __name__ == "__main__":
    unittest.main()

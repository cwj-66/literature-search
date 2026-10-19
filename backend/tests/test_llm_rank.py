"""LLM 绝对量表候选相关性打分：超阈值触发，失败/部分失败降级保留本地序。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.infrastructure import llm
from app.services.retrieval import ranking


def entry(i, title):
    return {"id": f"p:{i}", "title": title, "abstract": f"abstract {i}", "year": 2023}


class LlmRankTests(unittest.IsolatedAsyncioTestCase):
    async def test_below_threshold_skips_llm(self):
        papers = [entry(i, f"Paper {i}") for i in range(5)]
        with patch("app.infrastructure.llm.complete", new=AsyncMock()) as complete:
            ranked, calls = await ranking.llm_rank(papers, "rag")
        self.assertEqual(calls, 0)
        complete.assert_not_awaited()
        self.assertEqual([p["id"] for p in ranked], [p["id"] for p in papers])

    async def test_scores_reorder_candidates(self):
        papers = [entry(i, f"Paper {i}") for i in range(25)]
        scores = json.dumps({"scores": [[i, 25 - i] for i in range(25)]})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=scores)):
            ranked, calls = await ranking.llm_rank(papers, "rag")
        self.assertEqual(calls, 1)
        self.assertEqual(ranked[0]["id"], "p:0")
        self.assertEqual(ranked[-1]["id"], "p:24")
        self.assertEqual(ranked[0]["rank_score"], 25)

    async def test_llm_failure_keeps_local_order(self):
        papers = [entry(i, f"Paper {i}") for i in range(25)]
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=llm.AnalysisError("模型调用失败"))):
            ranked, calls = await ranking.llm_rank(papers, "rag")
        self.assertEqual(calls, 1)
        self.assertEqual([p["id"] for p in ranked], [p["id"] for p in papers])
        self.assertNotIn("rank_score", ranked[0])

    async def test_partial_scores_unscored_keep_local_order_at_tail(self):
        papers = [entry(i, f"Paper {i}") for i in range(6)]
        scores = json.dumps({"scores": [[4, 9], [2, 8]]})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=scores)):
            ranked, _ = await ranking.llm_rank(papers, "rag", min_candidates=3)
        self.assertEqual([p["id"] for p in ranked], ["p:4", "p:2", "p:0", "p:1", "p:3", "p:5"])


if __name__ == "__main__":
    unittest.main()

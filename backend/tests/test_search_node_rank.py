"""workflow.search_node 接入 LLM 候选打分的集成测试。"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from app.agents import agent, workflow
from app.agents.state import initial_state
from app.services.retrieval import ranking


def entry(i):
    return {"id": f"p:{i}", "title": f"Paper {i}", "abstract": f"abstract {i}", "year": 2023}


class SearchNodeRankIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_node_ranks_pool_above_threshold(self):
        state = {**initial_state("goal"), "queries": ["rag"], "query_index": 0,
                 "papers": [entry(i) for i in range(25)]}
        scores = json.dumps({"scores": [[i, 25 - i] for i in range(25)]})
        with patch("app.services.retrieval.search.search_papers",
                   new=AsyncMock(return_value={"papers": [], "source_errors": {}, "source_retry_after": {}})), \
             patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=scores)) as complete:
            updates = await workflow.search_node(state)
        self.assertTrue(complete.await_count >= 1)
        self.assertEqual(updates["papers"][0]["id"], "p:0")
        self.assertGreaterEqual(updates.get("llm_calls", 0), state.get("llm_calls", 0) + 1)

    async def test_search_node_below_threshold_skips_ranking(self):
        state = {**initial_state("goal"), "queries": ["rag"], "query_index": 0,
                 "papers": [entry(i) for i in range(5)]}
        with patch("app.services.retrieval.search.search_papers",
                   new=AsyncMock(return_value={"papers": [], "source_errors": {}, "source_retry_after": {}})), \
             patch("app.infrastructure.llm.complete", new=AsyncMock()) as complete:
            updates = await workflow.search_node(state)
        complete.assert_not_awaited()
        self.assertNotIn("papers", updates)
        self.assertNotIn("rank_score", state["papers"][0])


if __name__ == "__main__":
    unittest.main()

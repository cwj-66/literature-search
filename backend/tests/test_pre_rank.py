"""候选本地重排：超出候选池上限时按相关性保留，而不是丢弃最旧。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.agents import agent, tooling
from app.services.retrieval import search


def entry(pid, title, abstract=""):
    return {"id": pid, "title": title, "abstract": abstract, "year": 2023}


class RankCandidatesTests(unittest.TestCase):
    def test_ranks_relevant_paper_above_off_topic(self):
        papers = [
            entry("a", "Unrelated biology study", "cells and proteins"),
            entry("b", "Contrastive learning for segmentation", "we propose contrastive learning"),
        ]
        ranked = search.rank_candidates(papers, '"contrastive learning" AND ("segmentation")')
        self.assertEqual(ranked[0]["id"], "b")

    def test_plain_query_ranks_by_keyword_score(self):
        papers = [
            entry("a", "Survey of everything", "general"),
            entry("b", "RAG reduces hallucination", "retrieval augmented generation reduces hallucination"),
        ]
        ranked = search.rank_candidates(papers, "rag hallucination")
        self.assertEqual(ranked[0]["id"], "b")

    def test_empty_words_keep_input_order(self):
        papers = [entry("a", "x"), entry("b", "y")]
        self.assertEqual([p["id"] for p in search.rank_candidates(papers, "")], ["a", "b"])


class CandidatePoolTrimTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_tool_trims_pool_by_relevance_not_age(self):
        state = {**agent.initial_state("goal"), "queries": ["q"], "search_count": 0}
        fresh = [
            entry("new-off", "Completely unrelated", "nothing relevant"),
            entry("new-hit", "Contrastive learning segmentation", "contrastive learning"),
        ]
        with patch("app.services.retrieval.search.search_papers", new=AsyncMock(return_value={"papers": fresh, "source_errors": {}})), \
             patch.object(tooling, "MAX_CANDIDATE_PAPERS", 2):
            existing = entry("old-relevant", "Contrastive learning", "medical segmentation")
            state["papers"] = [existing.model_dump() if hasattr(existing, "model_dump") else existing]
            _, updates = await tooling._tool_search_papers(state, {"query": "contrastive learning", "page": 1})
        kept = {paper["id"] for paper in updates["papers"]}
        self.assertEqual(kept, {"old-relevant", "new-hit"})


if __name__ == "__main__":
    unittest.main()

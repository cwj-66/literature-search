"""覆盖评估规则路径加固：标题漂移检测、确定性查询改写、查询指纹反重复。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.agents import agent, tooling
from app.agents.state import initial_state


def paper(pid, title):
    return {"id": pid, "title": title, "abstract": "", "year": 2023}


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_normalizes_whitespace_keeps_phrases(self):
        fp1 = tooling.query_fingerprint('"contrastive learning"  AND   (segmentation)')
        fp2 = tooling.query_fingerprint('"contrastive learning" AND (segmentation)')
        self.assertEqual(fp1, fp2)
        self.assertIn('"contrastive learning"', fp1)

    def test_fingerprint_distinguishes_operator_order(self):
        self.assertNotEqual(tooling.query_fingerprint("a AND b"), tooling.query_fingerprint("b AND a"))


class TitleDriftTests(unittest.TestCase):
    def test_drift_when_top_titles_miss_query_terms(self):
        state = initial_state("goal")
        state["papers"] = [paper(f"p:{i}", f"Unrelated biology {i}") for i in range(5)]
        state["queries"] = ["contrastive segmentation"]
        self.assertTrue(tooling._title_drift(state))

    def test_no_drift_when_titles_cover_query_terms(self):
        state = initial_state("goal")
        state["papers"] = [paper(f"p:{i}", f"Contrastive segmentation study {i}") for i in range(5)]
        state["queries"] = ["contrastive segmentation"]
        self.assertFalse(tooling._title_drift(state))

    def test_no_signal_with_few_candidates(self):
        state = initial_state("goal")
        state["papers"] = [paper("p:1", "Unrelated")]
        state["queries"] = ["contrastive segmentation"]
        self.assertFalse(tooling._title_drift(state))


class RuleRewriteTests(unittest.TestCase):
    def test_rewrite_drops_last_and_group(self):
        self.assertEqual(tooling._rule_rewrite_query('("a") AND ("b")', {"queries": []}), '("a")')

    def test_rewrite_drops_last_word_for_plain_query(self):
        self.assertEqual(tooling._rule_rewrite_query("a b c", {"queries": []}), "a b")

    def test_rewrite_returns_none_when_nothing_to_relax(self):
        self.assertIsNone(tooling._rule_rewrite_query("ab", {"queries": []}))
        self.assertIsNone(tooling._rule_rewrite_query('("a")', {"queries": []}))


class RuleAssessIntegrationTests(unittest.TestCase):
    def base_state(self):
        state = initial_state("goal")
        state["papers"] = [paper(f"p:{i}", f"Unrelated topic {i}") for i in range(6)]
        state["queries"] = ["contrastive segmentation model"]
        return state

    def test_drift_triggers_search_more_with_rewrite(self):
        state = self.base_state()
        result = tooling._rule_assess(state)
        self.assertEqual(result["next_action"], "search_more")
        self.assertEqual(result["new_query"], "contrastive segmentation")

    def test_rewrite_already_tried_falls_back(self):
        state = self.base_state()
        state["query_fingerprints"] = [tooling.query_fingerprint("contrastive segmentation")]
        result = tooling._rule_assess(state)
        self.assertNotEqual(result["new_query"], "contrastive segmentation")

    async def test_search_tool_records_query_fingerprint(self):
        state = {**initial_state("goal"), "queries": [], "search_count": 0}
        with patch("app.services.retrieval.search.search_papers",
                   new=AsyncMock(return_value={"papers": [], "source_errors": {}, "source_retry_after": {}})):
            _, updates = await tooling._tool_search_papers(state, {"query": "contrastive segmentation", "page": 1})
        self.assertIn(tooling.query_fingerprint("contrastive segmentation"), updates["query_fingerprints"])


if __name__ == "__main__":
    unittest.main()

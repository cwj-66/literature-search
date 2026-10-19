"""查询方言编译：把 SearchPlan 编译出的布尔视图查询翻成各源检索语法。"""

import unittest

from app.services.retrieval.search import arxiv_search_query, compile_query_dialect


VIEW = '("contrastive learning" OR "instance discrimination") AND ("medical image segmentation")'


class DialectCompilerTests(unittest.TestCase):
    def test_arxiv_dialect_compiles_groups(self):
        compiled = compile_query_dialect(VIEW, "arxiv")
        self.assertEqual(compiled, '(all:"contrastive learning" OR all:"instance discrimination") AND (all:"medical image segmentation")')

    def test_arxiv_dialect_appends_year_range(self):
        compiled = arxiv_search_query(VIEW, 2020, 2024)
        self.assertIn('AND submittedDate:[202001010000 TO 202412312359]', compiled)
        self.assertIn('OR all:"instance discrimination"', compiled)

    def test_s2_dialect_keeps_boolean_view(self):
        compiled = compile_query_dialect(VIEW, "s2")
        self.assertEqual(compiled, VIEW)

    def test_plain_query_falls_back_to_all_words_and(self):
        compiled = arxiv_search_query("rag hallucination", None, None)
        self.assertEqual(compiled, '(all:"rag" AND all:"hallucination")')

    def test_single_group_view(self):
        compiled = compile_query_dialect('("self-supervised")', "arxiv")
        self.assertEqual(compiled, '(all:"self-supervised")')


if __name__ == "__main__":
    unittest.main()

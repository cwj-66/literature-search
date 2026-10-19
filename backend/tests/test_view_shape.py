"""parse_view_groups 严格校验：非视图查询不得误判，畸形输入不得产出垃圾检索式。"""

import unittest

from app.services.retrieval.search import compile_query_dialect, parse_view_groups


class ViewShapeValidationTests(unittest.TestCase):
    def test_parenthesized_plain_query_is_not_a_view(self):
        self.assertIsNone(parse_view_groups("(2023) RAG survey"))

    def test_lowercase_and_is_not_a_view(self):
        # 小写 and 不属于 SearchPlan 编译产物，宁可走原样查询也不错误重组。
        self.assertIsNone(parse_view_groups('("a") and ("b")'))

    def test_valid_view_parses(self):
        groups = parse_view_groups('("contrastive learning" OR "instance discrimination") AND ("segmentation")')
        self.assertEqual(groups, [["contrastive learning", "instance discrimination"], ["segmentation"]])

    def test_quote_inside_term_rejected(self):
        self.assertIsNone(parse_view_groups('("a"b") AND ("c")'))

    def test_unbalanced_parens_rejected(self):
        self.assertIsNone(parse_view_groups('("a" AND ("b")'))

    def test_non_view_query_compiles_unchanged(self):
        self.assertEqual(compile_query_dialect("(2023) RAG survey", "arxiv"), "(2023) RAG survey")


if __name__ == "__main__":
    unittest.main()

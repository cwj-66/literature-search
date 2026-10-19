"""rank_candidates：布尔运算符不得参与相关性打分。"""

import unittest

from app.services.retrieval.search import rank_candidates


def paper(pid, title):
    return {"id": pid, "title": title, "abstract": "", "year": 2023}


class RankStopwordTests(unittest.TestCase):
    def test_boolean_operators_do_not_drive_score(self):
        papers = [
            paper("a", "AND OR NOT study"),   # 标题由运算符组成，不应得高分
            paper("b", "contrastive segmentation"),
        ]
        ranked = rank_candidates(papers, '("contrastive" OR "segmentation") AND ("model")')
        self.assertEqual(ranked[0]["id"], "b")

    def test_view_query_terms_used_for_scoring(self):
        papers = [
            paper("a", "unrelated"),
            paper("b", "contrastive model"),
        ]
        ranked = rank_candidates(papers, '("contrastive") AND ("model")')
        self.assertEqual(ranked[0]["id"], "b")


if __name__ == "__main__":
    unittest.main()

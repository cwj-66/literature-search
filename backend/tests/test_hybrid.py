import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.retrieval import hybrid
from app.services.analysis.fulltext import PreparedText
from app.schemas import PaperOut


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_rrf_and_per_paper_cap(self):
        fused = hybrid.rrf([["a", "b", "c"], ["b", "d", "a"]])
        self.assertEqual(fused[0], "b")
        by_id = {
            "c1": {"paper_id": "p1"}, "c2": {"paper_id": "p1"}, "c3": {"paper_id": "p1"},
            "c4": {"paper_id": "p1"}, "c5": {"paper_id": "p2"},
        }
        self.assertEqual(hybrid.cap_per_paper(["c1", "c2", "c3", "c4", "c5"], by_id, 2), ["c1", "c2", "c5"])

    def test_index_and_keyword_search(self):
        paper = PaperOut(id="arxiv:2005.11401", title="RAG", abstract="Retrieval augmented generation reduces hallucination.", sources=["arxiv"])
        prepared = PreparedText("Retrieval augmented generation reduces hallucination with citations.", "abstract", "abstract", None, None, {"truncated": False})
        hybrid.index_prepared(paper, prepared, "local1")
        hits = hybrid.keyword_search("hallucination citations", ["arxiv:2005.11401"], 5)
        self.assertTrue(hits)
        chunks = hybrid.load_chunks(["arxiv:2005.11401"])
        self.assertEqual(chunks[hits[0]]["paper_id"], "arxiv:2005.11401")


if __name__ == "__main__":
    unittest.main()

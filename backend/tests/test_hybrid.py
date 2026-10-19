import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.retrieval import hybrid
from app.infrastructure import embeddings
from app.infrastructure.db import connection
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


class HybridVectorMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_model_vectors_are_reencoded_before_comparison(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(directory) / "papers.sqlite3"),
            "SILICONFLOW_EMBEDDING_MODEL": "BAAI/bge-m3",
        }):
            paper = PaperOut(id="arxiv:2005.11401", title="RAG")
            prepared = PreparedText("Retrieval augmented generation.", "abstract", "abstract", None, None, {})
            hybrid.index_prepared(paper, prepared)
            with connection() as db, db:
                db.execute("UPDATE paper_chunks SET embed_model = ?, vector = ?", ("old-model", "[0,1,0]"))
            chunks = hybrid.load_chunks([paper.id])
            async def encode(texts):
                return {text: [1.0, 0.0] for text in texts}
            with patch.object(embeddings, "embed_many", new=AsyncMock(side_effect=encode)) as embed:
                hits = await hybrid.vector_search("retrieval", chunks, 5)
            self.assertTrue(hits)
            self.assertEqual(embed.await_count, 2)
            self.assertTrue(all(c["embed_model"] == "BAAI/bge-m3" and c["vector"] == [1., 0.] for c in chunks.values()))
            with connection() as db:
                self.assertEqual(db.execute("SELECT DISTINCT embed_model FROM paper_chunks").fetchone()[0], "BAAI/bge-m3")


if __name__ == "__main__":
    unittest.main()

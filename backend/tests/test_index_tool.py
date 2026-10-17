import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import embeddings
from app.tools import index_paper as index_tool
from app.services.analysis.fulltext import PreparedText
from app.services.papers.library import connection
from app.schemas import LocalPaperOut


def fake_embed(model="BAAI/bge-m3", dim=4):
    async def embed(texts):
        found = {}
        for index, text in enumerate(texts):
            vector = [0.0] * dim
            vector[index % dim] = 1.0
            found[text] = vector
        return model, found
    return embed


def chunks(paper_id):
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM vector_chunks WHERE paper_id = ? ORDER BY ordinal",
            (paper_id,),
        ).fetchall()
    return [dict(row) for row in rows]


class SplitTests(unittest.TestCase):
    def test_fixed_window_with_overlap(self):
        self.assertEqual(index_tool.split_text("  "), [])
        self.assertEqual(index_tool.split_text("检索"), [(0, "检索")])
        text = "甲" * (index_tool.CHUNK_CHARS + 50)
        parts = index_tool.split_text(text)
        self.assertEqual(parts[0], (0, "甲" * index_tool.CHUNK_CHARS))
        self.assertEqual(parts[1][0], index_tool.CHUNK_CHARS - index_tool.OVERLAP)
        self.assertEqual(parts[1][1], text[parts[1][0]:])
        self.assertEqual(len(parts), 2)


class IndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(root / "papers.sqlite3"),
            "PAPER_FAISS_PATH": str(root / "vectors.faiss"),
            "SILICONFLOW_API_KEY": "test-key",
            "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
            "SILICONFLOW_EMBEDDING_MODEL": "BAAI/bge-m3",
            "ANALYSIS_WORKER": "0",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_index_stores_text_in_sqlite_and_vectors_in_faiss(self):
        text = "检索增强生成可以减少幻觉。"
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            result = await index_tool.index_paper("arxiv:1", text=text, title="RAG", workspace_id="demo")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool"], "index_paper")
        self.assertEqual(result["chunk_count"], 1)
        self.assertEqual(result["embed_model"], "BAAI/bge-m3")
        self.assertEqual(result["vector_dim"], 4)
        self.assertEqual(result["message"], "索引完成")
        self.assertEqual(result["basis"], "text")
        stored = chunks("arxiv:1")
        self.assertEqual(stored[0]["text"], text)
        self.assertEqual(stored[0]["workspace_id"], "demo")
        self.assertEqual(stored[0]["start_char"], 0)
        self.assertEqual(stored[0]["end_char"], len(text))
        self.assertNotIn("vector", stored[0])
        saved = index_tool.get_index_status("arxiv:1")
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["title"], "RAG")
        import faiss
        import numpy as np
        index = index_tool._read_index(index_tool.faiss_path())
        self.assertEqual(index.ntotal, 1)
        query = np.asarray([[1.0, 0.0, 0.0, 0.0]], dtype="float32")
        faiss.normalize_L2(query)
        scores, ids = index.search(query, 1)
        self.assertEqual(int(ids[0][0]), stored[0]["faiss_id"])
        self.assertAlmostEqual(float(scores[0][0]), 1.0, places=5)

    async def test_long_text_is_split_before_embedding(self):
        seen = []

        async def embed(texts):
            seen.extend(texts)
            return "BAAI/bge-m3", {text: [1.0, 0.0] for text in texts}

        text = "甲" * (index_tool.CHUNK_CHARS + 50)
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=embed):
            result = await index_tool.index_paper("arxiv:long", text=text)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["chunk_count"], 2)
        self.assertEqual(seen[0], text[:index_tool.CHUNK_CHARS])
        self.assertEqual(len(chunks("arxiv:long")), 2)
        self.assertEqual(index_tool._read_index(index_tool.faiss_path()).ntotal, 2)

    async def test_reindex_replaces_chunks(self):
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            await index_tool.index_paper("arxiv:1", text="甲" * (index_tool.CHUNK_CHARS + 50))
            result = await index_tool.index_paper("arxiv:1", text="只剩一段。")
        self.assertEqual(result["chunk_count"], 1)
        self.assertEqual([row["text"] for row in chunks("arxiv:1")], ["只剩一段。"])
        self.assertEqual(index_tool._read_index(index_tool.faiss_path()).ntotal, 1)

    async def test_second_paper_keeps_both_vectors(self):
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            await index_tool.index_paper("arxiv:1", text="第一篇论文。")
            result = await index_tool.index_paper("arxiv:2", text="第二篇论文。")
        self.assertEqual(result["status"], "completed")
        kept = chunks("arxiv:2")[0]["faiss_id"]
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            again = await index_tool.index_paper("arxiv:1", text="改写后的第一篇。")
        self.assertEqual(again["status"], "completed")
        self.assertEqual([row["text"] for row in chunks("arxiv:1")], ["改写后的第一篇。"])
        self.assertEqual(chunks("arxiv:2")[0]["faiss_id"], kept)
        self.assertEqual(index_tool._read_index(index_tool.faiss_path()).ntotal, 2)

    async def test_other_model_does_not_overwrite(self):
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            await index_tool.index_paper("arxiv:1", text="第一篇论文。")
        before = index_tool.faiss_path().read_bytes()
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed(model="other-model")):
            result = await index_tool.index_paper("arxiv:2", text="另一模型。")
        self.assertEqual(result["status"], "failed")
        self.assertIn("不能混用", result["message"])
        self.assertIsNone(index_tool.get_index_status("arxiv:2"))
        self.assertEqual(index_tool.get_index_status("arxiv:1")["status"], "completed")
        self.assertEqual(index_tool.faiss_path().read_bytes(), before)

    async def test_failed_write_keeps_previous_index(self):
        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            await index_tool.index_paper("arxiv:1", text="第一篇论文。")
            before = index_tool.faiss_path().read_bytes()
            with patch("app.tools.index_paper._replace_chunks", side_effect=OSError("disk full")):
                result = await index_tool.index_paper("arxiv:2", text="写到一半失败。")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"], "向量库写入失败")
        self.assertEqual(index_tool.faiss_path().read_bytes(), before)
        self.assertEqual(len(chunks("arxiv:1")), 1)
        self.assertEqual(chunks("arxiv:2"), [])

    async def test_embedding_error_writes_nothing(self):
        async def boom(_texts):
            raise embeddings.EmbeddingError("语义向量请求失败")

        with patch("app.infrastructure.embeddings.embed_siliconflow", new=boom):
            result = await index_tool.index_paper("arxiv:1", text="正文")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"], "语义向量请求失败")
        self.assertFalse(index_tool.faiss_path().exists())
        self.assertIsNone(index_tool.get_index_status("arxiv:1"))

    async def test_missing_key_returns_failed(self):
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": ""}):
            result = await index_tool.index_paper("arxiv:1", text="正文")
        self.assertEqual(result["status"], "failed")
        self.assertIn("硅基流动", result["message"])
        self.assertFalse(index_tool.faiss_path().exists())

    async def test_workspace_does_not_index_outside_library(self):
        with patch("app.tools.index_paper.fulltext.prepare", new=AsyncMock(side_effect=AssertionError("should not fetch"))):
            result = await index_tool.index_paper("arxiv:missing", workspace_id="demo")
        self.assertEqual(result["status"], "failed")
        self.assertIn("文献库", result["message"])

    async def test_library_text_is_indexed_when_body_omitted(self):
        paper = LocalPaperOut(
            id="arxiv:1", local_id="loc", imported_at="2026-01-01T00:00:00+00:00",
            title="RAG", sources=["arxiv"], abstract="摘要",
        )
        prepared = PreparedText("全文正文", "full_text", "pdf", "https://arxiv.org/pdf/1", None, {})
        with patch("app.tools.index_paper.library.get_paper", return_value=paper), \
             patch("app.tools.index_paper.fulltext.prepare", new=AsyncMock(return_value=prepared)), \
             patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            result = await index_tool.index_paper("arxiv:1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["basis"], "full_text")
        self.assertEqual(result["title"], "RAG")
        self.assertEqual(chunks("arxiv:1")[0]["text"], "全文正文")

    async def test_embed_posts_to_siliconflow(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"data": [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [0.0, 1.0]},
            ]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.object(embeddings.httpx, "AsyncClient", return_value=client):
            model, vectors = await embeddings.embed_siliconflow(["甲", "乙"])
        self.assertEqual(model, "BAAI/bge-m3")
        self.assertEqual(seen["url"], "https://api.siliconflow.cn/v1/embeddings")
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual(seen["body"]["model"], "BAAI/bge-m3")
        self.assertEqual(seen["body"]["input"], ["甲", "乙"])
        self.assertEqual(vectors["甲"], [1.0, 0.0])
        self.assertEqual(vectors["乙"], [0.0, 1.0])

    async def test_route_returns_completed(self):
        from app.main import app

        with patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/tools/index_paper", json={
                    "paper_id": "arxiv:1", "text": "检索增强生成。", "title": "RAG",
                })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["chunk_count"], 1)
        self.assertEqual(chunks("arxiv:1")[0]["text"], "检索增强生成。")

    async def test_blank_paper_id_is_rejected(self):
        with self.assertRaises(ValueError):
            await index_tool.index_paper("  ")


if __name__ == "__main__":
    unittest.main()

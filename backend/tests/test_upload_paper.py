import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import mineru
from app.main import app
from app.schemas import PaperOut
from app.services.analysis import fulltext
from app.services.papers import documents, library, upload
from app.tools import index_paper as index_tool


def zip_markdown(text: str, name: str = "full.md") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.md", "ignore")
        archive.writestr(name, text)
    return buffer.getvalue()


def fake_embed(model="BAAI/bge-m3", dim=4):
    async def embed(texts):
        found = {}
        for index, text in enumerate(texts):
            vector = [0.0] * dim
            vector[index % dim] = 1.0
            found[text] = vector
        return model, found
    return embed


class MarkdownTests(unittest.TestCase):
    def test_prefers_full_markdown(self):
        text = mineru.markdown_from_zip(zip_markdown("# 标题\n\n正文"))
        self.assertEqual(text, "# 标题\n\n正文")

    def test_title_comes_from_heading_or_filename(self):
        self.assertEqual(upload.title_and_abstract("# RAG 检索\n\n摘要\n\n这是摘要。\n\n# 引言\n\n后文", "a.pdf"), ("RAG 检索", "这是摘要。"))
        self.assertEqual(upload.title_and_abstract("没有标题", "my_paper.pdf")[0], "my paper")


class MinerUTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_polls_until_done(self):
        seen = {"polls": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/file-urls/batch"):
                return httpx.Response(200, json={"code": 0, "data": {"batch_id": "b1", "file_urls": ["https://mineru.net/upload/paper.pdf"]}})
            if request.method == "PUT":
                self.assertTrue(request.content.startswith(b"%PDF"))
                return httpx.Response(200)
            if request.url.path.endswith("/extract-results/batch/b1"):
                seen["polls"] += 1
                state = "running" if seen["polls"] == 1 else "done"
                body = {"file_name": "paper.pdf", "state": state}
                if state == "done":
                    body["full_zip_url"] = "https://cdn-mineru.openxlab.org.cn/result.zip"
                return httpx.Response(200, json={"code": 0, "data": {"extract_result": [body]}})
            if request.url.host == "cdn-mineru.openxlab.org.cn":
                return httpx.Response(200, content=zip_markdown("# 解析结果\n\n正文足够长。"))
            return httpx.Response(404)

        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        with patch.dict(os.environ, {"MINERU_API_TOKEN": "token", "MINERU_POLL_INTERVAL": "0", "MINERU_POLL_LIMIT": "3"}), \
             patch("app.infrastructure.mineru.httpx.AsyncClient", factory):
            text = await mineru.parse_pdf(b"%PDF-1.7 demo", "paper.pdf")
        self.assertIn("解析结果", text)
        self.assertEqual(seen["polls"], 2)

    async def test_rejects_private_upload_url_and_missing_token(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 0, "data": {"batch_id": "b1", "file_urls": ["http://127.0.0.1/paper.pdf"]}})

        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        with patch.dict(os.environ, {"MINERU_API_TOKEN": "token", "MINERU_POLL_INTERVAL": "0"}), \
             patch("app.infrastructure.mineru.httpx.AsyncClient", factory):
            with self.assertRaises(mineru.MinerUError):
                await mineru.parse_pdf(b"%PDF", "paper.pdf")
        with patch.dict(os.environ, {"MINERU_API_TOKEN": ""}):
            with self.assertRaises(mineru.MinerUError):
                await mineru.parse_pdf(b"%PDF", "paper.pdf")


class IngestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(root / "papers.sqlite3"),
            "PAPER_FAISS_PATH": str(root / "vectors.faiss"),
            "SILICONFLOW_API_KEY": "test-key",
            "ANALYSIS_WORKER": "0",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def test_ingest_saves_paper_and_calls_index_tool(self):
        markdown = "# 本地论文\n\n摘要\n\n这是一篇上传的论文。\n"
        with patch("app.services.papers.upload.mineru.parse_pdf", new=AsyncMock(return_value=markdown)), \
             patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            result = await upload.ingest_pdf(b"%PDF-1.4 hello", "local_notes.pdf", "pdemo")
        self.assertEqual(result["index"]["status"], "completed")
        self.assertEqual(result["index"]["tool"], "index_paper")
        self.assertGreaterEqual(result["index"]["chunk_count"], 1)
        paper = result["paper"]
        self.assertEqual(paper.title, "本地论文")
        self.assertEqual(paper.sources, ["upload"])
        self.assertTrue(paper.id.startswith("upload:"))
        stored = documents.get_text(paper.local_id, paper.id)
        self.assertIn("上传的论文", stored)
        chunks = index_tool.get_index_status(paper.local_id)
        self.assertEqual(chunks["status"], "completed")
        self.assertEqual(chunks["workspace_id"], "pdemo")
        again = await self._again(markdown)
        self.assertFalse(again["created"])
        self.assertEqual(again["paper"].local_id, paper.local_id)
        self.assertEqual(again["paper"].id, paper.id)

    async def _again(self, markdown):
        with patch("app.services.papers.upload.mineru.parse_pdf", new=AsyncMock(return_value=markdown)), \
             patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
            return await upload.ingest_pdf(b"%PDF-1.4 hello", "local_notes.pdf", "pdemo")

    async def test_rejects_non_pdf_and_indexes_uploaded_text_for_later_reads(self):
        with self.assertRaises(upload.UploadError):
            await upload.ingest_pdf(b"not a pdf", "a.pdf", "pdemo")
        paper = PaperOut(id="upload:abc", title="已保存", sources=["upload"], venue="本地上传")
        saved, _ = library.save_paper(paper, paper.id)
        documents.save(saved.local_id, saved.id, "这是解析后的全文。", "a.pdf")
        prepared = await fulltext.prepare(saved)
        self.assertEqual(prepared.basis, "full_text")
        self.assertEqual(prepared.source_kind, "upload")
        self.assertIn("解析后的全文", prepared.text)

    async def test_api_upload(self):
        markdown = "# 接口论文\n\n摘要\n\n接口上传的正文。\n"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            rejected = await client.post("/api/papers/upload", data={"workspace_id": "pdemo"}, files={"file": ("a.txt", b"hello", "text/plain")})
            self.assertEqual(rejected.status_code, 400)
            with patch("app.services.papers.upload.mineru.parse_pdf", new=AsyncMock(return_value=markdown)), \
                 patch("app.infrastructure.embeddings.embed_siliconflow", new=fake_embed()):
                response = await client.post(
                    "/api/papers/upload",
                    data={"workspace_id": "pdemo"},
                    files={"file": ("demo.pdf", b"%PDF-1.4 demo", "application/pdf")},
                )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["paper"]["title"], "接口论文")
        self.assertEqual(body["index"]["status"], "completed")
        self.assertTrue(library.get_paper(body["paper"]["local_id"]))

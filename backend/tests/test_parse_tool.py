import io
import json
import os
import unittest
import zipfile
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import mineru
from app.main import app
from app.tools import parse_paper as tool


def archive(content=None, markdown="# Paper\n\nBody"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as result:
        result.writestr("paper/full.md", markdown)
        if content is not None:
            result.writestr("paper/paper_content_list.json", json.dumps(content))
    return buffer.getvalue()


CONTENT = [
    {"type": "text", "text": "Paper", "text_level": 1, "page_idx": 0},
    {"type": "text", "text": "Introduction", "text_level": 2, "page_idx": 0},
    {"type": "text", "text": "First paragraph", "page_idx": 0, "bbox": [0, 1, 2, 3]},
    {"type": "text", "text": "Continuation", "page_idx": 1},
    {"type": "text", "text": "Methods", "text_level": 2, "page_idx": 1},
    {"type": "table", "table_caption": ["Results"], "table_body": "<table>42</table>", "page_idx": 2},
    {"type": "equation", "text": "E=mc^2", "page_idx": 2},
]


class StructureTests(unittest.TestCase):
    def test_sections_pages_and_non_text_content(self):
        result = tool.normalize(mineru.structured_from_zip(archive(CONTENT)), "paper.pdf")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(result["blocks"][2]["page_number"], 1)
        self.assertEqual(result["blocks"][2]["bbox"], [0, 1, 2, 3])
        title, intro, method = result["sections"]
        self.assertEqual(intro["parent_id"], title["id"])
        self.assertEqual((intro["page_start"], intro["page_end"]), (1, 2))
        self.assertIn("Continuation", intro["text"])
        self.assertNotIn("Methods", intro["text"])
        self.assertEqual(method["page_end"], 3)
        self.assertIn("<table>42</table>", result["text"])
        self.assertIn("E=mc^2", result["text"])

    def test_missing_structure_does_not_invent_pages(self):
        result = tool.normalize(mineru.structured_from_zip(archive()), "paper.pdf")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["page_count"])
        self.assertIsNone(result["sections"][0]["page_start"])
        self.assertTrue(result["warnings"])
        self.assertIn("Body", result["text"])

    def test_missing_and_invalid_pages_are_unknown(self):
        result = tool.normalize({"markdown": "body", "content_list": [
            {"text": "a", "page_idx": -1}, {"text": "b", "page_idx": True},
            {"text": "c", "page_idx": 4},
        ]}, "a.pdf")
        self.assertEqual([block["page_number"] for block in result["blocks"]], [None, None, 5])
        self.assertEqual(result["status"], "partial")

    def test_markdown_headings_without_blank_lines_and_code_fences(self):
        result = tool.normalize({"markdown": "# Paper\nBody\n## Method\n```\n# code\n```", "content_list": None}, "a.pdf")
        self.assertEqual([s["title"] for s in result["sections"]], ["Paper", "Method"])
        self.assertIn("# code", result["text"])

    def test_invalid_archives_and_content_are_rejected(self):
        for blob in (b"not a zip", archive({"bad": "format"}), archive([1])):
            with self.assertRaises(mineru.MinerUError):
                mineru.structured_from_zip(blob)


class ParseToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_mineru_transport_and_api(self):
        calls = []

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/file-urls/batch"):
                return httpx.Response(200, json={"code": 0, "data": {
                    "batch_id": "b1", "file_urls": ["https://mineru.net/upload"]}})
            if request.method == "PUT":
                return httpx.Response(200)
            if request.url.path.endswith("/batch/b1"):
                return httpx.Response(200, json={"code": 0, "data": {"extract_result": [{
                    "file_name": "paper.pdf", "state": "done", "full_zip_url": "https://mineru.net/result.zip"}]}})
            return httpx.Response(200, content=archive(CONTENT))

        real_client = httpx.AsyncClient

        def factory(**kwargs):
            return real_client(**kwargs, transport=httpx.MockTransport(handler))

        async with real_client(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.dict(os.environ, {"MINERU_API_TOKEN": "test", "MINERU_POLL_INTERVAL": "0"}), \
                 patch.object(mineru.httpx, "AsyncClient", factory):
                response = await client.post("/api/tools/parse_paper", files={
                    "file": ("local.pdf", b"%PDF-1.7 test", "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["page_count"], 3)
        self.assertEqual(response.json()["filename"], "local.pdf")
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1].content, b"%PDF-1.7 test")
        self.assertNotIn("authorization", calls[1].headers)
        self.assertNotIn("authorization", calls[-1].headers)

    async def test_invalid_inputs_do_not_call_mineru(self):
        with patch.object(mineru, "parse_pdf_structured", new=AsyncMock()) as parse:
            with self.assertRaises(ValueError):
                await tool.parse_paper(b"not PDF")
            with patch.object(tool, "MAX_PDF", 3):
                with self.assertRaises(ValueError):
                    await tool.parse_paper(b"%PDF test")
            parse.assert_not_awaited()

    async def test_api_errors(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invalid = await client.post("/api/tools/parse_paper", files={"file": ("a.pdf", b"bad")})
            self.assertEqual(invalid.status_code, 400)
            for message in ("MinerU 解析超时，请稍后重试", "MinerU 解析失败"):
                with patch.object(mineru, "parse_pdf_structured", new=AsyncMock(side_effect=mineru.MinerUError(message))):
                    failed = await client.post("/api/tools/parse_paper", files={"file": ("a.pdf", b"%PDF test")})
                self.assertEqual(failed.status_code, 502)
                self.assertEqual(failed.json()["detail"], message)

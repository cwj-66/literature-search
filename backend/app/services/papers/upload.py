"""Save an uploaded PDF after MinerU parsing and vector indexing."""

import asyncio
import hashlib
import re
from pathlib import Path

from app.infrastructure import mineru
from app.schemas import PaperOut
from app.services.papers import documents, library
from app.tools import index_paper as index_tool
from app.tools.parse_paper import parse_paper

MAX_PDF = 50 * 1024 * 1024


class UploadError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def display_name(filename: str | None) -> str:
    name = Path(filename or "paper.pdf").name
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name).strip("._") or "paper.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:180]


def title_and_abstract(markdown: str, filename: str) -> tuple[str, str | None]:
    title = ""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break
    if not title:
        title = Path(filename).stem.replace("_", " ").strip() or "未命名论文"
    title = re.sub(r"\s+", " ", title)[:500]
    match = re.search(r"(?is)(?:^|\n)#{0,3}\s*(?:abstract|摘要)\s*\n+(.+?)(?:\n#{1,3}\s|\Z)", markdown)
    if match:
        abstract = re.sub(r"\s+", " ", match.group(1)).strip()
    else:
        body = markdown.split("\n", 1)[1] if markdown.lstrip().startswith("#") else markdown
        abstract = re.sub(r"\s+", " ", re.split(r"\n\s*\n", body.strip(), maxsplit=1)[0]).strip()
    return title, (abstract[:1200] or None)


async def ingest_pdf(data: bytes, filename: str | None, workspace_id: str | None) -> dict:
    if not data.startswith(b"%PDF"):
        raise UploadError("请上传 PDF 文件")
    if len(data) > MAX_PDF:
        raise UploadError("PDF 不能超过 50MB")
    workspace_id = (workspace_id or "").strip()
    if not workspace_id or len(workspace_id) > 80:
        raise UploadError("请在研究项目中上传")
    name = display_name(filename)
    try:
        structure = await parse_paper(data, "paper.pdf")
        markdown = structure["markdown"].strip()
    except mineru.MinerUError as exc:
        raise UploadError(exc.message, 502) from exc
    if not markdown:
        raise UploadError("MinerU 没有解析出正文", 502)
    body = structure["text"] if structure and structure.get("text") else markdown
    if len(body) > index_tool.MAX_TEXT:
        raise UploadError("全文超过索引长度限制，未截断保存，请人工处理")
    title, abstract = title_and_abstract(markdown, name)
    paper = PaperOut(
        id="upload:" + hashlib.sha256(data).hexdigest()[:32],
        title=title,
        abstract=abstract,
        venue="本地上传",
        sources=["upload"],
    )
    saved, created = await asyncio.to_thread(library.save_paper, paper, paper.id)
    await asyncio.to_thread(documents.save, saved.local_id, saved.id, body, name, structure)
    indexed = await index_tool.index_paper(saved.local_id, text=body, title=saved.title, workspace_id=workspace_id)
    return {"paper": saved, "created": created, "index": indexed}

"""PDF → MinerU blocks, sections and physical page numbers (one based)."""

import re
from pathlib import PurePosixPath

from app.infrastructure import mineru

MAX_PDF = 50 * 1024 * 1024
TOOL = {
    "name": "parse_paper",
    "description": "调用 MinerU 将 PDF 解析为正文、章节、文本块和页码，不调用语言模型。",
    "parameters": {"data": "PDF 文件字节", "filename": "PDF 文件名，默认 paper.pdf"},
}


def _text(item: dict) -> str:
    kind = item.get("type", "text")
    if kind == "table":
        keys = ("table_caption", "table_body", "table_footnote")
    elif kind == "image":
        keys = ("image_caption", "image_footnote")
    else:
        keys = ("text",)
    parts = []
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(part for part in value if isinstance(part, str))
    return "\n".join(parts).strip()


def normalize(parsed: dict, filename: str) -> dict:
    markdown = parsed["markdown"].strip()
    if not markdown:
        raise mineru.MinerUError("MinerU 没有解析出正文")
    content = parsed.get("content_list")
    warnings = []
    if not content:
        warnings.append("MinerU 未返回结构化内容列表，已从 Markdown 提取章节；页码和坐标未知。")
        content = []
        paragraph = []
        fenced = False
        for line in [*markdown.splitlines(), ""]:
            heading = re.match(r"^(#{1,6})\s+(.+)$", line) if not fenced else None
            if (heading or not line.strip()) and paragraph:
                content.append({"type": "text", "text": "\n".join(paragraph), "text_level": 0})
                paragraph = []
            if heading:
                content.append({"type": "text", "text": heading[2], "text_level": len(heading[1])})
            elif line.strip():
                paragraph.append(line)
            if line.startswith(("```", "~~~")):
                fenced = not fenced
    blocks, sections, stack = [], [], []
    page_indices = [item["page_idx"] for item in content
                    if type(item.get("page_idx")) is int and item["page_idx"] >= 0]
    for item in content:
        text = _text(item)
        index = item.get("page_idx")
        page = index + 1 if type(index) is int and index >= 0 else None
        level = item.get("text_level", 0)
        level = level if type(level) is int and level > 0 else 0
        block_id = f"b{len(blocks) + 1}"
        if level and text:
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            section = {"id": f"s{len(sections) + 1}", "title": text, "level": level,
                       "parent_id": stack[-1]["id"] if stack else None,
                       "page_start": page, "page_end": page, "block_ids": [], "text": ""}
            sections.append(section)
            stack.append(section)
        for section in stack:
            if page is not None:
                if section["page_start"] is None:
                    section["page_start"] = page
                section["page_end"] = page
        bbox = item.get("bbox")
        blocks.append({"id": block_id, "type": item.get("type", "text"), "text": text,
                       "page_number": page, "bbox": bbox if isinstance(bbox, list) else None,
                       "heading_level": level, "section_id": stack[-1]["id"] if stack else None})
        if stack:
            stack[-1]["block_ids"].append(block_id)
            stack[-1]["text"] += ("\n\n" if stack[-1]["text"] else "") + text
    if any(block["page_number"] is None for block in blocks) and not warnings:
        warnings.append("部分内容缺少页码；未知页码返回 null。")
    return {"tool": "parse_paper", "status": "partial" if warnings else "completed",
            "parser": "mineru", "filename": filename, "markdown": markdown,
            "text": "\n\n".join(block["text"] for block in blocks if block["text"]),
            "page_count": max(page_indices) + 1 if page_indices else None,
            "blocks": blocks, "sections": sections, "warnings": warnings}


async def parse_paper(data: bytes, filename: str = "paper.pdf") -> dict:
    if not data.startswith(b"%PDF"):
        raise ValueError("请上传 PDF 文件")
    if len(data) > MAX_PDF:
        raise ValueError("PDF 不能超过 50MB")
    name = PurePosixPath((filename or "paper.pdf").replace("\\", "/")).name
    # Keep arbitrary local filenames out of the cloud job metadata.
    parsed = await mineru.parse_pdf_structured(data, "paper.pdf")
    return normalize(parsed, name)

"""Background-only full-text acquisition; never exposed to the chat tool catalog."""

from app.services.analysis import fulltext
from app.services.papers import documents, library

TOOL = {"name": "acquire_paper_fulltext", "description": "后台获取可公开访问的 PDF，记录可用性。"}


async def acquire_paper_fulltext(paper_id: str) -> dict:
    paper = library.get_paper(paper_id)
    if paper is None:
        raise ValueError("论文未入库")
    if documents.get_text(paper.local_id, paper.id):
        return {"tool": "acquire_paper_fulltext", "status": "cached", "paper_id": paper.id, "pdf": None}
    reasons = []
    for url in fulltext._jobs(paper):
        if "/html/" in url:
            continue
        try:
            data = await fulltext.download(url, limit=50 * 1024 * 1024)
            if not data.startswith(b"%PDF"):
                reasons.append("来源未返回 PDF")
                continue
            return {"tool": "acquire_paper_fulltext", "status": "available", "paper_id": paper.id,
                    "source_url": url, "pdf": data}
        except fulltext.TextFailure as exc:
            reasons.append(exc.reason)
    return {"tool": "acquire_paper_fulltext", "status": "unavailable", "paper_id": paper.id, "pdf": None,
            "message": "；".join(dict.fromkeys(reasons)) or "没有可公开获取的 PDF 链接"}

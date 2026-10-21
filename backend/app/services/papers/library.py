"""Persistent local analysis library; no external URLs are fetched directly."""

import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse
from uuid import uuid4

from app.infrastructure.db import connection
from app.schemas import LocalPaperOut, PaperOut
from app.services.papers import ccf
from app.services.papers.paper_merge import combine, identity_keys


def normalize_reference(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        path = unquote(parsed.path)
        if host in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
            match = re.fullmatch(r"/(?:abs|pdf)/(.+?)(?:\.pdf)?/?", path)
            value = "arxiv:" + match[1] if match else ""
        elif host in ("doi.org", "dx.doi.org"):
            value = "doi:" + path.lstrip("/")
        elif host in ("semanticscholar.org", "www.semanticscholar.org") and path.startswith("/paper/"):
            value = "s2:" + path.rstrip("/").rsplit("/", 1)[-1]
        else:
            raise ValueError("仅支持 arXiv、DOI 或 Semantic Scholar 的论文链接")
    if value.startswith("arxiv:"):
        ref = re.sub(r"v\d+$", "", value[6:], flags=re.I)
        if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[a-zA-Z-]+)?/\d{7})", ref):
            return "arxiv:" + ref
    elif value.startswith("s2:") and re.fullmatch(r"[a-fA-F0-9]{40}", value[3:]):
        return "s2:" + value[3:].lower()
    elif value.startswith("doi:") and re.fullmatch(r"10\.\d{4,9}/\S+", value[4:]):
        return "doi:" + value[4:].lower()
    elif re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", value):
        return normalize_reference("arxiv:" + value)
    elif value.startswith("10."):
        return normalize_reference("doi:" + value)
    raise ValueError("请输入有效的 arXiv ID、DOI、Semantic Scholar ID 或论文链接")


def as_paper(row) -> LocalPaperOut:
    paper = ccf.annotate(PaperOut.model_validate_json(row["data"]))
    return LocalPaperOut(**paper.model_dump(), local_id=row["local_id"], imported_at=row["imported_at"])


def get_paper(ref: str) -> LocalPaperOut | None:
    with connection() as db:
        row = db.execute("""SELECT * FROM papers WHERE local_id = ? OR local_id IN
            (SELECT local_id FROM paper_keys WHERE key = ?) LIMIT 1""", (ref, ref)).fetchone()
        return as_paper(row) if row else None


def save_paper(paper: PaperOut, reference: str) -> tuple[LocalPaperOut, bool]:
    paper = ccf.annotate(paper)
    keys = identity_keys(paper) | {paper.id, reference}
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        marks = ",".join("?" for _ in keys)
        rows = db.execute(f"""SELECT DISTINCT p.* FROM papers p JOIN paper_keys k
            ON p.local_id = k.local_id WHERE k.key IN ({marks}) ORDER BY p.imported_at, p.local_id""", tuple(keys)).fetchall()
        created = not rows
        local_id = rows[0]["local_id"] if rows else uuid4().hex
        imported_at = rows[0]["imported_at"] if rows else datetime.now(timezone.utc).isoformat()
        # A DOI import can later bridge an arXiv and S2 entry. Merge transactionally.
        for row in rows:
            paper = combine(PaperOut.model_validate_json(row["data"]), paper)
            keys.update(r[0] for r in db.execute("SELECT key FROM paper_keys WHERE local_id = ?", (row["local_id"],)))
        db.execute("INSERT INTO papers VALUES (?, ?, ?) ON CONFLICT(local_id) DO UPDATE SET data = excluded.data", (local_id, paper.model_dump_json(), imported_at))
        for row in rows[1:]:
            db.execute("""INSERT OR IGNORE INTO workspace_papers (workspace_id, local_id, added_at)
                SELECT workspace_id, ?, added_at FROM workspace_papers WHERE local_id = ?""",
                (local_id, row["local_id"]))
            db.execute("""UPDATE analysis_tasks SET local_id = ?, paper_ref = CASE WHEN paper_ref = ? THEN ? ELSE paper_ref END
                WHERE local_id = ?""", (local_id, row["local_id"], local_id, row["local_id"]))
            db.execute("UPDATE analysis_results SET local_id = ? WHERE local_id = ?", (local_id, row["local_id"]))
            db.execute("DELETE FROM papers WHERE local_id = ?", (row["local_id"],))
        keys.update(identity_keys(paper))
        keys.add(paper.id)
        db.executemany("INSERT OR REPLACE INTO paper_keys VALUES (?, ?)", ((key, local_id) for key in keys))
        # Durable enqueue in the same transaction as import. No network work blocks saving.
        db.execute("INSERT OR IGNORE INTO relation_jobs (local_id, requested_at) VALUES (?, ?)",
                   (local_id, datetime.now(timezone.utc).isoformat()))
        return LocalPaperOut(**paper.model_dump(), local_id=local_id, imported_at=imported_at), created


def list_recent(limit: int) -> list[LocalPaperOut]:
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM papers ORDER BY imported_at DESC, local_id LIMIT ?",
            (limit,),
        ).fetchall()
        return [as_paper(row) for row in rows]


def list_papers(page: int, page_size: int) -> dict:
    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        rows = db.execute("SELECT * FROM papers ORDER BY imported_at DESC, local_id LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
        return {"papers": [as_paper(row) for row in rows], "total": total, "page": page, "page_size": page_size}

"""Durable, idempotent background PDF import queue shared across workspaces."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.infrastructure.db import connection
from app.services.papers import documents, library
from app.tools.acquire_paper_fulltext import acquire_paper_fulltext
from app.tools.parse_paper import parse_paper
from app.tools import index_paper as index_tool


def _ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS paper_processing (
        local_id TEXT PRIMARY KEY, status TEXT NOT NULL, message TEXT, source_url TEXT, updated_at TEXT NOT NULL)""")


def status(local_id):
    with connection() as db:
        _ensure(db)
        row = db.execute("SELECT * FROM paper_processing WHERE local_id = ?", (local_id,)).fetchone()
        return dict(row) if row else None


def enqueue(local_id):
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        _ensure(db)
        db.execute("INSERT OR IGNORE INTO paper_processing VALUES (?, 'pending', NULL, NULL, ?)", (local_id, stamp))
    return status(local_id)


def _finish(local_id, state, message=None, source_url=None):
    with connection() as db, db:
        _ensure(db)
        db.execute("UPDATE paper_processing SET status=?, message=?, source_url=?, updated_at=? WHERE local_id=?",
                   (state, message, source_url, datetime.now(timezone.utc).isoformat(), local_id))


def _claim():
    with connection() as db, db:
        _ensure(db)
        db.execute("BEGIN IMMEDIATE")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        db.execute("UPDATE paper_processing SET status='pending' WHERE status='running' AND updated_at < ?", (expired,))
        row = db.execute("SELECT local_id FROM paper_processing WHERE status='pending' ORDER BY updated_at, local_id LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE paper_processing SET status='running', updated_at=? WHERE local_id=?",
                       (datetime.now(timezone.utc).isoformat(), row[0]))
            return row[0]


async def process(local_id):
    paper = await asyncio.to_thread(library.get_paper, local_id)
    if not paper:
        raise ValueError("论文未入库")
    existing = await asyncio.to_thread(documents.get_text, local_id)
    indexed = (await asyncio.to_thread(index_tool.get_index_status, local_id)
               or await asyncio.to_thread(index_tool.get_index_status, paper.id))
    if existing and indexed and indexed["status"] == "completed":
        return {"status": "completed", "reused": True}
    acquired = await acquire_paper_fulltext(local_id)
    if acquired["status"] == "unavailable":
        return {"status": "unavailable", "message": acquired["message"]}
    source = acquired.get("source_url")
    if acquired["status"] == "available":
        parsed = await parse_paper(acquired["pdf"])
        text = parsed["text"] or parsed["markdown"]
        if len(text) > index_tool.MAX_TEXT:
            raise ValueError("全文超过索引长度限制，未截断保存，请人工处理")
        await asyncio.to_thread(documents.save, local_id, paper.id, text, "paper.pdf", parsed)
    else:
        text = existing
    result = await index_tool.index_paper(local_id, text=text, title=paper.title)
    return {"status": result["status"], "message": result["message"], "source_url": source}


async def worker():
    # Claims are transactional; expired leases recover after an abrupt process exit.
    while True:
        local_id = await asyncio.to_thread(_claim)
        if local_id is None:
            await asyncio.sleep(1)
            continue
        try:
            result = await process(local_id)
            await asyncio.to_thread(_finish, local_id, result["status"], result.get("message"), result.get("source_url"))
        except asyncio.CancelledError:
            await asyncio.to_thread(_finish, local_id, "pending", "等待服务恢复后继续")
            raise
        except Exception:
            await asyncio.to_thread(_finish, local_id, "failed", "论文处理失败，请检查全文来源、MinerU 和向量服务配置")

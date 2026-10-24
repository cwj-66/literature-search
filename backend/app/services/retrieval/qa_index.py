"""Durable QA indexing jobs consumed by the existing paper-processing worker.

All embeddings are validated before a single transaction replaces chunks + FTS.
The document revision is checked under that transaction: stale jobs cannot win.
FAISS is deliberately not touched here.
"""
import asyncio
import hashlib
import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from app.infrastructure import embeddings
from app.infrastructure.db import connection
from app.services.retrieval import hybrid, qa_chunking

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_MINUTES = 30


def now():
    return datetime.now(timezone.utc).isoformat()


def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS qa_index_jobs (
        local_id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, revision TEXT NOT NULL,
        embed_model TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL, retry_at TEXT, message TEXT)""")
    if 'split_config' not in {r[1] for r in db.execute('PRAGMA table_info(qa_index_jobs)')}:
        try:
            db.execute("ALTER TABLE qa_index_jobs ADD COLUMN split_config TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            if 'split_config' not in {r[1] for r in db.execute('PRAGMA table_info(qa_index_jobs)')}:raise


def revision(text, structure):
    # Empty structures are stored as NULL by documents.save_in_transaction.
    data = json.dumps([qa_chunking.signature(), text, structure or None], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def enqueue_in_transaction(db, local_id, paper_id, text, structure=None):
    ensure(db)
    rev, model = revision(text, structure), embeddings.model_name()
    db.execute("""INSERT INTO qa_index_jobs
        (local_id,paper_id,revision,embed_model,status,attempts,updated_at,retry_at,message,split_config)
        VALUES(?,?,?,?,'pending',0,?,NULL,NULL,?)
        ON CONFLICT(local_id) DO UPDATE SET paper_id=excluded.paper_id,revision=excluded.revision,
        embed_model=excluded.embed_model,status='pending',attempts=0,updated_at=excluded.updated_at,
        retry_at=NULL,message=NULL,split_config=excluded.split_config WHERE qa_index_jobs.revision!=excluded.revision
        OR qa_index_jobs.embed_model!=excluded.embed_model OR qa_index_jobs.paper_id!=excluded.paper_id
        OR qa_index_jobs.status='failed'""",
        (local_id, paper_id, rev, model, now(),qa_chunking.signature()))


def status(local_id):
    with connection() as db:
        ensure(db)
        row = db.execute("SELECT * FROM qa_index_jobs WHERE local_id=?", (local_id,)).fetchone()
        return dict(row) if row else None


def statuses(paper_ids):
    if not paper_ids:
        return {}
    with connection() as db:
        ensure(db)
        marks=",".join("?" for _ in paper_ids)
        return {r["paper_id"]:{k:r[k] for k in ("status","attempts","message","updated_at","revision","embed_model")}
                for r in db.execute(f"SELECT * FROM qa_index_jobs WHERE paper_id IN ({marks})",paper_ids)}


def reconcile(limit=20):
    """Bounded backfill for previously parsed documents and stale/missing indexes."""
    from app.services.papers.documents import _ensure
    with connection() as db, db:
        _ensure(db); ensure(db); hybrid.ensure(db)
        rows = db.execute("""SELECT d.*,p.data AS paper_data FROM paper_documents d
            JOIN papers p ON p.local_id=d.local_id
            LEFT JOIN qa_index_jobs j ON j.local_id=d.local_id
            WHERE j.local_id IS NULL OR j.split_config!=? OR j.embed_model!=? OR j.paper_id!=json_extract(p.data,'$.id') OR
            (j.status='ready' AND NOT EXISTS(SELECT 1 FROM paper_chunks c WHERE c.paper_id=j.paper_id
              AND c.source_hash=j.revision AND c.vector IS NOT NULL AND c.embed_model=j.embed_model)) OR
            (j.status='ready' AND EXISTS(SELECT 1 FROM paper_chunks c WHERE c.paper_id=j.paper_id
              AND c.parent_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM qa_parent_chunks p
                WHERE p.parent_id=c.parent_id AND p.paper_id=c.paper_id AND p.source_hash=c.source_hash)))
            LIMIT ?""", (qa_chunking.signature(),embeddings.model_name(), limit)).fetchall()
        for row in rows:
            structure = json.loads(row["structure"]) if row["structure"] else None
            # A corrupt/missing ready index is explicitly requeued even at the same revision.
            db.execute("UPDATE qa_index_jobs SET status='failed' WHERE local_id=? AND status='ready'", (row["local_id"],))
            enqueue_in_transaction(db, row["local_id"], json.loads(row["paper_data"])["id"], row["text"], structure)


def claim():
    with connection() as db, db:
        ensure(db)
        db.execute("BEGIN IMMEDIATE")
        expired = (datetime.now(timezone.utc) - timedelta(minutes=LEASE_MINUTES)).isoformat()
        db.execute("UPDATE qa_index_jobs SET status='pending' WHERE status='running' AND updated_at<?", (expired,))
        row = db.execute("""SELECT * FROM qa_index_jobs WHERE status='pending'
            OR (status='failed' AND attempts<? AND retry_at<=?) ORDER BY updated_at LIMIT 1""", (MAX_ATTEMPTS, now())).fetchone()
        if not row:
            return None
        db.execute("UPDATE qa_index_jobs SET status='running',attempts=attempts+1,updated_at=? WHERE local_id=?", (now(), row["local_id"]))
        return dict(row)


def chunks_for_document(text, structure, paper_id, local_id, rev):
    return qa_chunking.build(text,structure,paper_id,local_id,rev)


def persist(job, rows, mapping):
    """Swap only when every vector and the source revision are still current."""
    with connection() as db, db:
        hybrid.ensure(db); ensure(db)
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM qa_index_jobs WHERE local_id=?", (job["local_id"],)).fetchone()
        if not current or current["revision"] != job["revision"] or current["embed_model"] != job["embed_model"] or current["paper_id"] != job["paper_id"]:
            return False
        if embeddings.model_name() != job["embed_model"]:
            db.execute("UPDATE qa_index_jobs SET status='pending',embed_model=?,attempts=0 WHERE local_id=?",
                       (embeddings.model_name(), job["local_id"]))
            return False
        db.execute("""DELETE FROM paper_fts WHERE paper_id=? OR chunk_id IN
            (SELECT chunk_id FROM paper_chunks WHERE local_id=?)""", (job["paper_id"],job["local_id"]))
        db.execute("DELETE FROM paper_chunks WHERE paper_id=? OR local_id=?", (job["paper_id"],job["local_id"]))
        db.execute('DELETE FROM qa_parent_chunks WHERE paper_id=? OR local_id=?',(job['paper_id'],job['local_id']))
        parents={r['parent_id']:r['parent'] for r in rows}
        for p in parents.values():
            db.execute('''INSERT INTO qa_parent_chunks
                (parent_id,paper_id,local_id,source_hash,text,section,section_path,paragraph_ids,
                 page_start,page_end,source_start,source_end,token_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (p['parent_id'],p['paper_id'],p['local_id'],p['source_hash'],p['text'],p['section'],json.dumps(p['section_path']),
                 json.dumps(p['paragraph_ids']),p['page_start'],p['page_end'],p['source_start'],p['source_end'],p['token_count']))
        for row in rows:
            db.execute("""INSERT INTO paper_chunks (chunk_id,paper_id,local_id,section,text,paragraph_ids,
                start_char,embed_model,split_version,vector,basis,chunk_index,page_start,page_end,source_hash,
                parent_id,section_path,source_start,source_end,token_count)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                row["chunk_id"],row["paper_id"],row["local_id"],row["section"],row["text"],json.dumps(row["paragraph_ids"]),
                row["start_char"],job["embed_model"],qa_chunking.VERSION,json.dumps(mapping[row["text"]]),
                "full_text",row["chunk_index"],row["page_start"],row["page_end"],job["revision"],
                row['parent_id'],json.dumps(row['section_path']),row['source_start'],row['source_end'],row['token_count']))
            db.execute("INSERT INTO paper_fts(chunk_id,paper_id,body) VALUES(?,?,?)", (row["chunk_id"],row["paper_id"],row["text"]))
        db.execute("UPDATE qa_index_jobs SET status='ready',updated_at=?,retry_at=NULL,message=NULL WHERE local_id=?", (now(), job["local_id"]))
    return True


def fail(job, message, *, cancelled=False):
    retry = (datetime.now(timezone.utc) + timedelta(seconds=min(600, 30*2**job["attempts"]))).isoformat()
    with connection() as db, db:
        ensure(db)
        db.execute("""UPDATE qa_index_jobs SET status=?,updated_at=?,retry_at=?,message=?
            WHERE local_id=? AND revision=? AND embed_model=? AND paper_id=?""",
            ("pending" if cancelled else "failed", now(), retry, message,job["local_id"],job["revision"],job["embed_model"],job["paper_id"]))


async def run_one():
    await asyncio.to_thread(reconcile)
    job = await asyncio.to_thread(claim)
    if not job:
        return False
    try:
        from app.services.papers import documents
        text = await asyncio.to_thread(documents.get_text, job["local_id"], preserve_whitespace=True)
        structure = await asyncio.to_thread(documents.get_structure, job["local_id"])
        if not text or revision(text, structure) != job["revision"]:
            raise ValueError("全文版本已变化，等待重新入队")
        rows = chunks_for_document(text, structure, job["paper_id"],job["local_id"],job["revision"])
        if not rows:
            raise ValueError("全文未产生可检索分块")
        mapping = await asyncio.wait_for(embeddings.embed_many([r["text"] for r in rows], model=job["embed_model"]), timeout=600)
        dim = None
        for row in rows:
            vector = mapping.get(row["text"])
            if not vector or not all(type(v) in (int,float) and math.isfinite(v) for v in vector):
                raise ValueError("QA 全文向量不完整")
            dim = dim or len(vector)
            if len(vector) != dim or not any(vector):
                raise ValueError("QA 全文向量维度不一致或为空")
        written = await asyncio.to_thread(persist, job, rows, mapping)
        log.info("QA fulltext indexing local_id=%s ready=%s chunks=%d", job["local_id"], written, len(rows))
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.to_thread(fail,job,"等待服务恢复后重试",cancelled=True))
        raise
    except Exception as exc:
        log.exception("QA fulltext indexing failed local_id=%s",job["local_id"])
        await asyncio.to_thread(fail,job,str(exc)[:250])
    return True

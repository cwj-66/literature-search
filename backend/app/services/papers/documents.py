"""Markdown produced by MinerU for an uploaded PDF."""

from datetime import datetime, timezone

from app.infrastructure.db import connection


def _ensure(db) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS paper_documents (
            local_id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL UNIQUE,
            filename TEXT,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)


def save(local_id: str, paper_id: str, text: str, filename: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        _ensure(db)
        db.execute(
            """INSERT INTO paper_documents (local_id, paper_id, filename, text, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(local_id) DO UPDATE SET
                paper_id = excluded.paper_id,
                filename = excluded.filename,
                text = excluded.text,
                created_at = excluded.created_at""",
            (local_id, paper_id, filename, text, stamp),
        )


def get_text(*ids: str | None) -> str | None:
    keys = [item.strip() for item in ids if item and item.strip()]
    if not keys:
        return None
    with connection() as db:
        _ensure(db)
        marks = ",".join("?" for _ in keys)
        row = db.execute(
            f"SELECT text FROM paper_documents WHERE local_id IN ({marks}) OR paper_id IN ({marks}) LIMIT 1",
            (*keys, *keys),
        ).fetchone()
    if row is None:
        return None
    text = (row["text"] or "").strip()
    return text or None

"""Markdown produced by MinerU for an uploaded PDF."""

from datetime import datetime, timezone
import json

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
    if "structure" not in {row[1] for row in db.execute("PRAGMA table_info(paper_documents)")}:
        db.execute("ALTER TABLE paper_documents ADD COLUMN structure TEXT")


def save(local_id: str, paper_id: str, text: str, filename: str, structure: dict | None = None) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        _ensure(db)
        db.execute(
            """INSERT INTO paper_documents (local_id, paper_id, filename, text, created_at, structure)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(local_id) DO UPDATE SET
                paper_id = excluded.paper_id,
                filename = excluded.filename,
                text = excluded.text,
                created_at = excluded.created_at,
                structure = excluded.structure""",
            (local_id, paper_id, filename, text, stamp, json.dumps(structure, ensure_ascii=False) if structure else None),
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


def get_structure(local_id: str) -> dict | None:
    with connection() as db:
        _ensure(db)
        row = db.execute("SELECT structure FROM paper_documents WHERE local_id = ?", (local_id,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None

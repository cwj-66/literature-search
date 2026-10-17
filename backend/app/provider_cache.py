"""Bounded, persistent public metadata cache and provider retry deadlines."""

import hashlib
import json
import os
import sqlite3
import time
import zlib
from contextlib import contextmanager
from pathlib import Path

FRESH_SECONDS = 86400
RETAIN_SECONDS = 7 * 86400
MAX_BYTES = 32 * 1024 * 1024
MAX_ENTRY_BYTES = 8 * 1024 * 1024
MAX_STORED_BYTES = 2 * 1024 * 1024


def key(url: str, params: dict) -> str:
    payload = json.dumps([url, params], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@contextmanager
def connection():
    default = Path(__file__).resolve().parents[1] / "data" / "papers.sqlite3"
    path = Path(os.getenv("PAPER_DB_PATH", str(default))).with_name("provider-cache.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS responses (
                key TEXT PRIMARY KEY, saved_at REAL NOT NULL,
                content_type TEXT NOT NULL, body BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS providers (
                label TEXT PRIMARY KEY, retry_at REAL NOT NULL
            );
        """)
        columns = [row[1] for row in db.execute("PRAGMA table_info(providers)")]
        if "strikes" not in columns:
            db.execute("ALTER TABLE providers ADD COLUMN strikes INTEGER NOT NULL DEFAULT 0")
        yield db
    finally:
        db.close()


def get(cache_key: str):
    with connection() as db:
        row = db.execute("SELECT saved_at, content_type, body FROM responses WHERE key = ? AND saved_at >= ?",
                         (cache_key, time.time() - RETAIN_SECONDS)).fetchone()
        if not row:
            return None
        saved_at, content_type, body = row
        if body.startswith(b"PCZ1"):
            try:
                decoder = zlib.decompressobj()
                body = decoder.decompress(body[4:], MAX_ENTRY_BYTES + 1)
                if len(body) > MAX_ENTRY_BYTES or decoder.unconsumed_tail or not decoder.eof:
                    return None
            except zlib.error:
                return None
        return saved_at, content_type, body


def put(cache_key: str, content_type: str, body: bytes):
    if len(body) > MAX_ENTRY_BYTES:
        return
    body = b"PCZ1" + zlib.compress(body)
    if len(body) > MAX_STORED_BYTES:
        return
    with connection() as db, db:
        db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?)", (cache_key, time.time(), content_type, body))
        db.execute("DELETE FROM responses WHERE saved_at < ?", (time.time() - RETAIN_SECONDS,))
        rows = db.execute("SELECT key, length(body) FROM responses ORDER BY saved_at DESC, key").fetchall()
        used = 0
        for index, (entry_key, size) in enumerate(rows):
            used += size
            if index >= 128 or used > MAX_BYTES:
                db.execute("DELETE FROM responses WHERE key = ?", (entry_key,))


def retry_at(label: str) -> float:
    with connection() as db:
        row = db.execute("SELECT retry_at FROM providers WHERE label = ?", (label,)).fetchone()
        return row[0] if row else 0


def set_retry_at(label: str, deadline: float):
    with connection() as db, db:
        row = db.execute("SELECT strikes FROM providers WHERE label = ?", (label,)).fetchone()
        strikes = row[0] if row else 0
        db.execute(
            "INSERT OR REPLACE INTO providers (label, retry_at, strikes) VALUES (?, ?, ?)",
            (label, deadline, strikes),
        )


def bump_strike(label: str) -> int:
    with connection() as db, db:
        row = db.execute("SELECT retry_at, strikes FROM providers WHERE label = ?", (label,)).fetchone()
        retry_at = row[0] if row else 0
        strikes = (row[1] if row else 0) + 1
        db.execute(
            "INSERT OR REPLACE INTO providers (label, retry_at, strikes) VALUES (?, ?, ?)",
            (label, retry_at, strikes),
        )
        return strikes


def clear_strikes(label: str):
    with connection() as db, db:
        db.execute("UPDATE providers SET strikes = 0 WHERE label = ?", (label,))

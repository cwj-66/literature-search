"""Short-lived extraction checkpoints; drafts still require grounding and review."""

import hashlib
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar

from app.infrastructure.db import connection

log = logging.getLogger(__name__)
_keys: ContextVar[list[str] | None] = ContextVar("analysis_checkpoint_keys", default=None)


def _table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS analysis_step_cache (
        cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at REAL NOT NULL)""")


@contextmanager
def enabled():
    keys = []
    token = _keys.set(keys)
    try:
        yield
    except BaseException:
        # Failed/cancelled runs retain valid extraction steps for a bounded time.
        raise
    else:
        try:
            with connection() as db, db:
                _table(db)
                db.executemany("DELETE FROM analysis_step_cache WHERE cache_key = ?", ((key,) for key in keys))
        except (sqlite3.Error, OSError):
            log.warning("analysis checkpoint cleanup unavailable")
    finally:
        _keys.reset(token)


def key_for(messages, model: str, version: str) -> str:
    return hashlib.sha256(json.dumps([model, version, messages], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def get(key: str):
    keys = _keys.get()
    if keys is None:
        return None
    keys.append(key)
    try:
        with connection() as db:
            _table(db)
            row = db.execute("SELECT payload FROM analysis_step_cache WHERE cache_key = ? AND created_at > ?", (key, time.time() - 7 * 86400)).fetchone()
            return json.loads(row["payload"]) if row else None
    except (sqlite3.Error, OSError, ValueError):
        log.warning("analysis checkpoint read unavailable")
        return None


def put(key: str, output):
    if _keys.get() is None:
        return
    try:
        with connection() as db, db:
            _table(db)
            db.execute("DELETE FROM analysis_step_cache WHERE created_at < ?", (time.time() - 7 * 86400,))
            db.execute("INSERT OR REPLACE INTO analysis_step_cache VALUES (?, ?, ?)", (key, output.model_dump_json(), time.time()))
            db.execute("""DELETE FROM analysis_step_cache WHERE cache_key IN
                (SELECT cache_key FROM analysis_step_cache ORDER BY created_at DESC LIMIT -1 OFFSET 500)""")
    except (sqlite3.Error, OSError):
        log.warning("analysis checkpoint write unavailable")

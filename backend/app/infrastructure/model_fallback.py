"""Explicit quota-only model order, optionally persisted outside visitor data."""
import hashlib
import os
from app.infrastructure.config import value as provider_value
import sqlite3
from contextlib import contextmanager


def models():
    primary = provider_value('LLM_MODEL', '').strip() or 'qwen3.7-flash-2026-07-15'
    return list(dict.fromkeys([primary] + [s.strip() for s in provider_value('LLM_FALLBACK_MODELS', '').split(',') if s.strip()]))


@contextmanager
def state():
    from app.infrastructure.user_scope import enabled, database_path
    path = str(database_path().parent/'model-fallback.sqlite3') if enabled() else os.getenv('LLM_FALLBACK_STATE', '').strip()
    if not path:
        yield None
        return
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS model_fallback(chain TEXT PRIMARY KEY, position INTEGER NOT NULL)')
        yield db
    finally:
        db.close()


def chain_key(chain):
    # A deliberate provider/model configuration change starts a new chain.
    value = provider_value('DASHSCOPE_BASE_URL', '') + '\n' + '\n'.join(chain)
    return hashlib.sha256(value.encode()).hexdigest()


def position(db, chain):
    row = db.execute('SELECT position FROM model_fallback WHERE chain=?', (chain_key(chain),)).fetchone()
    return min(max(row[0], 0), len(chain)-1) if row else 0


def current():
    chain = models()
    with state() as db:
        return chain[position(db, chain)] if db is not None else chain[0]


def after_quota(failed):
    chain = models()
    with state() as db:
        if db is None:
            return chain[chain.index(failed)+1] if failed in chain[:-1] else None
        with db:
            db.execute('BEGIN IMMEDIATE')
            index = position(db, chain)
            # Concurrent failures of the same model advance once, never twice.
            if chain[index] == failed:
                if index == len(chain)-1:
                    return None
                index += 1
                db.execute('INSERT OR REPLACE INTO model_fallback VALUES(?,?)', (chain_key(chain), index))
            return chain[index]

"""Small server-side key/value settings stored in the paper database."""

from app.infrastructure.db import connection
import os

SECRET_NAMES = {'DASHSCOPE_API_KEY','SILICONFLOW_API_KEY','MINERU_API_TOKEN','SEMANTIC_SCHOLAR_API_KEY'}


def value(name: str, default: str = '') -> str:
    from app.infrastructure.user_scope import enabled
    stored = get('provider:' + name)
    if stored:
        return stored
    # Session-hosted users never inherit the host's paid credentials.
    if enabled() and name in SECRET_NAMES:
        return ''
    return os.getenv(name, default)


def get(key: str) -> str:
    with connection() as db:
        row = db.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""


def set(key: str, value: str) -> None:
    with connection() as db, db:
        if value:
            db.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES (?, ?)", (key, value))
        else:
            db.execute("DELETE FROM app_config WHERE key = ?", (key,))

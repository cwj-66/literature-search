"""Small server-side key/value settings stored in the paper database."""

from app.infrastructure.db import connection


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

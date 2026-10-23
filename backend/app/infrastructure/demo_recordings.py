"""Publish a new recorded demo once, without replacing visitor-owned chats."""
from contextlib import closing
from datetime import datetime, timezone
import json
import sqlite3

VERSION = "recorded-discovery-20261023-v1"


def install():
    from app.assistant_chats import ensure
    from app.infrastructure import demo
    from app.infrastructure.db import connection

    seed = demo.base_path().parent / "demo-seed" / "papers.sqlite3"
    if not seed.exists():
        return False
    with closing(sqlite3.connect(seed.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        if not source.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_chats'").fetchone():
            return False
        rows = source.execute("SELECT workspace_id,id,payload FROM assistant_chats WHERE deleted=0 ORDER BY id").fetchall()
    recordings = [(wid, cid, json.loads(raw)) for wid, cid, raw in rows
                  if json.loads(raw).get("recorded_demo", {}).get("kind") == "recorded_live_model_run"]
    if not recordings:
        return False
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        ensure(db)
        db.execute("CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)")
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM demo_sample_updates WHERE version=?", (VERSION,)).fetchone():
            return True
        for wid, cid, chat in recordings:
            if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (wid,)).fetchone():
                continue
            published_id = "demo-live-20261023-" + cid.removeprefix("demo-reviewed-")
            if db.execute("SELECT 1 FROM assistant_chats WHERE workspace_id=? AND id=?", (wid, published_id)).fetchone():
                continue  # Preserve edits, archiving and deletion tombstones.
            source_tasks = {t.get("task", {}).get("task_id") for t in chat.get("turns", [])} - {None}
            existing = [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM assistant_chats WHERE workspace_id=? AND deleted=0", (wid,))]
            if source_tasks and any(source_tasks.issubset(
                    {t.get("task", {}).get("task_id") for t in old.get("turns", [])}) for old in existing):
                continue  # New seed copies and the author's live runs already contain it.
            chat["id"] = published_id
            db.execute("INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)",
                       (wid, published_id, json.dumps(chat, ensure_ascii=False), 1, stamp))
        db.execute("INSERT INTO demo_sample_updates VALUES(?)", (VERSION,))
    return True

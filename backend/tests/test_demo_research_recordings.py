import json
import sqlite3
from pathlib import Path

from app.infrastructure import demo_research_recordings as recordings


def database():
    db = sqlite3.connect(":memory:")
    db.executescript('''
        CREATE TABLE papers(local_id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE workspace_papers(workspace_id TEXT,local_id TEXT);
        CREATE TABLE research_conversations(id TEXT PRIMARY KEY,workspace_id TEXT,title TEXT,paper_ids TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE research_messages(id TEXT,conversation_id TEXT,payload TEXT);
    ''')
    for i, ref in enumerate(["arxiv:2506.08938", "arxiv:2407.02485"]):
        db.execute("INSERT INTO papers VALUES(?,?)", (str(i), json.dumps({"id": ref})))
        db.execute("INSERT INTO workspace_papers VALUES(?,?)", ("pmvcf313hr4xt", str(i)))
    return db


def test_recordings_are_real_and_scoped():
    db = database()
    recordings.install_into(db)
    assert db.execute("SELECT COUNT(*) FROM research_conversations").fetchone()[0] == 3
    originals = json.loads((Path(recordings.__file__).parents[1] / "assets/research-demo.json").read_text(encoding="utf-8"))
    for item in originals:
        payload = json.loads(db.execute("SELECT payload FROM research_messages WHERE conversation_id=?", (item["id"],)).fetchone()[0])
        assert payload["response"] == item["message"]["response"]
        assert all(p["id"] in {"0", "1"} for p in payload["papers"])
        assert {e["paper_id"] for e in payload["response"]["evidence"]} <= {p["paper_id"] for p in payload["papers"]}


def test_deletions_edits_and_other_visitors_are_preserved():
    first, second = database(), database()
    recordings.install_into(first)
    recordings.install_into(second)
    first.execute("DELETE FROM research_conversations WHERE id LIKE '%simple'")
    first.execute("UPDATE research_conversations SET title='visitor edit'")
    recordings.install_into(first)
    assert first.execute("SELECT COUNT(*) FROM research_conversations").fetchone()[0] == 2
    assert first.execute("SELECT title FROM research_conversations").fetchone()[0] == "visitor edit"
    assert second.execute("SELECT COUNT(*) FROM research_conversations").fetchone()[0] == 3


def test_removed_papers_are_not_restored():
    db = database()
    db.execute("DELETE FROM workspace_papers")
    recordings.install_into(db)
    assert db.execute("SELECT COUNT(*) FROM research_conversations").fetchone()[0] == 0

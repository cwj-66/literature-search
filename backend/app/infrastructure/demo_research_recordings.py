"""Add reviewed real research runs once per visitor; never restore deletions."""
import json
from pathlib import Path

VERSION = "research-recordings-20261023-v1"


def install_into(db):
    db.execute("CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)")
    if db.execute("SELECT 1 FROM demo_sample_updates WHERE version=?", (VERSION,)).fetchone():
        return
    recordings = json.loads((Path(__file__).parents[1] / "assets" / "research-demo.json").read_text(encoding="utf-8"))
    wid = "pmvcf313hr4xt"
    members = {json.loads(row[1])["id"]: row[0] for row in db.execute(
        "SELECT p.local_id,p.data FROM papers p JOIN workspace_papers wp ON wp.local_id=p.local_id WHERE wp.workspace_id=?", (wid,))}
    for item in recordings:
        message = item["message"]
        if not all(p["paper_id"] in members for p in message["papers"]):
            continue  # Never recreate removed workspace papers.
        cid = item["id"]
        if db.execute("SELECT 1 FROM research_conversations WHERE id=?", (cid,)).fetchone():
            continue
        for paper in message["papers"]:
            paper["id"] = members[paper["paper_id"]]
        selected = [p["id"] for p in message["papers"]]
        stamp = message["created_at"]
        message["id"] = cid + "-message"
        message["recorded_demo"] = {"kind": "recorded_live_model_run", "model": "qwen3.8-2.4t-a95b"}
        db.execute("INSERT INTO research_conversations VALUES(?,?,?,?,?,?)",
                   (cid, wid, item["title"], json.dumps(selected), stamp, stamp))
        db.execute("INSERT INTO research_messages VALUES(?,?,?)",
                   (message["id"], cid, json.dumps(message, ensure_ascii=False)))
    db.execute("INSERT INTO demo_sample_updates VALUES(?)", (VERSION,))


def install():
    from app.infrastructure.db import connection
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        install_into(db)
    return True

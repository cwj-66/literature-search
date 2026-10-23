"""Install reviewed live discovery snapshots into the initial seed only.

Run inside the paper API container with a snapshot JSON path. Existing visitor
databases are never modified; each new visitor receives their own seed copy.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def install(source: Path, target: Path):
    target = target.resolve(strict=True)
    if target.name != "papers.sqlite3" or target.parent.name != "demo-seed":
        raise ValueError("Only the initial demo-seed database can be updated")
    snapshot = json.loads(source.read_text(encoding="utf-8"))
    workspace_id = snapshot["workspace_id"]
    if workspace_id not in {"pmvcf313hr4xt", "pmvci88eig1qi"}:
        raise ValueError("Not an original demo workspace")
    chats = snapshot["chats"]
    if [len(chat["turns"]) for chat in chats] != [1, 3, 5]:
        raise ValueError("Expected complete 1/3/5-round live conversations")
    for chat in chats:
        if chat.get("selectedIds"):
            raise ValueError("Discovery examples must not preselect private papers")
        for turn in chat["turns"]:
            task = turn["task"]
            if turn.get("pending") or task["status"] != "done" or not task.get("recommended"):
                raise ValueError("Incomplete/empty live results cannot become a demo")
    stamp = datetime.now(timezone.utc)
    backup = target.with_name("before-recorded-demo-" + stamp.strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
    with closing(sqlite3.connect(target)) as db:
        with closing(sqlite3.connect(backup)) as copy:
            db.backup(copy)
        db.execute("PRAGMA foreign_keys=ON")
        if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone():
            raise ValueError("Seed workspace does not exist")
        from app.assistant_chats import ensure
        with db:
            ensure(db)
            for index, original in enumerate(chats, 1):
                chat = dict(original)
                # Reuse initial-example IDs, so the normal installer preserves
                # these recorded conversations instead of adding old templates.
                chat["id"] = f"demo-reviewed-{index}"
                chat.pop("version", None)
                chat["recorded_demo"] = snapshot["provenance"]
                db.execute("""INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)
                    ON CONFLICT(workspace_id,id) DO UPDATE SET payload=excluded.payload,
                    version=assistant_chats.version+1,deleted=0,updated_at=excluded.updated_at""",
                    (workspace_id, chat["id"], json.dumps(chat, ensure_ascii=False), 1, stamp.isoformat()))
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Seed integrity check failed")
    print(json.dumps({"seed_updated": True, "workspace_id": workspace_id,
                      "rounds": [1, 3, 5], "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--seed", type=Path, default=Path("/app/data/demo-seed/papers.sqlite3"))
    args = parser.parse_args()
    install(args.snapshot, args.seed)

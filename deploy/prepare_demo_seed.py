"""Run inside the existing paper container; preserves the original database."""
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3

data = Path("/app/data")
seed = data / "demo-seed"
if (seed / "ready").exists():
    raise SystemExit("Demo seed already exists; inspect before replacing it")
seed.mkdir(exist_ok=True)
with closing(sqlite3.connect(data / "papers.sqlite3")) as source, closing(sqlite3.connect(seed / "papers.sqlite3")) as db:
    source.backup(db)
    db.execute("PRAGMA temp_store=MEMORY")
    db.execute("PRAGMA foreign_keys=ON")
    names = ("RAG研究", "大模型幻觉")
    rows = db.execute("SELECT id,name FROM workspaces WHERE name IN (?,?)", names).fetchall()
    if len(rows) != 2:
        raise SystemExit("Expected exactly the two author-selected sample workspaces")
    db.execute("DELETE FROM workspaces WHERE name NOT IN (?,?)", names)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("research_messages", "research_conversations", "assistant_chats", "research_tasks", "cancel_flags",
                  "call_cache", "agent_operations", "app_config", "analysis_step_cache"):
        if table in tables:
            db.execute('DELETE FROM "' + table + '"')
    # Do not replay historical processing jobs or automatic model calls for every visitor.
    db.execute("UPDATE analysis_tasks SET status='failed',error='示例任务未完成，可手动重试' WHERE status IN ('pending','preparing','analyzing')")
    db.execute("UPDATE relation_jobs SET status='ready' WHERE status IN ('queued','running')")
    if "paper_processing" in tables:
        db.execute("UPDATE paper_processing SET status='failed',message='可重新添加论文以重试处理' WHERE status IN ('pending','running')")
    db.commit()
    db.execute("VACUUM")
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("Demo seed workspaces:", rows)
    print("Sample membership:", db.execute("SELECT w.name,count(wp.local_id) FROM workspaces w LEFT JOIN workspace_papers wp ON wp.workspace_id=w.id GROUP BY w.id").fetchall())
if (data / "vectors.faiss").exists():
    shutil.copyfile(data / "vectors.faiss", seed / "vectors.faiss")
(seed / "ready").write_text("sanitized-demo-v1", encoding="ascii")
print("Seed bytes:", sum(p.stat().st_size for p in seed.iterdir()))

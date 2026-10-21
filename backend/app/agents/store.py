"""Research-task persistence: snapshots and run claims, control flags, call cache, operation ledger.

- research_tasks: public snapshot per task plus the run claim (status running/waiting/done/failed,
  updated_at doubles as the heartbeat of a running task).
- cancel_flags: pause/cancel requests that survive restarts.
- call_cache: replay cache for external calls; keys include AGENT_PROMPT_VERSION.
- agent_operations: ledger of side-effecting tool operations keyed by a stable operation ID
  (task + tool_call_id + full arguments), independent of prompt versions.
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agents.prompts import AGENT_PROMPT_VERSION
from app.infrastructure.db import connection

TASK_LEASE_SECONDS = int(os.getenv("RESEARCH_TASK_LEASE", "900"))


class TaskConflict(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checkpoint_path() -> Path:
    from app.infrastructure.demo import database_path
    base = database_path()
    return base.with_name("research-checkpoints.sqlite3")


# ---------------------------------------------------------------------------
# research_tasks
# ---------------------------------------------------------------------------


def ensure_tasks(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS research_tasks (
            task_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)


def save_task(task_id: str, thread_id: str, status: str, payload: dict) -> None:
    stamp = _now()
    with connection() as db, db:
        ensure_tasks(db)
        db.execute(
            """INSERT INTO research_tasks VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at""",
            (task_id, thread_id, status, json.dumps(payload, ensure_ascii=False, default=str), stamp, stamp),
        )


def get_task(task_id: str) -> dict | None:
    with connection() as db:
        ensure_tasks(db)
        row = db.execute("SELECT * FROM research_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        # 权威列（status 等）覆盖 payload 中的同名快照字段，恢复占用后对外仍显示 running。
        return {
            **payload,
            "task_id": row["task_id"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def list_tasks(limit: int = 50) -> list[dict]:
    with connection() as db:
        ensure_tasks(db)
        rows = db.execute("SELECT * FROM research_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    tasks = []
    for row in rows:
        payload = json.loads(row["payload"])
        interrupt = payload.get("interrupt")
        tasks.append({
            "task_id": row["task_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "goal": payload.get("goal"),
            "stop_reason": payload.get("stop_reason"),
            "parent_task_id": payload.get("parent_task_id"),
            "interrupt": interrupt.get("type") if isinstance(interrupt, dict) else None,
        })
    return tasks


def _stale(row) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=TASK_LEASE_SECONDS)).isoformat()
    return row["updated_at"] < cutoff


def claim_new(task_id: str, payload: dict) -> None:
    """Create the task row as running before execution starts; refuse if the task is live or waiting."""
    stamp = _now()
    with connection() as db, db:
        ensure_tasks(db)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status, updated_at FROM research_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row and row["status"] == "waiting":
            raise TaskConflict("任务正在等待用户确认，请使用 resume 继续")
        if row and row["status"] == "running" and not _stale(row):
            raise TaskConflict("任务正在执行，不能重复启动")
        db.execute(
            """INSERT INTO research_tasks VALUES (?, ?, 'running', ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET status='running', payload=excluded.payload, updated_at=excluded.updated_at""",
            (task_id, task_id, json.dumps(payload, ensure_ascii=False, default=str), stamp, stamp),
        )


def claim_resume(task_id: str) -> str | None:
    """Atomically move a resumable task to running. Returns the previous status (None if no row).

    Resumable: waiting (HITL / pause), failed (retry from last checkpoint), or running whose
    heartbeat expired (process died). A live running task or a finished task is rejected.
    """
    with connection() as db, db:
        ensure_tasks(db)
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status, updated_at FROM research_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        status = row["status"]
        if status == "running" and not _stale(row):
            raise TaskConflict("任务正在执行，不能重复恢复")
        if status not in ("waiting", "failed", "running"):
            raise TaskConflict("任务已结束，无需恢复；如需在后台处理完成后继续，请使用 continue")
        db.execute("UPDATE research_tasks SET status='running', updated_at=? WHERE task_id=?", (_now(), task_id))
        return status


def release_claim(task_id: str, status: str | None) -> None:
    if status is None:
        return
    with connection() as db, db:
        ensure_tasks(db)
        db.execute("UPDATE research_tasks SET status=? WHERE task_id=?", (status, task_id))


def touch_task(task_id: str) -> None:
    try:
        with connection() as db, db:
            ensure_tasks(db)
            db.execute("UPDATE research_tasks SET updated_at=? WHERE task_id=? AND status='running'", (_now(), task_id))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# cancel_flags
# ---------------------------------------------------------------------------


def ensure_flags(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cancel_flags (
            task_id TEXT PRIMARY KEY,
            flag TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)


def set_flag(task_id: str, flag: str) -> None:
    with connection() as db, db:
        ensure_flags(db)
        db.execute(
            """INSERT INTO cancel_flags VALUES (?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET flag=excluded.flag, created_at=excluded.created_at""",
            (task_id, flag, _now()),
        )


def flag_of(task_id: str | None) -> str | None:
    if not task_id:
        return None
    try:
        with connection() as db:
            ensure_flags(db)
            row = db.execute("SELECT flag FROM cancel_flags WHERE task_id = ?", (task_id,)).fetchone()
            return row["flag"] if row else None
    except Exception:
        return None


def clear_flag(task_id: str | None) -> None:
    if not task_id:
        return
    try:
        with connection() as db, db:
            ensure_flags(db)
            db.execute("DELETE FROM cancel_flags WHERE task_id = ?", (task_id,))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# call_cache
# ---------------------------------------------------------------------------


def ensure_cache(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS call_cache (
            cache_key TEXT PRIMARY KEY,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)


def cache_key(kind: str, payload: dict) -> str:
    from app.infrastructure.llm import model_name
    material = {"v": AGENT_PROMPT_VERSION, "kind": kind, **payload, "model": model_name()}
    text = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_get(key: str):
    try:
        with connection() as db:
            ensure_cache(db)
            row = db.execute("SELECT result FROM call_cache WHERE cache_key = ?", (key,)).fetchone()
            return json.loads(row["result"]) if row else None
    except Exception:
        return None


def cache_put(key: str, result) -> None:
    try:
        with connection() as db, db:
            ensure_cache(db)
            db.execute("INSERT OR REPLACE INTO call_cache VALUES (?, ?, ?)",
                       (key, json.dumps(result, ensure_ascii=False, default=str), _now()))
    except Exception:
        pass


async def cached_call(kind: str, payload: dict, producer):
    """命中时直接返回缓存结果，不执行 producer。返回 (result, cache_hit)；producer 异常不写缓存。"""
    key = cache_key(kind, payload)
    hit = cache_get(key)
    if hit is not None:
        return hit, True
    result = producer()
    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
        result = await result
    cache_put(key, result)
    return result, False


# ---------------------------------------------------------------------------
# agent_operations（副作用操作账本）
# ---------------------------------------------------------------------------


def ensure_operations(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_operations (
            op_key TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            op_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            args TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_operations_task ON agent_operations(task_id);
    """)


def operation_key(task_id: str, op_id: str, tool: str, args: dict) -> str:
    text = json.dumps({"task": task_id, "op": op_id, "tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def operation_get(key: str):
    with connection() as db:
        ensure_operations(db)
        row = db.execute("SELECT result FROM agent_operations WHERE op_key = ?", (key,)).fetchone()
        return json.loads(row["result"]) if row else None


def operation_put(key: str, task_id: str, op_id: str, tool: str, args: dict, result) -> None:
    with connection() as db, db:
        ensure_operations(db)
        db.execute(
            "INSERT OR IGNORE INTO agent_operations VALUES (?, ?, ?, ?, ?, ?, ?)",
            (key, task_id, op_id, tool, json.dumps(args, ensure_ascii=False, sort_keys=True, default=str),
             json.dumps(result, ensure_ascii=False, default=str), _now()),
        )


# ---------------------------------------------------------------------------
# 运行期限（进程内，仅覆盖当前执行段；等待用户确认的时间不计入）
# ---------------------------------------------------------------------------

_DEADLINES: dict[str, float] = {}


def set_deadline(task_id: str, seconds: float | None) -> None:
    if seconds:
        _DEADLINES[task_id] = time.monotonic() + float(seconds)


def clear_deadline(task_id: str) -> None:
    _DEADLINES.pop(task_id, None)


def deadline_passed(task_id: str | None) -> bool:
    deadline = _DEADLINES.get(task_id or "")
    return deadline is not None and time.monotonic() > deadline

"""Analysis tasks and successful results stored in the local SQLite library."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from app.infrastructure.db import connection
from app.schemas import AnalysisFields


class Decision:
    def __init__(self, task_row, created: bool, reused: bool, result_row):
        self.task_row = task_row
        self.created = created
        self.reused = reused
        self.result_row = result_row

    def payload(self) -> dict:
        task = public_task(self.task_row, self.reused)
        return {"task": task, "result": public_result(self.result_row)}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_filter(aliases: set[str], local_id: str | None, table: str = "") -> tuple[str, list]:
    keys = [key for key in aliases if key]
    if local_id and local_id not in keys:
        keys.append(local_id)
    if not keys:
        raise ValueError("缺少论文标识")
    prefix = f"{table}." if table else ""
    marks = ",".join("?" * len(keys))
    return f"({prefix}paper_ref IN ({marks}) OR {prefix}local_id IN ({marks}))", [*keys, *keys]


def public_task(row, reused: bool = False) -> dict | None:
    if not row:
        return None
    data = row if isinstance(row, dict) else dict(row)
    return {
        "task_id": data["task_id"],
        "status": data["status"],
        "error": data["error"],
        "created_at": data["created_at"],
        "finished_at": data["finished_at"],
        "reused": reused,
    }


def public_result(row) -> dict | None:
    if not row:
        return None
    data = row if isinstance(row, dict) else dict(row)
    payload = json.loads(data["payload"])
    evidence = json.loads(data["evidence"])
    sections = []
    for item in evidence.get("sections") or []:
        if isinstance(item, dict):
            sections.append({"title": str(item.get("title") or "摘录"), "excerpt": str(item.get("excerpt") or "")})
    payload.update({
        "analysis_basis": data["analysis_basis"],
        "generated_at": data["generated_at"],
        "model_name": data["model_name"],
        "prompt_version": data["prompt_version"],
        "degradation_reason": data["degradation_reason"],
        "source_kind": data["source_kind"],
        "source_url": data["source_url"],
        "truncated": bool(evidence.get("truncated")),
        "evidence_sections": sections,
    })
    return payload


def _latest_result(db, aliases: set[str], local_id: str | None):
    sql, params = task_filter(aliases, local_id, "t")
    return db.execute(f"""SELECT r.* FROM analysis_results r JOIN analysis_tasks t ON t.task_id = r.task_id
        WHERE {sql} ORDER BY r.generated_at DESC, r.result_id DESC LIMIT 1""", params).fetchone()


def create_task(paper_ref: str, aliases: set[str], local_id: str | None, reanalyze: bool, model_name: str, prompt_version: str) -> Decision:
    aliases = set(aliases)
    aliases.add(paper_ref)
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        sql, params = task_filter(aliases, local_id)
        active = db.execute(f"""SELECT * FROM analysis_tasks WHERE status IN ('pending', 'preparing', 'analyzing') AND {sql}
            ORDER BY created_at DESC LIMIT 1""", params).fetchone()
        result = _latest_result(db, aliases, local_id)
        if active:
            return Decision(dict(active), False, True, dict(result) if result else None)
        if result and not reanalyze:
            task = db.execute(f"SELECT * FROM analysis_tasks WHERE {sql} ORDER BY created_at DESC LIMIT 1", params).fetchone()
            return Decision(dict(task) if task else None, False, False, dict(result))
        task_id = uuid4().hex
        db.execute("""INSERT INTO analysis_tasks
            (task_id, paper_ref, local_id, status, error, model_name, prompt_version, reanalyze, created_at)
            VALUES (?, ?, ?, 'pending', NULL, ?, ?, ?, ?)""",
            (task_id, paper_ref, local_id, model_name, prompt_version, int(reanalyze), now()))
        created = db.execute("SELECT * FROM analysis_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Decision(dict(created), True, False, dict(result) if result else None)


def get_state(aliases: set[str], local_id: str | None) -> tuple[dict | None, dict | None]:
    with connection() as db:
        sql, params = task_filter(aliases, local_id)
        task = db.execute(f"SELECT * FROM analysis_tasks WHERE {sql} ORDER BY created_at DESC, task_id DESC LIMIT 1", params).fetchone()
        return public_task(task), public_result(_latest_result(db, aliases, local_id))


def get_task(task_id: str) -> dict | None:
    with connection() as db:
        row = db.execute("SELECT * FROM analysis_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def recover() -> list[str]:
    with connection() as db, db:
        db.execute("""UPDATE analysis_tasks SET status = 'failed', error = ?, finished_at = ?
            WHERE status IN ('preparing', 'analyzing')""", ("分析因服务重启中断，请重试", now()))
        rows = db.execute("SELECT task_id FROM analysis_tasks WHERE status = 'pending' ORDER BY created_at, task_id").fetchall()
        return [row["task_id"] for row in rows]


def transition(task_id: str, expect: str, status: str) -> bool:
    with connection() as db, db:
        if status == "preparing":
            cursor = db.execute("UPDATE analysis_tasks SET status = ?, started_at = ? WHERE task_id = ? AND status = ?", (status, now(), task_id, expect))
        else:
            cursor = db.execute("UPDATE analysis_tasks SET status = ? WHERE task_id = ? AND status = ?", (status, task_id, expect))
        return cursor.rowcount == 1


def fail(task_id: str, message: str) -> None:
    with connection() as db, db:
        db.execute("""UPDATE analysis_tasks SET status = 'failed', error = ?, finished_at = ?
            WHERE task_id = ? AND status IN ('pending', 'preparing', 'analyzing')""", (message[:500], now(), task_id))


def attach_paper(task_id: str, local_id: str) -> None:
    with connection() as db, db:
        db.execute("UPDATE analysis_tasks SET local_id = ? WHERE task_id = ?", (local_id, task_id))


def save_success(task_id: str, local_id: str, fields: AnalysisFields, prepared, model_name: str, prompt_version: str) -> bool:
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status FROM analysis_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row or row["status"] != "analyzing":
            return False
        stamp = now()
        db.execute("""INSERT INTO analysis_results (
            result_id, task_id, local_id, payload, analysis_basis, degradation_reason, source_kind, source_url,
            model_name, prompt_version, evidence, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            uuid4().hex, task_id, local_id, fields.model_dump_json(), fields.analysis_basis, prepared.degradation_reason,
            prepared.source_kind, prepared.source_url, model_name, prompt_version,
            json.dumps(prepared.evidence, ensure_ascii=False), stamp,
        ))
        db.execute("UPDATE analysis_tasks SET status = 'success', error = NULL, finished_at = ? WHERE task_id = ?", (stamp, task_id))
        return True


def create_tool_task(paper_ref: str, local_id: str, model_name: str, prompt_version: str) -> str:
    """Expose synchronous tool progress to the same readers as queued analysis."""
    task_id = uuid4().hex
    stamp = now()
    with connection() as db, db:
        db.execute("""INSERT INTO analysis_tasks
            (task_id, paper_ref, local_id, status, model_name, prompt_version, created_at, started_at)
            VALUES (?, ?, ?, 'preparing', ?, ?, ?, ?)""",
            (task_id, paper_ref, local_id, model_name, prompt_version, stamp, stamp))
    return task_id

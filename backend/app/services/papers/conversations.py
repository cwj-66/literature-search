"""Saved conversations and immutable per-message paper scopes."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from app.infrastructure.db import connection
from app.services.papers import workspaces
from app.tools.research_scope import ToolContext, resolve


class ConversationNotFound(ValueError):
    pass


class ConversationBusy(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def _require(db, workspace_id, conversation_id):
    row = db.execute("SELECT * FROM research_conversations WHERE id = ? AND workspace_id = ?",
                     (conversation_id, workspace_id)).fetchone()
    if row is None:
        raise ConversationNotFound("这个对话不属于当前工作区，或已被删除")
    return row


def _summary(row):
    return {key: row[key] for key in ("id", "workspace_id", "title", "created_at", "updated_at")} | {
        "paper_ids": json.loads(row["paper_ids"]),
    }


def selected(workspace_id, refs):
    if not refs:
        workspaces.require(workspace_id)
        return []
    return resolve(ToolContext(workspace_id=workspace_id, scope="selected", selected_paper_ids=refs))


def list_all(workspace_id):
    workspaces.require(workspace_id)
    with connection() as db:
        return [_summary(row) for row in db.execute(
            "SELECT * FROM research_conversations WHERE workspace_id = ? ORDER BY updated_at DESC, id",
            (workspace_id,))]


def get(workspace_id, conversation_id):
    with connection() as db:
        result = _summary(_require(db, workspace_id, conversation_id))
        result["messages"] = [json.loads(row["payload"]) for row in db.execute(
            "SELECT payload FROM research_messages WHERE conversation_id = ? ORDER BY rowid", (conversation_id,))]
        return result


def create(workspace_id, title, refs):
    papers = selected(workspace_id, refs)
    stamp, cid = now(), uuid4().hex
    with connection() as db, db:
        db.execute("INSERT INTO research_conversations VALUES (?, ?, ?, ?, ?, ?)",
                   (cid, workspace_id, title, json.dumps([p.local_id for p in papers]), stamp, stamp))
    return get(workspace_id, cid)


def update(workspace_id, conversation_id, title=None, refs=None):
    papers = selected(workspace_id, refs) if refs is not None else None
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _require(db, workspace_id, conversation_id)
        db.execute("UPDATE research_conversations SET title = ?, paper_ids = ?, updated_at = ? WHERE id = ?",
                   (title if title is not None else row["title"],
                    json.dumps([p.local_id for p in papers]) if papers is not None else row["paper_ids"], now(), conversation_id))
    return get(workspace_id, conversation_id)


def delete(workspace_id, conversation_id):
    with connection() as db, db:
        _require(db, workspace_id, conversation_id)
        db.execute("DELETE FROM research_conversations WHERE id = ?", (conversation_id,))
    return {"status": "deleted"}


def begin(workspace_id, conversation_id, question, refs, request_id):
    """Persist before generation; a retry of the same request cannot duplicate it."""
    papers = selected(workspace_id, refs)
    if not papers:
        raise ValueError("请先选择当前工作区中的论文")
    snapshot = [{"id": p.local_id, "paper_id": p.id, "title": p.title} for p in papers]
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = _require(db, workspace_id, conversation_id)
        existing = db.execute("SELECT payload FROM research_messages WHERE conversation_id = ? AND id = ?",
                              (conversation_id, request_id)).fetchone()
        if existing:
            message = json.loads(existing["payload"])
            if message["question"] != question or message["papers"] != snapshot:
                raise ValueError("同一请求标识不能用于不同问题或论文范围")
            if message["status"] == "pending":
                raise ConversationBusy("这次提问仍在生成，请稍后重新打开对话")
            return message, papers, True
        pending = db.execute("SELECT payload FROM research_messages WHERE conversation_id = ?", (conversation_id,)).fetchall()
        if any(json.loads(item["payload"])["status"] == "pending" for item in pending):
            raise ConversationBusy("这个对话还有回答正在生成，请完成后继续")
        message = {"id": request_id, "question": question, "papers": snapshot,
                   "status": "pending", "created_at": now(), "response": None, "error": None}
        db.execute("INSERT INTO research_messages VALUES (?, ?, ?)", (request_id, conversation_id, json.dumps(message, ensure_ascii=False)))
        title = question[:60] if row["title"] == "新对话" else row["title"]
        db.execute("UPDATE research_conversations SET title = ?, paper_ids = ?, updated_at = ? WHERE id = ?",
                   (title, json.dumps([p.local_id for p in papers]), now(), conversation_id))
    return message, papers, False


def finish(workspace_id, conversation_id, message, response=None, error=None):
    message = {**message, "status": "failed" if error else "completed", "response": response, "error": error}
    with connection() as db, db:
        _require(db, workspace_id, conversation_id)
        db.execute("UPDATE research_messages SET payload = ? WHERE conversation_id = ? AND id = ?",
                   (json.dumps(message, ensure_ascii=False), conversation_id, message["id"]))
        db.execute("UPDATE research_conversations SET updated_at = ? WHERE id = ?", (now(), conversation_id))
    return message


def recover():
    """Pending requests cannot survive a server restart."""
    with connection() as db, db:
        for row in db.execute("SELECT id, conversation_id, payload FROM research_messages").fetchall():
            message = json.loads(row["payload"])
            if message["status"] == "pending":
                message.update(status="failed", error="服务已重启，这次回答未完成，请重新提问。")
                db.execute("UPDATE research_messages SET payload = ? WHERE conversation_id = ? AND id = ?",
                           (json.dumps(message, ensure_ascii=False), row["conversation_id"], row["id"]))

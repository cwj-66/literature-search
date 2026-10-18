"""Durable assistant conversations; separate from legacy evidence-QA conversations."""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.infrastructure.db import connection
from app.services.papers import workspaces

router = APIRouter(prefix="/api/workspaces/{workspace_id}/assistant-chats")


class Chat(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=120)
    titleEdited: bool = False
    pinned: bool = False
    archived: bool = False
    createdAt: int = 0
    updatedAt: int = 0
    selectedIds: list[str] = Field(default_factory=list, max_length=500)
    turns: list[dict] = Field(default_factory=list, max_length=2000)
    version: int = Field(0, ge=0)


class Import(BaseModel):
    chats: list[Chat] = Field(max_length=500)


class Version(BaseModel):
    version: int = Field(ge=1)


def ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS assistant_chats (
        workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        id TEXT NOT NULL, payload TEXT NOT NULL, version INTEGER NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        PRIMARY KEY(workspace_id,id))""")


def payload(chat):
    data = chat.model_dump(exclude={"version"})
    data["title"] = data["title"].strip()
    if not data["title"]:
        raise HTTPException(422, "对话名称不能为空")
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw.encode("utf-8")) > 8 * 1024 * 1024:
        raise HTTPException(413, "对话超过 8MB，请先导出并新建对话")
    return raw


def read_all(workspace_id):
    workspaces.require(workspace_id)
    with connection() as db:
        ensure(db)
        rows = db.execute("SELECT * FROM assistant_chats WHERE workspace_id=? ORDER BY updated_at DESC", (workspace_id,)).fetchall()
    return {"chats": [{**json.loads(r["payload"]), "version": r["version"]} for r in rows if not r["deleted"]],
            "deleted_ids": [r["id"] for r in rows if r["deleted"]]}


def write(workspace_id, chat, import_only=False):
    raw = payload(chat)
    with connection() as db, db:
        ensure(db)
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace_id,)).fetchone():
            raise workspaces.WorkspaceNotFound("工作区不存在")
        row = db.execute("SELECT version,deleted FROM assistant_chats WHERE workspace_id=? AND id=?", (workspace_id, chat.id)).fetchone()
        if row and import_only:
            return
        if row and (row["deleted"] or row["version"] != chat.version):
            raise HTTPException(409, "此对话已在其他页面更新或删除。当前记录保留在浏览器，请导出后重新载入。")
        if not row and chat.version:
            raise HTTPException(409, "对话版本已变化，请重新载入")
        version = (row["version"] if row else 0) + 1
        db.execute("""INSERT INTO assistant_chats VALUES (?,?,?,?,0,?)
            ON CONFLICT(workspace_id,id) DO UPDATE SET payload=excluded.payload,
            version=excluded.version,updated_at=excluded.updated_at""",
            (workspace_id, chat.id, raw, version, datetime.now(timezone.utc).isoformat()))
    return {**json.loads(raw), "version": version}


def remove(workspace_id, chat_id, version):
    workspaces.require(workspace_id)
    with connection() as db, db:
        ensure(db)
        cursor = db.execute("""UPDATE assistant_chats SET deleted=1,payload='{}',version=version+1
            WHERE workspace_id=? AND id=? AND version=? AND deleted=0""", (workspace_id, chat_id, version))
        if cursor.rowcount != 1:
            raise HTTPException(409, "对话已变化，请重新载入后再删除")
    return {"deleted": chat_id}


async def call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except workspaces.WorkspaceNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(503, "对话存储暂不可用，浏览器记录已保留") from None


@router.get("")
async def listing(workspace_id: str):
    return await call(read_all, workspace_id)


@router.post("/import")
async def importing(workspace_id: str, body: Import):
    await call(workspaces.require, workspace_id)
    for chat in body.chats:
        await call(write, workspace_id, chat, True)
    return await call(read_all, workspace_id)


@router.post("/save")
async def saving(workspace_id: str, body: Chat):
    return await call(write, workspace_id, body)


@router.post("/{chat_id}/delete")
async def deleting(workspace_id: str, chat_id: str, body: Version):
    return await call(remove, workspace_id, chat_id, body.version)

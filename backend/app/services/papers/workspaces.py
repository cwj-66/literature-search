"""工作区注册和论文成员关系；阅读状态、笔记继续由浏览器保存。"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

from app.infrastructure.db import connection
from app.services.papers import library


class WorkspaceNotFound(ValueError):
    pass


class RegisterWorkspaceRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("工作区名称不能为空")
        return value.strip()


class PaperReferences(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("paper_ids")
    @classmethod
    def valid_references(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("论文标识不能为空或超过 500 字符")
        return [value.strip() for value in values]


def register(workspace_id: str, name: str) -> dict:
    request = RegisterWorkspaceRequest(workspace_id=workspace_id, name=name)
    with connection() as db, db:
        db.execute("""INSERT INTO workspaces VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name = excluded.name""",
            (request.workspace_id, request.name, datetime.now(timezone.utc).isoformat()))
    return {"workspace_id": request.workspace_id, "name": request.name}


def require(workspace_id: str) -> None:
    with connection() as db:
        if not db.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone():
            raise WorkspaceNotFound("指定工作区不存在，请先打开或注册该工作区")


def list_workspaces() -> list[dict]:
    with connection() as db:
        return [dict(row) for row in db.execute("SELECT id AS workspace_id, name FROM workspaces ORDER BY created_at, id")]


def add(workspace_id: str, local_id: str) -> bool:
    with connection() as db, db:
        # 与写入处于同一事务，确保工作区不存在时不会创建虚假的关联。
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM workspaces WHERE id = ?", (workspace_id,)).fetchone():
            raise WorkspaceNotFound("指定工作区不存在")
        return db.execute("INSERT OR IGNORE INTO workspace_papers VALUES (?, ?, ?)",
            (workspace_id, local_id, datetime.now(timezone.utc).isoformat())).rowcount == 1


def list_papers(workspace_id: str) -> dict:
    require(workspace_id)
    with connection() as db:
        rows = db.execute("""SELECT p.*, wp.added_at FROM workspace_papers wp
            JOIN papers p ON p.local_id = wp.local_id WHERE wp.workspace_id = ?
            ORDER BY wp.added_at, p.local_id""", (workspace_id,)).fetchall()
        return {"workspace_id": workspace_id, "papers": [
            {"paper": library.as_paper(row).model_dump(mode="json"), "added_at": row["added_at"]} for row in rows
        ]}


def remove(workspace_id: str, paper_ids: list[str]) -> dict:
    require(workspace_id)
    with connection() as db, db:
        for ref in paper_ids:
            db.execute("""DELETE FROM workspace_papers WHERE workspace_id = ? AND
                (local_id = ? OR local_id IN (SELECT local_id FROM paper_keys WHERE key = ?))""",
                (workspace_id, ref, ref))
    return {"workspace_id": workspace_id, "status": "completed"}


def delete(workspace_id: str) -> dict:
    with connection() as db, db:
        db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    return {"workspace_id": workspace_id, "status": "completed"}

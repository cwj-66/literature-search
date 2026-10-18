"""Workspace-scoped research conversations."""

import asyncio
import logging
import sqlite3
from contextlib import suppress

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.infrastructure import llm
from app.services.analysis import attribution
from app.services.papers import conversations, workspaces
from app.services.retrieval import hybrid

router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations")


class CreateConversation(BaseModel):
    title: str = Field("新对话", min_length=1, max_length=120)
    paper_ids: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("对话名称不能为空")
        return value.strip()


class UpdateConversation(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    paper_ids: list[str] | None = Field(None, max_length=40)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("对话名称不能为空")
        return value.strip() if value is not None else value


class ConversationQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    paper_ids: list[str] = Field(min_length=1, max_length=40)
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


async def stored(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (conversations.ConversationNotFound, workspaces.WorkspaceNotFound) as exc:
        raise HTTPException(404, str(exc)) from None
    except conversations.ConversationBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(503, "对话保存暂不可用，请稍后重试") from None


@router.get("")
async def list_conversations(workspace_id: str):
    return await stored(conversations.list_all, workspace_id)


@router.post("")
async def create_conversation(workspace_id: str, payload: CreateConversation):
    return await stored(conversations.create, workspace_id, payload.title, payload.paper_ids)


@router.get("/{conversation_id}")
async def get_conversation(workspace_id: str, conversation_id: str):
    return await stored(conversations.get, workspace_id, conversation_id)


@router.post("/{conversation_id}")
async def update_conversation(workspace_id: str, conversation_id: str, payload: UpdateConversation):
    return await stored(conversations.update, workspace_id, conversation_id, payload.title, payload.paper_ids)


@router.post("/{conversation_id}/delete")
async def delete_conversation(workspace_id: str, conversation_id: str):
    return await stored(conversations.delete, workspace_id, conversation_id)


@router.post("/{conversation_id}/messages")
async def ask_conversation(workspace_id: str, conversation_id: str, payload: ConversationQuestion):
    message, papers, replayed = await stored(conversations.begin, workspace_id, conversation_id,
                                           payload.question, payload.paper_ids, payload.request_id)
    if replayed:
        return message
    try:
        thread = await stored(conversations.get, workspace_id, conversation_id)
        allowed = {paper.id for paper in papers}
        # A range change cannot smuggle answers or questions from excluded papers into the new context.
        history = [m["question"] for m in thread["messages"] if m["status"] == "completed"
                   and {p["paper_id"] for p in m["papers"]} <= allowed][-3:]
        await asyncio.to_thread(hybrid.index_abstracts, papers)
        response = await attribution.answer_question(payload.question, [p.id for p in papers], history=history)
        return await stored(conversations.finish, workspace_id, conversation_id, message, response=response)
    except asyncio.CancelledError:
        with suppress(Exception):
            await asyncio.shield(asyncio.to_thread(conversations.finish, workspace_id, conversation_id,
                                                 message, error="回答已中断，请重新提问。"))
        raise
    except Exception as exc:
        logging.exception("conversation answer failed")
        error = exc.message if isinstance(exc, llm.AnalysisError) else "这次回答未完成，请重试。"
        return await stored(conversations.finish, workspace_id, conversation_id, message, error=error)

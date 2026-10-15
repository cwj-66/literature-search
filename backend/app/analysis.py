"""Create and run one paper analysis task at a time."""

import asyncio
import logging
import re
import sqlite3
from urllib.parse import unquote

import httpx

from . import analysis_store as store
from . import fulltext, llm
from .library import get_paper, normalize_reference, save_paper
from .search import SourceFailure, fetch_paper, identity_keys

log = logging.getLogger(__name__)
_queue: asyncio.Queue | None = None


class ResolveError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class Target:
    def __init__(self, paper_ref: str, aliases: set[str], local_id: str | None):
        self.paper_ref = paper_ref
        self.aliases = aliases
        self.local_id = local_id


def resolve_target(raw: str) -> Target:
    raw = unquote(raw).strip()
    if not raw or len(raw) > 500:
        raise ResolveError(400, "请输入有效的论文标识")
    try:
        paper = get_paper(raw)
    except (sqlite3.Error, OSError) as exc:
        raise ResolveError(503, "本地分析库暂不可用，请稍后重试") from exc
    if paper:
        return Target(paper.local_id, identity_keys(paper) | {paper.id, paper.local_id, raw}, paper.local_id)
    try:
        ref = normalize_reference(raw)
    except ValueError as exc:
        if re.fullmatch(r"[0-9a-fA-F]{32}", raw):
            raise ResolveError(404, "论文尚未加入本地分析库") from None
        raise ResolveError(400, str(exc)) from None
    return Target(ref, {ref}, None)


def get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def enqueue(task_id: str) -> None:
    await get_queue().put(task_id)


async def ensure_paper(task: dict):
    local_id = task.get("local_id")
    if local_id:
        paper = await asyncio.to_thread(get_paper, local_id)
        if paper:
            return paper
    ref = task["paper_ref"]
    try:
        reference = ref if ref.startswith(("arxiv:", "doi:", "s2:")) else normalize_reference(ref)
    except ValueError:
        raise llm.AnalysisError("论文尚未加入本地分析库") from None
    if not str(reference).startswith(("arxiv:", "doi:", "s2:")):
        raise llm.AnalysisError("论文尚未加入本地分析库")
    try:
        fetched = await fetch_paper(reference)
    except SourceFailure as exc:
        raise llm.AnalysisError(exc.message) from exc
    except httpx.TimeoutException:
        raise llm.AnalysisError("论文详情请求超时") from None
    except httpx.HTTPError:
        raise llm.AnalysisError("论文详情请求失败") from None
    if fetched is None:
        raise llm.AnalysisError("没有找到这篇论文")
    try:
        saved, _ = await asyncio.to_thread(save_paper, fetched, reference)
    except (sqlite3.Error, OSError) as exc:
        raise llm.AnalysisError("本地分析库暂不可用，请稍后重试") from exc
    await asyncio.to_thread(store.attach_paper, task["task_id"], saved.local_id)
    return saved


async def run_task(task_id: str) -> None:
    task = await asyncio.to_thread(store.get_task, task_id)
    if not task or task["status"] != "pending":
        return
    if not await asyncio.to_thread(store.transition, task_id, "pending", "preparing"):
        return
    try:
        task = await asyncio.to_thread(store.get_task, task_id)
        paper = await ensure_paper(task)
        prepared = await fulltext.prepare(paper)
        if prepared is None:
            raise llm.AnalysisError("没有可用的论文全文或摘要，无法分析")
        if not await asyncio.to_thread(store.transition, task_id, "preparing", "analyzing"):
            return
        fields = await llm.analyze(paper, prepared)
        current = await asyncio.to_thread(store.get_task, task_id)
        if not current or not current.get("local_id"):
            raise llm.AnalysisError("论文尚未加入本地分析库")
        saved = await asyncio.to_thread(store.save_success, task_id, current["local_id"], fields, prepared, current["model_name"], current["prompt_version"])
        if not saved:
            await asyncio.to_thread(store.fail, task_id, "分析失败，请重试")
    except llm.AnalysisError as exc:
        await asyncio.to_thread(store.fail, task_id, exc.message)
    except Exception:
        log.exception("analysis task %s failed", task_id)
        await asyncio.to_thread(store.fail, task_id, "分析失败，请重试")


async def worker(queue: asyncio.Queue) -> None:
    while True:
        task_id = await queue.get()
        try:
            await run_task(task_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("analysis worker failed for %s", task_id)
            await asyncio.to_thread(store.fail, task_id, "分析失败，请重试")
        finally:
            queue.task_done()

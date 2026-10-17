"""LangGraph research agent wrapping existing search, fulltext and analysis tools."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict
from uuid import uuid4

from . import attribution, fulltext, hybrid, library, llm, search
from .library import connection
from .schemas import PaperOut

MAX_EXTRA_SEARCH = 2
MAX_API = 8
MAX_LLM = 12
MAX_DEEP = 5


class AgentState(TypedDict, total=False):
    goal: str
    plan: list
    plan_version: int
    queries: list
    query_index: int
    papers: list
    read_ids: list
    evidence_ids: list
    gaps: list
    events: list
    api_calls: int
    llm_calls: int
    extra_search: int
    deep_reads: int
    max_extra_search: int
    max_api_calls: int
    max_llm_calls: int
    max_deep_reads: int
    year_from: int | None
    year_to: int | None
    source_errors: dict
    source_retry_after: dict
    stop_reason: str | None
    auto_approve: bool
    need_evidence: bool
    cancelled: bool
    interrupt: dict | None
    answer: str | None
    comparison: dict | None
    reviews: list


def checkpoint_path() -> Path:
    base = Path(os.getenv("PAPER_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "papers.sqlite3")))
    return base.with_name("research-checkpoints.sqlite3")


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


def budget_exhausted(state: dict) -> str | None:
    if state.get("api_calls", 0) >= state.get("max_api_calls", MAX_API):
        return "学术接口次数用尽"
    if state.get("llm_calls", 0) >= state.get("max_llm_calls", MAX_LLM):
        return "模型调用次数用尽"
    if state.get("extra_search", 0) > state.get("max_extra_search", MAX_EXTRA_SEARCH):
        return "补检索次数用尽"
    if state.get("deep_reads", 0) > state.get("max_deep_reads", MAX_DEEP):
        return "深读篇数用尽"
    if state.get("cancelled"):
        return "用户取消"
    return None


def limited(state: dict) -> bool:
    return any("限流" in str(value) or "429" in str(value) for value in (state.get("source_errors") or {}).values())


def route(state: dict) -> Literal["expand", "read", "synthesize", "stop"]:
    reason = budget_exhausted(state)
    if reason:
        return "stop"
    papers = state.get("papers") or []
    if limited(state) and not papers:
        return "stop"
    queries = state.get("queries") or []
    index = state.get("query_index", 0)
    extra = state.get("extra_search", 0)
    if len(papers) < 5 and extra < state.get("max_extra_search", MAX_EXTRA_SEARCH) and index + 1 < len(queries):
        return "expand"
    read_ids = set(state.get("read_ids") or [])
    unread = [paper for paper in papers if paper.get("id") not in read_ids]
    if unread and state.get("deep_reads", 0) < state.get("max_deep_reads", MAX_DEEP):
        if state.get("need_evidence") or state.get("deep_reads", 0) == 0:
            return "read"
    if papers:
        return "synthesize"
    return "stop"


def with_event(state: dict, event: dict | list[dict]) -> list[dict]:
    extra = event if isinstance(event, list) else [event]
    return list(state.get("events") or []) + extra


def public_state(state: dict) -> dict:
    return {
        "goal": state.get("goal"),
        "plan": state.get("plan") or [],
        "plan_version": state.get("plan_version", 0),
        "queries": state.get("queries") or [],
        "paper_ids": [paper.get("id") for paper in state.get("papers") or []],
        "evidence_ids": state.get("evidence_ids") or [],
        "gaps": state.get("gaps") or [],
        "events": state.get("events") or [],
        "api_calls": state.get("api_calls", 0),
        "llm_calls": state.get("llm_calls", 0),
        "extra_search": state.get("extra_search", 0),
        "deep_reads": state.get("deep_reads", 0),
        "stop_reason": state.get("stop_reason"),
        "interrupt": state.get("interrupt"),
        "answer": state.get("answer"),
        "comparison": state.get("comparison"),
    }


async def plan_node(state: dict) -> dict:
    planned = await attribution.decompose(state["goal"])
    queries = planned["queries"]
    plan = [{"action": "search", "query": query} for query in queries]
    events = [{"step": "plan", "queries": queries, "rationale": planned.get("rationale")}]
    llm_calls = state.get("llm_calls", 0) + (0 if planned.get("rationale", "").startswith("模型规划不可用") else 1)
    if not state.get("auto_approve"):
        from langgraph.types import interrupt
        approved = interrupt({"type": "review_plan", "plan": plan, "queries": queries})
        if isinstance(approved, dict):
            plan = approved.get("plan", plan)
            queries = approved.get("queries", queries)
    return {
        "plan": plan,
        "queries": queries,
        "plan_version": state.get("plan_version", 0) + 1,
        "query_index": 0,
        "llm_calls": llm_calls,
        "events": with_event(state, events),
        "interrupt": None,
    }


async def search_node(state: dict) -> dict:
    queries = state.get("queries") or [state["goal"]]
    query = queries[min(state.get("query_index", 0), len(queries) - 1)]
    result = await search.search_papers(
        query, 1, 20, "relevance", "all", state.get("year_from"), state.get("year_to"),
    )
    known = {paper["id"]: paper for paper in state.get("papers") or []}
    for paper in result.get("papers") or []:
        payload = paper.model_dump() if hasattr(paper, "model_dump") else paper
        known[payload["id"]] = payload
    errors = result.get("source_errors") or {}
    event = {
        "step": "search",
        "query": query,
        "count": len(result.get("papers") or []),
        "errors": errors,
        "reason": "来源部分失败，保留已取得结果" if errors and known else "检索候选",
    }
    stop_reason = None
    if errors and not known:
        stop_reason = "来源限流或失败且没有可用候选" if limited({"source_errors": errors}) else "没有检索到候选"
    return {
        "papers": list(known.values()),
        "source_errors": errors,
        "source_retry_after": result.get("source_retry_after") or {},
        "api_calls": state.get("api_calls", 0) + 1,
        "events": with_event(state, event),
        "stop_reason": stop_reason or state.get("stop_reason"),
        "need_evidence": True,
    }


def expand_node(state: dict) -> dict:
    nxt = state.get("query_index", 0) + 1
    query = (state.get("queries") or [""])[min(nxt, len(state.get("queries") or [""]) - 1)]
    return {
        "query_index": nxt,
        "extra_search": state.get("extra_search", 0) + 1,
        "events": with_event(state, {"step": "expand", "reason": "覆盖不足", "query": query}),
    }


async def read_node(state: dict) -> dict:
    read_ids = list(state.get("read_ids") or [])
    unread = [paper for paper in state.get("papers") or [] if paper.get("id") not in read_ids]
    if not unread:
        return {"need_evidence": False}
    paper = PaperOut.model_validate(unread[0])
    try:
        saved, _ = await __import__("asyncio").to_thread(library.save_paper, paper, paper.id)
        prepared = await fulltext.prepare(saved)
    except Exception:
        prepared = None
        saved = paper
    gaps = list(state.get("gaps") or [])
    evidence_ids = list(state.get("evidence_ids") or [])
    llm_calls = state.get("llm_calls", 0)
    if prepared is None:
        gaps.append(f"全文不可得:{paper.id}")
        event = {"step": "read", "paper_id": paper.id, "reason": "全文不可得，保留摘要缺口"}
    else:
        hybrid.index_prepared(saved, prepared, getattr(saved, "local_id", None))
        try:
            fields = await llm.analyze(saved, prepared)
            llm_calls += 1
            items = attribution.evidence_from_claims(paper.id, fields, start=len(evidence_ids) + 1)
            evidence_ids.extend(item.evidence_id for item in items)
            event = {"step": "read", "paper_id": paper.id, "reason": "补读正文并建立证据"}
        except llm.AnalysisError:
            gaps.append(f"分析失败:{paper.id}")
            event = {"step": "read", "paper_id": paper.id, "reason": "精读解析失败，已保留全文索引"}
    read_ids.append(paper.id)
    return {
        "read_ids": read_ids,
        "deep_reads": state.get("deep_reads", 0) + 1,
        "gaps": gaps,
        "evidence_ids": evidence_ids,
        "llm_calls": llm_calls,
        "need_evidence": False,
        "events": with_event(state, event),
    }


async def synthesize_node(state: dict) -> dict:
    paper_ids = [paper["id"] for paper in state.get("papers") or []]
    try:
        result = await attribution.answer_question(state["goal"], paper_ids or None)
        llm_calls = state.get("llm_calls", 0) + 1
    except llm.AnalysisError:
        result = {
            "answer": "生成模型不可用，已保留检索与已有证据。",
            "evidence": [],
            "reviews": [],
            "coverage_gap": True,
        }
        llm_calls = state.get("llm_calls", 0)
    event = {"step": "synthesize", "reason": "综合已收集证据"}
    return {
        "answer": result.get("answer"),
        "evidence_ids": [item["evidence_id"] for item in result.get("evidence") or []],
        "reviews": result.get("reviews") or [],
        "llm_calls": llm_calls,
        "events": with_event(state, event),
        "stop_reason": state.get("stop_reason") or "目标覆盖完成",
    }


def finish_node(state: dict) -> dict:
    reason = state.get("stop_reason") or budget_exhausted(state) or "没有可继续的动作"
    if limited(state) and state.get("papers"):
        reason = "来源限流，返回部分结果"
    return {
        **state,
        "stop_reason": reason,
        "events": with_event(state, {"step": "stop", "reason": reason}),
    }


def build(checkpointer=None):
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("search", search_node)
    graph.add_node("expand", expand_node)
    graph.add_node("read", read_node)
    graph.add_node("synthesize", synthesize_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "search")
    graph.add_conditional_edges("search", route, {
        "expand": "expand", "read": "read", "synthesize": "synthesize", "stop": "finish",
    })
    graph.add_edge("expand", "search")
    graph.add_conditional_edges("read", route, {
        "expand": "expand", "read": "read", "synthesize": "synthesize", "stop": "finish",
    })
    graph.add_edge("synthesize", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


_GRAPH = None
_CHECKPOINTER = None


async def make_async_checkpointer():
    if os.getenv("RESEARCH_CHECKPOINT", "sqlite") == "memory":
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    path = checkpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver


async def graph():
    global _GRAPH, _CHECKPOINTER
    if _GRAPH is None:
        _CHECKPOINTER = await make_async_checkpointer()
        _GRAPH = build(_CHECKPOINTER)
    return _GRAPH


async def reset_graph():
    global _GRAPH, _CHECKPOINTER
    saver = _CHECKPOINTER
    _GRAPH = None
    _CHECKPOINTER = None
    conn = getattr(saver, "conn", None)
    if conn is not None:
        await conn.close()


def initial_state(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False) -> dict:
    return {
        "goal": goal,
        "year_from": year_from,
        "year_to": year_to,
        "auto_approve": auto_approve,
        "papers": [],
        "read_ids": [],
        "evidence_ids": [],
        "gaps": [],
        "events": [],
        "api_calls": 0,
        "llm_calls": 0,
        "extra_search": 0,
        "deep_reads": 0,
        "max_extra_search": MAX_EXTRA_SEARCH,
        "max_api_calls": MAX_API,
        "max_llm_calls": MAX_LLM,
        "max_deep_reads": MAX_DEEP,
        "plan_version": 0,
        "query_index": 0,
        "need_evidence": False,
    }


def save_task(task_id: str, thread_id: str, status: str, payload: dict) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        ensure_tasks(db)
        db.execute(
            """INSERT INTO research_tasks VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at""",
            (task_id, thread_id, status, json.dumps(payload, ensure_ascii=False), stamp, stamp),
        )


def get_task(task_id: str) -> dict | None:
    with connection() as db:
        ensure_tasks(db)
        row = db.execute("SELECT * FROM research_tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload"])
        return {
            "task_id": row["task_id"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            **payload,
        }


def _interrupt_payload(result: dict):
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


async def run(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False, resume: dict | None = None, task_id: str | None = None) -> dict:
    from langgraph.types import Command

    task_id = task_id or uuid4().hex
    thread_id = task_id
    config = {"configurable": {"thread_id": thread_id}}
    compiled = await graph()
    if resume is not None:
        result = await compiled.ainvoke(Command(resume=resume), config)
    else:
        result = await compiled.ainvoke(initial_state(goal, year_from, year_to, auto_approve), config)
    paused = _interrupt_payload(result)
    payload = public_state({k: v for k, v in result.items() if k != "__interrupt__"})
    if paused is not None:
        payload["interrupt"] = paused
        save_task(task_id, thread_id, "waiting", payload)
        return {"task_id": task_id, "status": "waiting", **payload}
    save_task(task_id, thread_id, "done", payload)
    return {"task_id": task_id, "status": "done", **payload}

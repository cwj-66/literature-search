"""AgentState, intermediate models, budgets and the unified evidence registry."""

import hashlib
import operator
import os
import re
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.agents import store
from app.tools.research_scope import ToolContext

MAX_EXTRA_SEARCH = 2
MAX_API = 8
MAX_LLM = 24
MAX_DEEP = 5
MAX_ASSESS = 4
MAX_JUDGE_ROUNDS = 2
MAX_REACT = 24
MAX_CLARIFY = 2
MAX_SECONDS = int(os.getenv("RESEARCH_MAX_SECONDS", "900"))

MAX_ASSESS_PAPERS = 15
MAX_EVIDENCE_ITEMS = 60
MAX_CANDIDATE_PAPERS = 120
MIN_MERGE_SCORE = 1
ABSTRACT_CHARS = 300
MAX_CLAIM_CHARS = 1000
SEARCH_PAGE_SIZE = 20
MAX_TOOL_PAPERS = 5

EVIDENCE_ID_RE = re.compile(r"^E\d{4}$")
SCIENTIFIC_MATERIALS = {"original", "abstract"}
DOMAINS = ("discovery", "workspace", "analysis", "knowledge")
SLOT_NAMES = ("workspace_id", "current_paper_id", "selected_paper_ids", "scope", "year_from", "year_to")
CRITICAL_SLOTS = {"workspace_id", "current_paper_id", "selected_paper_ids"}


class AgentState(TypedDict, total=False):
    # 上下文
    workspace_id: str | None
    scope: str
    current_paper_id: str | None
    selected_paper_ids: list[str]
    accessible_paper_ids: list[str] | None
    conversation_history: str
    year_from: int | None
    year_to: int | None
    # 任务
    task_id: str
    parent_task_id: str | None
    goal: str
    mode: str
    route: dict | None
    task_kind: str
    domains: list[str]
    research_intent: str
    workflow_stage: str
    workflow_action: str
    workflow_local_ids: list[str]
    workflow_discovered_ids: list[str]
    workflow_reads: dict
    workflow_searches: list[str]
    workflow_feedback: list[str]
    workflow_calls: dict
    workflow_rows: list[dict]
    workflow_paper_id: str | None
    workflow_batch_ids: list[str]
    missing_slots: list[dict]
    clarify_resolved: bool
    clarify_count: int
    plan: list
    plan_version: int
    queries: list
    query_index: int
    auto_approve: bool
    cancelled: bool
    stop_reason: str | None
    interrupt: dict | None
    # 预算与过程
    max_seconds: int
    react_turns: int
    search_count: int
    assessments: int
    search_pages: dict
    api_calls: int
    llm_calls: int
    extra_search: int
    deep_reads: int
    max_extra_search: int
    max_api_calls: int
    max_llm_calls: int
    max_deep_reads: int
    judge_round: int
    source_errors: dict
    source_retry_after: dict
    events: list
    past_steps: Annotated[list, operator.add]
    messages: Annotated[list, add_messages]
    operations: list
    # 论文与证据
    papers: list
    read_ids: list
    evidence_ids: list
    evidence_pool: dict
    evidence_papers: dict
    evidence_sources: dict
    evidence_items: dict
    result_limit: int | None
    sort_order: str | None
    gaps: list
    coverage: dict | None
    need_evidence: bool
    pending_processing: list
    # 输出
    claims: list
    review: dict | None
    answer: str | None
    comparison: dict | None
    reviews: list


class CoverageAssessment(BaseModel):
    coverage: Literal["sufficient", "insufficient", "partial"]
    missing: list[str] = Field(default_factory=list)
    next_action: Literal["search_more", "read_next", "paginate", "synthesize", "stop"]
    rationale: str = ""
    new_query: str | None = None


class TaskRoute(BaseModel):
    mode: Literal["direct", "react", "plan"]
    rationale: str = ""
    domains: list[str] = Field(default_factory=list)
    missing_slots: list[dict] = Field(default_factory=list)
    research_intent: Literal["auto", "discover", "read", "mixed"] = "auto"


class DirectAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=20000)


class ReactToolCall(BaseModel):
    name: str
    args: dict = Field(default_factory=dict)


class ReactOutput(BaseModel):
    thought: str = ""
    tool_calls: list[ReactToolCall] | None = None
    answer: str | None = None


class ClaimDraft(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimsOutput(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list)


class JudgeVerdict(BaseModel):
    claim_id: str
    verdict: Literal["supported", "insufficient", "contradiction", "unknown"]
    issue: str | None = None
    action: Literal["keep", "revise", "drop", "flag"] = "keep"
    quote: str | None = None


class JudgeBatch(BaseModel):
    reviews: list[JudgeVerdict] = Field(default_factory=list)


class EvidenceItem(TypedDict, total=False):
    evidence_id: str
    paper_id: str | None
    quote: str
    material: Literal["original", "abstract", "metadata", "sample_statistics"]
    tool: str
    section: str | None
    page_number: int | None
    chunk_id: str | None
    doc_version: str | None
    verified: bool


# ---------------------------------------------------------------------------
# 预算与控制
# ---------------------------------------------------------------------------


def is_cancel_requested(task_id: str | None) -> bool:
    return store.flag_of(task_id) == "cancel"


def budget_exhausted(state: dict) -> str | None:
    if state.get("api_calls", 0) >= state.get("max_api_calls", MAX_API):
        return "学术接口次数用尽"
    if state.get("llm_calls", 0) >= state.get("max_llm_calls", MAX_LLM):
        return "模型调用次数用尽"
    if state.get("extra_search", 0) > state.get("max_extra_search", MAX_EXTRA_SEARCH):
        return "补检索次数用尽"
    if state.get("deep_reads", 0) > state.get("max_deep_reads", MAX_DEEP):
        return "深读篇数用尽"
    if state.get("cancelled") or is_cancel_requested(state.get("task_id")):
        return "用户取消"
    if store.deadline_passed(state.get("task_id")):
        return "任务超时"
    return None


def hard_stop(state: dict) -> str | None:
    """必须立即结束循环的条件。接口/补检索/深读额度耗尽只拦截对应工具，仍允许基于已有证据作答与复核。"""
    if state.get("cancelled") or is_cancel_requested(state.get("task_id")):
        return "用户取消"
    if store.deadline_passed(state.get("task_id")):
        return "任务超时"
    if state.get("llm_calls", 0) >= state.get("max_llm_calls", MAX_LLM):
        return "模型调用次数用尽"
    return None


def remaining_budget(state: dict) -> dict:
    return {
        "api_calls_left": max(0, state.get("max_api_calls", MAX_API) - state.get("api_calls", 0)),
        "llm_calls_left": max(0, state.get("max_llm_calls", MAX_LLM) - state.get("llm_calls", 0)),
        "extra_search_left": max(0, state.get("max_extra_search", MAX_EXTRA_SEARCH) - state.get("extra_search", 0)),
        "deep_reads_left": max(0, state.get("max_deep_reads", MAX_DEEP) - state.get("deep_reads", 0)),
        "assessments": state.get("assessments", 0) + 1,
    }


def with_event(state: dict, event: dict | list[dict]) -> list[dict]:
    extra = event if isinstance(event, list) else [event]
    return list(state.get("events") or []) + extra


def msg_attr(message, key: str, default=None):
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def msg_role(message) -> str:
    """dict 消息用 role；检查点恢复出的 LangChain 消息用 type（human/ai/system/tool）。"""
    role = msg_attr(message, "role")
    if role:
        return role
    return {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}.get(msg_attr(message, "type"), "user")


# ---------------------------------------------------------------------------
# 统一证据登记：evidence_items 为权威结构，evidence_pool/papers/sources/ids 为兼容视图
# ---------------------------------------------------------------------------


def add_evidence(state: dict, items: list[dict]) -> tuple[dict, list[dict]]:
    """登记 EvidenceItem（分配 E-id、去重），返回 (state 更新, 已登记条目)。"""
    pool = dict(state.get("evidence_pool") or {})
    papers = dict(state.get("evidence_papers") or {})
    sources = dict(state.get("evidence_sources") or {})
    records = dict(state.get("evidence_items") or {})
    ids = list(state.get("evidence_ids") or [])
    seen = {(rec.get("paper_id"), rec.get("quote"), rec.get("material")): eid for eid, rec in records.items()}
    added = []
    for item in items:
        quote = str(item.get("quote") or "").strip()[:900]
        if not quote:
            continue
        material = item.get("material") or "original"
        key = (item.get("paper_id"), quote, material)
        if key in seen:
            added.append(records[seen[key]])
            continue
        # Eviction must never recycle an ID still referenced by earlier claims.
        next_id = max((int(key[1:]) for key in pool if EVIDENCE_ID_RE.match(key)), default=0) + 1
        eid = f"E{next_id:04d}"
        record: EvidenceItem = {
            "evidence_id": eid, "paper_id": item.get("paper_id"), "quote": quote, "material": material,
            "tool": item.get("tool"), "section": item.get("section"), "page_number": item.get("page_number"),
            "chunk_id": item.get("chunk_id"), "doc_version": item.get("doc_version"),
            "verified": bool(item.get("verified", material in SCIENTIFIC_MATERIALS)),
        }
        pool[eid] = quote
        if record["paper_id"]:
            papers[eid] = record["paper_id"]
        sources[eid] = {"tool": record["tool"], "material": material}
        records[eid] = record
        seen[key] = eid
        ids.append(eid)
        added.append(record)
    if len(records) > MAX_EVIDENCE_ITEMS:
        # 超出上限丢弃最旧证据，保持证据池有界
        for eid in list(records.keys())[:len(records) - MAX_EVIDENCE_ITEMS]:
            pool.pop(eid, None)
            papers.pop(eid, None)
            sources.pop(eid, None)
            records.pop(eid, None)
            if eid in ids:
                ids.remove(eid)
    updates = {"evidence_pool": pool, "evidence_papers": papers, "evidence_sources": sources,
               "evidence_items": records, "evidence_ids": list(dict.fromkeys(ids))}
    return updates, added


def material_of(state: dict, evidence_id: str) -> str:
    source = (state.get("evidence_sources") or {}).get(evidence_id) or {}
    return source.get("material") or "original"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


class QuoteVerifier:
    """模型返回的引文必须能在已存原文（或摘要）中逐字定位后才可作为证据入池。"""

    def __init__(self, state: dict, texts: dict | None = None):
        self.abstracts = {paper.get("id"): paper.get("abstract") for paper in state.get("papers") or []}
        self.texts = dict(texts or {})
        self.cache: dict[str, tuple[str, str | None, str]] = {}

    def _load(self, paper_id: str):
        if paper_id not in self.cache:
            from app.services.papers import documents, library
            text, abstract = self.texts.get(paper_id), self.abstracts.get(paper_id)
            try:
                stored = library.get_paper(paper_id)
                if text is None:
                    text = documents.get_text(paper_id, getattr(stored, "local_id", None), getattr(stored, "id", None))
                abstract = abstract or getattr(stored, "abstract", None)
            except Exception:
                pass
            version = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else None
            self.cache[paper_id] = (_norm(text or ""), version, _norm(abstract or ""))
        return self.cache[paper_id]

    def check(self, paper_id: str | None, quote: str) -> dict | None:
        needle = _norm(quote)
        if not paper_id or len(needle) < 8:
            return None
        text, version, abstract = self._load(paper_id)
        if text and needle in text:
            return {"material": "original", "doc_version": f"doc:{version}", "verified": True}
        if abstract and needle in abstract:
            return {"material": "abstract", "doc_version": None, "verified": True}
        return None


# ---------------------------------------------------------------------------
# 初始与公开状态
# ---------------------------------------------------------------------------


def initial_state(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False,
                  task_id: str | None = None, mode: str = "auto", context: dict | None = None,
                  max_seconds: int | None = None, parent_task_id: str | None = None) -> dict:
    return {
        **ToolContext.model_validate(context or {}).model_dump(),
        "task_id": task_id,
        "parent_task_id": parent_task_id,
        "goal": goal,
        "mode": mode,
        "route": None,
        "task_kind": "research",
        "domains": [],
        "research_intent": "auto",
        "workflow_stage": "",
        "workflow_action": "",
        "workflow_local_ids": [],
        "workflow_discovered_ids": [],
        "workflow_reads": {},
        "workflow_searches": [],
        "workflow_feedback": [],
        "workflow_calls": {},
        "workflow_rows": [],
        "workflow_paper_id": None,
        "workflow_batch_ids": [],
        "missing_slots": [],
        "clarify_resolved": False,
        "clarify_count": 0,
        "year_from": year_from,
        "year_to": year_to,
        "auto_approve": auto_approve,
        "max_seconds": max_seconds or MAX_SECONDS,
        "papers": [],
        "read_ids": [],
        "evidence_ids": [],
        "evidence_pool": {},
        "evidence_papers": {},
        "evidence_sources": {},
        "evidence_items": {},
        "gaps": [],
        "events": [],
        "past_steps": [],
        "messages": [],
        "operations": [],
        "pending_processing": [],
        "react_turns": 0,
        "search_count": 0,
        "claims": [],
        "judge_round": 0,
        "review": None,
        "coverage": None,
        "assessments": 0,
        "search_pages": {},
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
        "result_limit": None,
        "sort_order": None,
    }


def _status_of(state: dict) -> str:
    interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
    if interrupts or state.get("interrupt"):
        return "waiting"
    if state.get("stop_reason"):
        return "done"
    return "running"


def public_state(state: dict) -> dict:
    items = state.get("evidence_items") or {}
    return {
        **{key: state.get(key) for key in ToolContext.model_fields},
        "parent_task_id": state.get("parent_task_id"),
        "goal": state.get("goal"),
        "mode": state.get("mode"),
        "route": state.get("route"),
        "task_kind": state.get("task_kind"),
        "domains": state.get("domains") or [],
        "workflow_stage": state.get("workflow_stage"),
        "research_intent": state.get("research_intent"),
        "discovered_paper_ids": state.get("workflow_discovered_ids") or [],
        "reading_status": state.get("workflow_reads") or {},
        "missing_slots": state.get("missing_slots") or [],
        "clarify_count": state.get("clarify_count", 0),
        "year_from": state.get("year_from"),
        "year_to": state.get("year_to"),
        "auto_approve": state.get("auto_approve", False),
        "plan": state.get("plan") or [],
        "plan_version": state.get("plan_version", 0),
        "queries": state.get("queries") or [],
        "paper_ids": [paper.get("id") for paper in state.get("papers") or []],
        "papers": state.get("papers") or [],
        "evidence_ids": state.get("evidence_ids") or [],
        "evidence": [{**item, "quote": str(item.get("quote") or "")[:900]} for item in items.values()],
        "referenced_evidence_ids": list(dict.fromkeys(
            eid for claim in state.get("claims") or []
            if claim.get("status") not in {"dropped", "contradicted"}
            for eid in (claim.get("evidence_ids") or []))),
        "gaps": state.get("gaps") or [],
        "pending_processing": state.get("pending_processing") or [],
        "operations": state.get("operations") or [],
        "events": state.get("events") or [],
        "past_steps": state.get("past_steps") or [],
        "claims": state.get("claims") or [],
        "judge_round": state.get("judge_round", 0),
        "review": state.get("review"),
        "coverage": state.get("coverage"),
        "assessments": state.get("assessments", 0),
        "api_calls": state.get("api_calls", 0),
        "search_count": state.get("search_count", 0),
        "llm_calls": state.get("llm_calls", 0),
        "extra_search": state.get("extra_search", 0),
        "deep_reads": state.get("deep_reads", 0),
        "stop_reason": state.get("stop_reason"),
        "status": _status_of(state),
        "interrupt": state.get("interrupt"),
        "answer": state.get("answer"),
        "comparison": state.get("comparison"),
    }

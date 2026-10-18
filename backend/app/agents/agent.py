"""LangGraph research agent：Router → Plan/ReAct ⇄ 统一 ToolNode → Judge 质量控制。

图层结构（业务实现全部复用既有 services/tools）：
  START → router（任务路由）──plan──→ plan（制定计划+审批中断）──┐
                            └──react──→ react（直通初始化）───────┤
                              ↓（gate：取消/暂停/预算）              │
                        react_agent ⇄ tool_node（search_papers / retrieve_paper_evidence
                              │ 无 tool_calls                      / analyze_paper / assess_coverage
                              ↓                                    / save_papers_to_workspace*
                        gate_judge（gate）                         / request_deep_analysis*）
                              ↓                                    *特权工具：interrupt 确认后执行
                        claims → judge（预检+语义+覆盖度+多篇覆盖）
                              ↓ judge_route
                    revise（反馈回 ReAct 循环）→ react_agent …（≤MAX_JUDGE_ROUNDS）
                              ↓ finalize → gate_done → finish

外部调用（检索、分析、评估、评审）统一经过 call_cache 表缓存：恢复重放命中缓存时
不再重复调用、不重复计费（llm_calls/api_calls 只计未命中）。缓存键包含
AGENT_PROMPT_VERSION；修改任何提示词后递增该版本号即可使旧缓存失效。

router_node 的确定性捷径：arXiv 号 / DOI 等单实体文献标识直接 react，不调用 LLM。
"""

import asyncio
import hashlib
import json
import operator
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, TypedDict
from uuid import uuid4

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, ValidationError

from app.infrastructure import llm
from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.analysis import attribution, evidence_analysis as ea, fulltext
from app.services.papers import library, workspaces
from app.services.retrieval import hybrid, search
import app.tools.analyze_paper as analyze_tool
from app.tools import research as research_tools
from app.tools.research_scope import ToolContext

MAX_EXTRA_SEARCH = 2
MAX_API = 8
MAX_LLM = 24
MAX_DEEP = 5
MAX_ASSESS = 4
MAX_JUDGE_ROUNDS = 2
MAX_REACT = 24

MAX_ASSESS_PAPERS = 15
ABSTRACT_CHARS = 300
MAX_CLAIM_CHARS = 1000
SEARCH_PAGE_SIZE = 20
MAX_TOOL_PAPERS = 5

ASSESS_TOOL = {
    "name": "assess_coverage", "module": "discovery",
    "description": "评估当前候选与证据的覆盖度，返回 next_action（search_more/paginate/read_next/synthesize/stop）和 new_query；由你据此决定是否换检索词、翻页（search_papers 的 page）或补取证据。",
    "parameters": {"type": "object", "properties": {}},
}
PRIVILEGED_TOOLS = {"add_papers_to_workspace", "remove_paper_from_workspace", "save_papers_to_workspace", "request_deep_analysis"}

EVIDENCE_ID_RE = re.compile(r"^E\d{4}$")

# 提示词版本：任何系统提示词或缓存键结构变化时递增，使 call_cache 中的旧结果失效。
AGENT_PROMPT_VERSION = "v3-research-tools"

ASSESS_SYSTEM = """你是文献调研的覆盖度评估员。只根据给出的实际结果判断，不得臆造文献或事实。
year_from/year_to 是用户的硬性年份约束，绝对不能修改；new_query 是补充用的检索式，不得包含年份范围（年份由检索接口参数约束）。
next_action 只能是以下之一：
- search_more：候选数量不足或与目标相关性低，需要补充检索；如有更好的检索式写入 new_query
- paginate：当前检索式仍可能有更多相关结果，请求下一页
- read_next：候选已基本足够，但关键方面缺少全文证据，应先精读未读文献
- synthesize：证据已充分，可以综合回答
- stop：预算耗尽或确实无法继续
输出JSON {"coverage":"sufficient|insufficient|partial","missing":["..."],"next_action":"...","rationale":"一句话理由","new_query":"..."或null}"""

REACT_SYSTEM = """你是文献调研 ReAct 智能体。围绕用户目标自主决定：调用工具收集材料，或在信息足够时给出最终答案。
【硬性规则】
1. 每轮只输出 tool_calls（可一次多个，按依赖顺序）或 answer，二者其一。
2. answer 中的关键事实必须标注证据 ID（如 [E0001]），只能引用工具返回过的 ID，不得编造论文、数字或引用。
3. 覆盖度不足时不要盲目作答；对比/综述类目标确保证据 spanning 至少两篇论文。
4. 年份约束由系统执行，检索词中不要带年份范围。
【工具使用】
下方工具目录按四个业务模块提供，必须使用目录中的工具名称和参数。
普通概念问题直接回答；涉及论文结论时先取证据。分析前先 get_paper_analysis，缺失时再 analyze_paper。
工作区上下文不代表检索整个工作区，严格遵守 current / selected / workspace 范围。
不得调用后台 acquire_paper_fulltext、parse_paper、index_paper；添加论文后由后台排队处理。
原文片段可作为证据；模型分析、元数据、图谱和样本统计不能冒充原文证据。
引用接口失败不代表没有引用，少量样本不能推断整个领域趋势。
输出JSON（二选一）：
{"thought":"一句话决策理由","tool_calls":[{"name":"工具名","args":{...}}]}
{"thought":"一句话","answer":"最终答案，关键事实标注[E0001]"}"""

CLAIMS_SYSTEM = """把给定的调研答案拆成可独立核验的主张句。每个主张句必须能在原文证据中找到依据。
只能使用给定证据表中的 evidence_id 绑定主张，不得新增证据或编造引用。
输出JSON {"claims":[{"text":"主张句（尽量保持原表述）","evidence_ids":["E0001"]}]}"""

JUDGE_SYSTEM = """你是独立评审，对每条主张做绝对量规核验：只根据附带的原文证据判断，不做 pairwise 比较，不使用外部知识。
证据篇幅短不构成问题，关键是有没有直接支持或矛盾的内容。
每条结论必须给出 quote：支持或矛盾的原文片段（从证据 quote 中截取，没有依据时给出最接近的片段并说明）。
verdict：supported=证据直接支持；insufficient=证据不足；contradiction=证据矛盾；unknown=无法判断。
action：keep=保留；revise=证据不足但可改写得更保守准确；drop=应删除；flag=保留但标记待人工核实。
输出JSON {"reviews":[{"claim_id":"c1","verdict":"supported","issue":null,"action":"keep","quote":"..."}]}"""

ROUTER_SYSTEM = """你是任务路由员，把用户请求分类为 react 或 plan。
react：单一明确意图、一轮检索即可回答，例如查某篇论文、某个具体事实、简单主题初探。
plan：多子问题、调研性质、需要覆盖度评估与补检索，例如综述、对比、梳理研究脉络。
输出JSON {"mode":"react|plan","rationale":"一句话理由"}"""

ARXIV_REF = re.compile(r"^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$", re.IGNORECASE)
DOI_REF = re.compile(r"^(doi:)?10\.\d{4,9}/\S+$", re.IGNORECASE)
COMPARATIVE_HINT = re.compile(r"对比|比较|综述|梳理|差异|各自|横向|区别")


class AgentState(TypedDict, total=False):
    workspace_id: str | None
    scope: str
    current_paper_id: str | None
    selected_paper_ids: list[str]
    evidence_sources: dict
    task_id: str
    goal: str
    mode: str
    route: dict | None
    plan: list
    plan_version: int
    queries: list
    query_index: int
    papers: list
    read_ids: list
    evidence_ids: list
    evidence_pool: dict
    evidence_papers: dict
    gaps: list
    events: list
    past_steps: Annotated[list, operator.add]
    messages: Annotated[list, add_messages]
    react_turns: int
    search_count: int
    claims: list
    judge_round: int
    review: dict | None
    coverage: dict | None
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


class CoverageAssessment(BaseModel):
    coverage: Literal["sufficient", "insufficient", "partial"]
    missing: list[str] = Field(default_factory=list)
    next_action: Literal["search_more", "read_next", "paginate", "synthesize", "stop"]
    rationale: str = ""
    new_query: str | None = None


class TaskRoute(BaseModel):
    mode: Literal["react", "plan"]
    rationale: str = ""


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


def checkpoint_path() -> Path:
    base = Path(os.getenv("PAPER_DB_PATH", str(Path(__file__).resolve().parents[2] / "data" / "papers.sqlite3")))
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


def ensure_flags(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS cancel_flags (
            task_id TEXT PRIMARY KEY,
            flag TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)


def _set_flag(task_id: str, flag: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    with connection() as db, db:
        ensure_flags(db)
        db.execute(
            """INSERT INTO cancel_flags VALUES (?, ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET flag=excluded.flag, created_at=excluded.created_at""",
            (task_id, flag, stamp),
        )


def _flag_of(task_id: str | None) -> str | None:
    if not task_id:
        return None
    try:
        with connection() as db:
            ensure_flags(db)
            row = db.execute("SELECT flag FROM cancel_flags WHERE task_id = ?", (task_id,)).fetchone()
            return row["flag"] if row else None
    except Exception:
        return None


def _clear_flag(task_id: str | None) -> None:
    if not task_id:
        return
    try:
        with connection() as db, db:
            ensure_flags(db)
            db.execute("DELETE FROM cancel_flags WHERE task_id = ?", (task_id,))
    except Exception:
        pass


def ensure_cache(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS call_cache (
            cache_key TEXT PRIMARY KEY,
            result TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)


def _cache_key(kind: str, payload: dict) -> str:
    material = {"v": AGENT_PROMPT_VERSION, "kind": kind, **payload}
    text = json.dumps(material, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_get(key: str):
    try:
        with connection() as db:
            ensure_cache(db)
            row = db.execute("SELECT result FROM call_cache WHERE cache_key = ?", (key,)).fetchone()
            return json.loads(row["result"]) if row else None
    except Exception:
        return None


def _cache_put(key: str, result) -> None:
    try:
        stamp = datetime.now(timezone.utc).isoformat()
        with connection() as db, db:
            ensure_cache(db)
            db.execute(
                "INSERT OR REPLACE INTO call_cache VALUES (?, ?, ?)",
                (key, json.dumps(result, ensure_ascii=False, default=str), stamp),
            )
    except Exception:
        pass


async def cached_call(kind: str, payload: dict, producer):
    """外部调用缓存：命中时直接返回缓存结果，不执行 producer（不重复解析/入库/付费）。

    返回 (result, cache_hit)。producer 抛异常时不写缓存，异常原样上抛。
    """
    key = _cache_key(kind, payload)
    hit = _cache_get(key)
    if hit is not None:
        return hit, True
    result = producer()
    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
        result = await result
    _cache_put(key, result)
    return result, False


def request_cancel(task_id: str) -> None:
    """请求取消任务：持久化到 sqlite，进程重启后仍然生效。"""
    _set_flag(task_id, "cancel")


def request_pause(task_id: str) -> None:
    """请求暂停任务：gate 节点检测到后以 paused 中断挂起，可再恢复。"""
    _set_flag(task_id, "pause")


def is_cancel_requested(task_id: str | None) -> bool:
    return _flag_of(task_id) == "cancel"


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
    return None


def limited(state: dict) -> bool:
    return any("限流" in str(value) or "429" in str(value) for value in (state.get("source_errors") or {}).values())


def route(state: dict) -> Literal["expand", "read", "synthesize", "stop"]:
    """规则路由：assess_coverage 工具在 LLM 不可用时的确定性回退。"""
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


def _rule_assess(state: dict) -> dict:
    """LLM 评估不可用时的规则回退：复用 route 的确定性逻辑，输出同一评估结构。"""
    decision = route(state)
    if decision == "expand":
        return {"coverage": "insufficient", "missing": ["候选数量或相关性"], "next_action": "search_more", "rationale": "候选不足，按计划继续补检索", "new_query": None}
    if decision == "read":
        return {"coverage": "partial", "missing": ["关键事实的全文证据"], "next_action": "read_next", "rationale": "已有候选但缺少全文证据，先精读", "new_query": None}
    if decision == "synthesize":
        return {"coverage": "sufficient", "missing": [], "next_action": "synthesize", "rationale": "证据充分，进入综合", "new_query": None}
    return {"coverage": "partial" if state.get("papers") else "insufficient", "missing": [], "next_action": "stop", "rationale": budget_exhausted(state) or "没有可继续的动作", "new_query": None}


def _downgrade_action(state: dict) -> str:
    """补检索预算不足时的降级选择：优先补读，其次综合，最后停止。"""
    read_ids = set(state.get("read_ids") or [])
    unread = [paper for paper in state.get("papers") or [] if paper.get("id") not in read_ids]
    if unread and state.get("deep_reads", 0) < state.get("max_deep_reads", MAX_DEEP):
        return "read_next"
    if state.get("papers"):
        return "synthesize"
    return "stop"


YEAR_TOKEN = re.compile(r"\b(19|20)\d{2}\b")


def _clean_query(query, state: dict) -> str | None:
    """清洗 LLM 建议的检索式：设置年份硬约束时剔除夹带的年份词，约束只由接口参数执行。"""
    if not isinstance(query, str):
        return None
    cleaned = query.strip()
    if not cleaned:
        return None
    if state.get("year_from") is not None and state.get("year_to") is not None:
        cleaned = re.sub(r"\s+", " ", YEAR_TOKEN.sub("", cleaned)).strip(" ()")
    return cleaned[:300] or None


def gate_route(state: dict) -> Literal["finish", "continue"]:
    if state.get("cancelled") or state.get("stop_reason") or budget_exhausted(state):
        return "finish"
    return "continue"


def with_event(state: dict, event: dict | list[dict]) -> list[dict]:
    extra = event if isinstance(event, list) else [event]
    return list(state.get("events") or []) + extra


def _interrupt_payload(state: dict):
    interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
    if not interrupts:
        return None
    first = interrupts[0]
    return getattr(first, "value", first)


def _status_of(state: dict) -> str:
    if _interrupt_payload(state) is not None or state.get("interrupt"):
        return "waiting"
    if state.get("stop_reason"):
        return "done"
    return "running"


def public_state(state: dict) -> dict:
    return {
        **{key: state.get(key) for key in ("workspace_id", "scope", "current_paper_id", "selected_paper_ids")},
        "goal": state.get("goal"),
        "mode": state.get("mode"),
        "route": state.get("route"),
        "plan": state.get("plan") or [],
        "plan_version": state.get("plan_version", 0),
        "queries": state.get("queries") or [],
        "paper_ids": [paper.get("id") for paper in state.get("papers") or []],
        "evidence_ids": state.get("evidence_ids") or [],
        "gaps": state.get("gaps") or [],
        "events": state.get("events") or [],
        "past_steps": state.get("past_steps") or [],
        "claims": state.get("claims") or [],
        "judge_round": state.get("judge_round", 0),
        "review": state.get("review"),
        "coverage": state.get("coverage"),
        "assessments": state.get("assessments", 0),
        "api_calls": state.get("api_calls", 0),
        "llm_calls": state.get("llm_calls", 0),
        "extra_search": state.get("extra_search", 0),
        "deep_reads": state.get("deep_reads", 0),
        "stop_reason": state.get("stop_reason"),
        "status": _status_of(state),
        "interrupt": state.get("interrupt"),
        "answer": state.get("answer"),
        "comparison": state.get("comparison"),
    }


def _remaining_budget(state: dict) -> dict:
    return {
        "api_calls_left": max(0, state.get("max_api_calls", MAX_API) - state.get("api_calls", 0)),
        "llm_calls_left": max(0, state.get("max_llm_calls", MAX_LLM) - state.get("llm_calls", 0)),
        "extra_search_left": max(0, state.get("max_extra_search", MAX_EXTRA_SEARCH) - state.get("extra_search", 0)),
        "deep_reads_left": max(0, state.get("max_deep_reads", MAX_DEEP) - state.get("deep_reads", 0)),
        "assessments": state.get("assessments", 0) + 1,
    }


def _plan_payload(state: dict, plan: list, queries: list) -> dict:
    return {
        "type": "review_plan",
        "goal": state.get("goal"),
        "plan": plan,
        "queries": queries,
        "constraints": {"year_from": state.get("year_from"), "year_to": state.get("year_to")},
        "budget": {
            "max_api_calls": state.get("max_api_calls", MAX_API),
            "max_llm_calls": state.get("max_llm_calls", MAX_LLM),
            "max_extra_search": state.get("max_extra_search", MAX_EXTRA_SEARCH),
            "max_deep_reads": state.get("max_deep_reads", MAX_DEEP),
        },
    }


def _apply_plan_decision(decision, plan: list, queries: list) -> tuple[list, list, bool]:
    """校验恢复值：仅接受合法字段，畸形输入回退到原计划。返回 (plan, queries, rejected)。"""
    if not isinstance(decision, dict):
        return plan, queries, False
    if decision.get("approved") is False:
        return plan, queries, True
    edited_queries = decision.get("queries")
    if isinstance(edited_queries, list) and edited_queries and all(isinstance(item, str) for item in edited_queries):
        queries = list(edited_queries)
    edited_plan = decision.get("plan")
    if isinstance(edited_plan, list) and edited_plan:
        plan = list(edited_plan)
    return plan, queries, False


# ---------------------------------------------------------------------------
# Router / Plan（任务路由与计划审批，HITL）
# ---------------------------------------------------------------------------


def _router_shortcut(goal: str) -> bool:
    """保守的确定性捷径：单实体文献标识（arXiv 号 / DOI）一眼可判，直接 react 且不调用 LLM。"""
    text = (goal or "").strip()
    return bool(ARXIV_REF.match(text) or DOI_REF.match(text))


async def _llm_route(goal: str) -> dict:
    messages = [{"role": "system", "content": ROUTER_SYSTEM}, {"role": "user", "content": goal}]
    try:
        decision = TaskRoute.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("任务路由不可用") from exc
    return decision.model_dump()


async def router_node(state: dict) -> dict:
    """入口路由：只决定 mode（override > 确定性捷径 > 路由 LLM（缓存）> 回退 plan），不做任何计划/检索状态变更。"""
    goal = state.get("goal") or ""
    requested = state.get("mode") or "auto"
    llm_delta = 0
    if requested in ("plan", "react"):
        mode = requested
        route = {"mode": mode, "rationale": "用户指定模式", "source": "override"}
    elif _router_shortcut(goal):
        mode = "react"
        route = {"mode": mode, "rationale": "单实体文献标识，一轮检索即可", "source": "shortcut"}
    else:
        try:
            decision, cache_hit = await cached_call("router", {"goal": goal}, lambda: _llm_route(goal))
            llm_delta = 0 if cache_hit else 1
            mode = decision["mode"]
            route = {"mode": mode, "rationale": decision.get("rationale") or "", "source": "llm"}
        except llm.AnalysisError:
            mode = "plan"
            llm_delta = 1  # 失败的尝试仍消耗一次调用，但不写缓存
            route = {"mode": mode, "rationale": "路由不可用，回退到计划模式", "source": "rule"}
    return {
        "mode": mode,
        "route": route,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events": with_event(state, {"step": "route", "mode": mode, "reason": route["rationale"], "source": route["source"]}),
        "past_steps": [["route", mode]],
    }


def react_node(state: dict) -> dict:
    """react 直通准备：跳过规划 LLM 与审批中断，以原始问题作为唯一初始检索式。"""
    goal = state.get("goal") or ""
    return {
        "queries": [goal],
        "plan": [{"action": "search", "query": goal}],
        "plan_version": 1,
        "events": with_event(state, {"step": "react", "reason": "简单任务跳过计划，直接进入检索执行"}),
        "past_steps": [["react", goal]],
    }


def route_route(state: dict) -> Literal["plan", "react"]:
    return "plan" if state.get("mode") == "plan" else "react"


async def plan_node(state: dict) -> dict:
    # 中断前的状态更新不会被保存：恢复后本节点从头重放，因此 llm 计数与事件
    # 都必须基于入参状态重新计算（天然幂等），并以 step 键去重作为额外保险。
    planned_before = any(event.get("step") == "plan" for event in state.get("events") or [])
    if planned_before:
        plan = list(state.get("plan") or [])
        queries = list(state.get("queries") or [])
        llm_calls = state.get("llm_calls", 0)
        events = list(state.get("events") or [])
    else:
        planned, _ = await cached_call(
            "plan",
            {"goal": state["goal"], "year_from": state.get("year_from"), "year_to": state.get("year_to")},
            lambda: attribution.decompose(state["goal"]),
        )
        queries = planned["queries"]
        plan = [{"action": "search", "query": query} for query in queries]
        llm_calls = state.get("llm_calls", 0) + (0 if planned.get("rationale", "").startswith("模型规划不可用") else 1)
        events = with_event(state, {"step": "plan", "queries": queries, "rationale": planned.get("rationale")})
    rejected = False
    if not state.get("auto_approve"):
        from langgraph.types import interrupt
        decision = interrupt(_plan_payload(state, plan, queries))
        plan, queries, rejected = _apply_plan_decision(decision, plan, queries)
    return {
        "plan": plan,
        "queries": queries,
        "plan_version": state.get("plan_version", 0) + (0 if planned_before else 1),
        "query_index": 0,
        "llm_calls": llm_calls,
        "events": events,
        "cancelled": True if rejected else state.get("cancelled"),
        "stop_reason": "用户取消" if rejected else state.get("stop_reason"),
        "interrupt": None,
    }


# ---------------------------------------------------------------------------
# ReAct Agent（LLM 决策 ⇄ 统一工具执行）
# ---------------------------------------------------------------------------


def _msg_attr(message, key: str, default=None):
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _react_system(state: dict) -> str:
    plan = state.get("plan") or []
    queries = state.get("queries") or []
    return (
        REACT_SYSTEM
        + "\n【业务工具目录】" + json.dumps([*research_tools.CHAT_TOOLS, ASSESS_TOOL], ensure_ascii=False)
        + "\n【研究范围】" + json.dumps({key: state.get(key) for key in ("workspace_id", "scope", "current_paper_id", "selected_paper_ids")}, ensure_ascii=False)
        + f"\n【目标】{state.get('goal') or ''}"
        + f"\n【计划】{json.dumps(plan, ensure_ascii=False) if plan else '（无，自行决定检索式）'}"
        + f"\n【已用检索式】{json.dumps(queries, ensure_ascii=False)}"
        + f"\n【剩余预算】{json.dumps(_remaining_budget(state), ensure_ascii=False)}"
        + (f"\n【已知缺口】{json.dumps((state.get('gaps') or [])[-5:], ensure_ascii=False)}" if state.get("gaps") else "")
    )


def _base_messages(state: dict) -> list[dict]:
    return [{"role": "system", "content": _react_system(state)}, {"role": "user", "content": state.get("goal") or ""}]


async def react_agent(state: dict) -> dict:
    """ReAct 决策节点：调用 LLM，输出 tool_calls（→tool_node）或 answer（→Judge）。"""
    halt = state.get("stop_reason") or budget_exhausted(state)
    if halt:
        return {"stop_reason": halt, "events": with_event(state, {"step": "react_stop", "reason": halt})}
    turns = state.get("react_turns", 0)
    if turns + 1 > MAX_REACT:
        return {"stop_reason": "达到最大迭代次数", "events": with_event(state, {"step": "react_stop", "reason": "达到最大迭代次数"})}
    messages = list(state.get("messages") or [])
    fresh = not (messages and _msg_attr(messages[0], "role") == "system")
    if fresh:
        messages = _base_messages(state)
    fallback = False
    try:
        raw = await llm.complete(messages)
        output = ReactOutput.model_validate(ea.parse_json(raw))
        llm_delta = 1
    except (llm.AnalysisError, ValueError, TypeError, ValidationError):
        # 模型不可用：按目标/计划做一次保底检索后给出降级答案（与旧 synthesize 回退一致）。
        query = (state.get("queries") or [state.get("goal") or ""])[min(state.get("query_index", 0), max(0, len(state.get("queries") or []) - 1))]
        content, updates = await _tool_search_papers(state, {"query": query or state.get("goal") or ""})
        assistant = {"role": "assistant", "content": json.dumps({"thought": "模型不可用，保底检索后直接作答", "answer": "生成模型不可用，已保留检索与已有证据。"}, ensure_ascii=False)}
        tool_events = updates.pop("events_extra", [])
        events = with_event({**state, "events": with_event(state, tool_events)}, {"step": "react", "turn": turns + 1, "action": "answer", "thought": "模型不可用，保底检索后直接作答", "fallback": True})
        return {
            **updates,
            "messages": messages + [assistant],
            "react_turns": turns + 1,
            "llm_calls": state.get("llm_calls", 0),
            "answer": "生成模型不可用，已保留检索与已有证据。",
            "stop_reason": updates.get("stop_reason") or state.get("stop_reason"),
            "events": events,
            "past_steps": list(state.get("past_steps") or []) + [["react", turns + 1]],
        }
    assistant = {"role": "assistant", "content": json.dumps(output.model_dump(), ensure_ascii=False)}
    has_calls = bool(output.tool_calls)
    if has_calls:
        assistant["tool_calls"] = [
            {"id": f"call_{turns + 1}_{index}", "name": call.name, "args": call.args, "type": "tool_call"}
            for index, call in enumerate(output.tool_calls)
        ]
    updates = {
        "messages": ([*messages, assistant] if fresh else [assistant]),
        "react_turns": turns + 1,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events": with_event(state, {"step": "react", "turn": turns + 1, "action": "tools" if has_calls else "answer", "thought": (output.thought or "")[:200]}),
        "past_steps": [["react", turns + 1]],
    }
    if not has_calls:
        updates["answer"] = (output.answer or "").strip()
        if not updates["answer"]:
            updates["answer"] = "（未能生成有效答复）"
    return updates


def react_route(state: dict) -> Literal["tools", "done"]:
    if state.get("stop_reason"):
        return "done"
    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    if last is not None and _msg_attr(last, "tool_calls"):
        return "tools"
    return "done"


# ---------------------------------------------------------------------------
# 业务工具（统一 ToolNode 执行；特权工具在中断确认后才产生副作用）
# ---------------------------------------------------------------------------


def _compact_papers(papers: list, limit: int = 8) -> list[dict]:
    compact = []
    for paper in papers[:limit]:
        compact.append({
            "id": paper.get("id"),
            "title": paper.get("title"),
            "year": paper.get("year"),
            "venue": paper.get("venue"),
            "abstract": str(paper.get("abstract") or "")[:200],
        })
    return compact


async def _tool_search_papers(state: dict, args: dict) -> tuple[str, dict]:
    raw_query = str(args.get("query") or "").strip()
    query = search.normalize_query(_clean_query(raw_query, state) or raw_query)
    if not query:
        return json.dumps({"error": "缺少检索词"}, ensure_ascii=False), {}
    page = max(1, int(args.get("page") or 1))
    validated = research_tools.SearchPapersRequest.model_validate({**args, "query": query})
    year_from = state.get("year_from") if state.get("year_from") is not None else validated.year_from
    year_to = state.get("year_to") if state.get("year_to") is not None else validated.year_to
    source, sort = validated.source, validated.sort
    page_size = args.get("page_size", SEARCH_PAGE_SIZE)
    cache_key = _cache_key("search", {
        "query": query, "page": page, "page_size": page_size,
        "sort": sort, "source": source, "year_from": year_from, "year_to": year_to,
    })
    cached = _cache_get(cache_key)
    if cached is not None:
        result = cached
        api_delta = 0
        from_cache = True
    else:
        raw = await search.search_papers(query, page, page_size, sort, source, year_from, year_to)
        result = {
            "papers": [item.model_dump() if hasattr(item, "model_dump") else item for item in raw.get("papers") or []],
            "source_errors": raw.get("source_errors") or {},
            "source_retry_after": raw.get("source_retry_after") or {},
        }
        api_delta = 1
        from_cache = False
        if not result["source_errors"]:
            _cache_put(cache_key, result)
    pages = dict(state.get("search_pages") or {})
    pages[query] = page
    known = {paper["id"]: paper for paper in state.get("papers") or []}
    for paper in result.get("papers") or []:
        payload = paper.model_dump() if hasattr(paper, "model_dump") else paper
        known[payload["id"]] = payload
    errors = result.get("source_errors") or {}
    queries = list(state.get("queries") or [])
    extra_search = state.get("extra_search", 0)
    search_count = state.get("search_count", 0)
    if query not in queries:
        queries.append(query)
    if search_count >= 1:
        extra_search += 1
    event = {
        "step": "search",
        "query": query,
        "page": page,
        "count": len(result.get("papers") or []),
        "errors": errors,
        "cached": from_cache,
        "reason": "来源部分失败，保留已取得结果" if errors and known else "检索候选",
    }
    content = json.dumps({
        "found": len(result.get("papers") or []),
        "papers": _compact_papers(result.get("papers") or []),
        "workspace_total": len(known),
        "source_errors": errors,
        "cached": from_cache,
    }, ensure_ascii=False)
    updates = {
        "papers": list(known.values()),
        "search_pages": pages,
        "source_errors": errors,
        "source_retry_after": result.get("source_retry_after") or {},
        "api_calls": state.get("api_calls", 0) + api_delta,
        "queries": queries,
        "extra_search": extra_search,
        "search_count": search_count + 1,
        "need_evidence": True,
        "events_extra": [event],
        "past_extra": [["search", query, page]],
    }
    return content, updates


async def _tool_retrieve_paper_evidence(state: dict, args: dict) -> tuple[str, dict]:
    if state.get("workspace_id") or state.get("current_paper_id") or state.get("selected_paper_ids") or args.get("query"):
        adapted = dict(args)
        if "paper_id" in adapted:
            adapted["paper_ids"] = [adapted.pop("paper_id")]
        adapted["query"] = adapted.pop("aspect", None) or adapted.get("query") or state.get("goal")
        return await _tool_catalog(state, "retrieve_paper_evidence", adapted)
    paper_id = str(args.get("paper_id") or "").strip()
    aspect = str(args.get("aspect") or "").strip() or (state.get("goal") or "")
    known = {paper.get("id") for paper in state.get("papers") or []}
    if not paper_id and not known:
        return json.dumps({"error": "请先指定论文范围"}, ensure_ascii=False), {}
    if paper_id and paper_id not in known:
        return json.dumps({"error": f"论文 {paper_id} 不在当前候选中，请先检索"}, ensure_ascii=False), {}
    try:
        hits = await hybrid.retrieve(aspect, [paper_id] if paper_id else sorted(known))
    except Exception as exc:
        return json.dumps({"error": f"证据检索失败：{str(exc)[:120]}"}, ensure_ascii=False), {}
    pool = dict(state.get("evidence_pool") or {})
    attribution_map = dict(state.get("evidence_papers") or {})
    start = len(pool) + 1
    items = []
    for hit in hits:
        actual_id = hit.get("paper_id") or paper_id
        if actual_id:
            items.extend(attribution.evidence_from_chunks(actual_id, [hit], start=start + len(items)))
    new_evidence = []
    evidence_ids = list(state.get("evidence_ids") or [])
    for item in items:
        pool[item.evidence_id] = item.quote[:900]
        attribution_map[item.evidence_id] = item.paper_id
        evidence_ids.append(item.evidence_id)
        new_evidence.append({"evidence_id": item.evidence_id, "quote": item.quote[:250], "section": item.section})
    content = json.dumps({
        "count": len(new_evidence),
        "evidence": new_evidence[:10],
        "total_in_pool": len(pool),
    }, ensure_ascii=False)
    updates = {
        "evidence_pool": pool,
        "evidence_papers": attribution_map,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "events_extra": [{"step": "tool", "name": "retrieve_paper_evidence", "summary": f"{paper_id or 'workspace'} 取回证据 {len(new_evidence)} 条"}],
        "past_extra": [["retrieve", paper_id or "workspace", len(new_evidence)]],
    }
    return content, updates


def _bind_report_evidence(state: dict, report: dict) -> tuple[dict, dict, list[str], list[dict]]:
    """把分析报告的字段证据绑定为 E-id 入池，返回 (pool, attribution_map, new_ids, new_items)。"""
    pool = dict(state.get("evidence_pool") or {})
    attribution_map = dict(state.get("evidence_papers") or {})
    new_ids, new_items = [], []
    for paper_report in report.get("papers") or []:
        pid = paper_report.get("paper_id")
        for field in paper_report.get("fields") or []:
            for ev in field.get("evidence") or []:
                quote = str(ev.get("quote") or "").strip()
                if len(quote) < 8:
                    continue
                eid = f"E{len(pool) + len(new_items) + 1:04d}"
                new_items.append({"evidence_id": eid, "quote": quote[:250], "section": ev.get("section"), "paper_id": pid})
                new_ids.append(eid)
    for item in new_items:
        pool[item["evidence_id"]] = item["quote"]
        attribution_map[item["evidence_id"]] = item["paper_id"]
    return pool, attribution_map, new_ids, new_items


async def _tool_analyze_paper(state: dict, args: dict) -> tuple[str, dict]:
    if args.get("depth") == "deep" and not (state.get("workspace_id") or state.get("current_paper_id") or state.get("selected_paper_ids")):
        return await _tool_request_deep_analysis(state, {"paper_ids": args.get("paper_ids")})
    if state.get("workspace_id") or state.get("current_paper_id") or state.get("selected_paper_ids"):
        return await _tool_catalog(state, "analyze_paper", args)
    ids = []
    for raw in args.get("paper_ids") or []:
        pid = str(raw).strip()
        if pid and pid not in ids:
            ids.append(pid)
    ids = ids[:MAX_TOOL_PAPERS]
    if not ids:
        return json.dumps({"error": "请提供至少一篇论文"}, ensure_ascii=False), {}
    try:
        report, cache_hit = await cached_call(
            "analyze", {"ids": ids, "depth": "standard"},
            lambda: analyze_tool.analyze_paper(ids, depth="standard"),
        )
    except (llm.AnalysisError, ValueError) as exc:
        return json.dumps({"error": str(exc)[:200]}, ensure_ascii=False), {}
    llm_delta = 0 if cache_hit else (report.get("llm_calls") or 0)
    pool, attribution_map, new_ids, new_items = _bind_report_evidence(state, report)
    evidence_ids = list(dict.fromkeys(list(state.get("evidence_ids") or []) + new_ids))
    content = json.dumps({
        "status": report.get("status"),
        "papers": [
            {
                "paper_id": item.get("paper_id"),
                "title": item.get("title"),
                "status": item.get("status"),
                "fields": [
                    {"label": field.get("label"), "status": field.get("status"), "content": str(field.get("content") or "")[:200]}
                    for field in (item.get("fields") or [])
                ],
            }
            for item in report.get("papers") or []
        ][:MAX_TOOL_PAPERS],
        "new_evidence": [{"evidence_id": item["evidence_id"], "quote": item["quote"][:200], "section": item.get("section")} for item in new_items[:10]],
    }, ensure_ascii=False)
    updates = {
        "evidence_pool": pool,
        "evidence_papers": attribution_map,
        "evidence_ids": evidence_ids,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events_extra": [{"step": "tool", "name": "analyze_paper", "summary": f"分析 {len(ids)} 篇，新增证据 {len(new_ids)} 条"}],
        "past_extra": [["analyze", ids, len(new_ids)]],
    }
    return content, updates


def _assess_cache_payload(state: dict) -> dict:
    read_ids = set(state.get("read_ids") or [])
    papers = []
    for paper in (state.get("papers") or [])[:MAX_ASSESS_PAPERS]:
        papers.append({
            "id": paper.get("id"),
            "title": paper.get("title"),
            "abstract": str(paper.get("abstract") or "")[:ABSTRACT_CHARS],
            "已读": paper.get("id") in read_ids,
        })
    return {
        "goal": state.get("goal"),
        "year_from": state.get("year_from"),
        "year_to": state.get("year_to"),
        "queries": state.get("queries") or [],
        "query_index": state.get("query_index", 0),
        "papers": papers,
        "read_ids": sorted(read_ids),
        "gaps": (state.get("gaps") or [])[-5:],
        "source_errors": state.get("source_errors") or {},
        "search_pages": state.get("search_pages") or {},
    }


async def _llm_assess(state: dict) -> dict:
    material = {**_assess_cache_payload(state), "remaining_budget": _remaining_budget(state)}
    messages = [{"role": "system", "content": ASSESS_SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    try:
        assessment = CoverageAssessment.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("覆盖度评估不可用") from exc
    result = assessment.model_dump()
    new_query = result.get("new_query")
    result["new_query"] = str(new_query).strip()[:300] if new_query else None
    return result


async def _run_coverage_assessment(state: dict) -> tuple[dict, int, bool]:
    """覆盖度评估核心：LLM（缓存）+ 规则回退 + 预算降级。返回 (assessment, llm_delta, fallback)。"""
    reason = budget_exhausted(state)
    if reason:
        return {"coverage": "partial" if state.get("papers") else "insufficient", "missing": [], "next_action": "stop", "rationale": reason, "new_query": None}, 0, False
    try:
        assessment, cache_hit = await cached_call("assess", _assess_cache_payload(state), lambda: _llm_assess(state))
        llm_delta = 0 if cache_hit else 1
        fallback = False
    except llm.AnalysisError:
        assessment = _rule_assess(state)
        llm_delta = 0
        fallback = True
    next_action = assessment.get("next_action")
    if next_action in ("search_more", "paginate") and (
        state.get("assessments", 0) + 1 > MAX_ASSESS
        or state.get("extra_search", 0) >= state.get("max_extra_search", MAX_EXTRA_SEARCH)
    ):
        assessment = {
            **assessment,
            "next_action": _downgrade_action(state),
            "rationale": f"{assessment.get('rationale', '')}；补检索预算不足，降级处理",
        }
    return assessment, llm_delta, fallback


async def _tool_assess_coverage(state: dict, args: dict) -> tuple[str, dict]:
    assessment, llm_delta, fallback = await _run_coverage_assessment(state)
    event = {
        "step": "assess",
        "coverage": assessment.get("coverage"),
        "next_action": assessment.get("next_action"),
        "reason": (assessment.get("rationale") or "") + ("；评估不可用，使用规则回退" if fallback else ""),
        "budget": _remaining_budget(state),
    }
    content = json.dumps({**assessment, "fallback": fallback, "remaining_budget": _remaining_budget(state)}, ensure_ascii=False)
    updates = {
        "coverage": assessment,
        "assessments": state.get("assessments", 0) + 1,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events_extra": [event],
        "past_extra": [["assess", assessment.get("next_action")]],
    }
    return content, updates


def _known_paper_ids(state: dict) -> list[str]:
    return [paper.get("id") for paper in state.get("papers") or [] if paper.get("id")]


async def _tool_save_papers_to_workspace(state: dict, args: dict) -> tuple[str, dict]:
    """*特权工具*：收藏论文到工作区。首次调用中断请用户确认，恢复后只在授权范围内保存。"""
    known = set(_known_paper_ids(state))
    requested = []
    for raw in args.get("paper_ids") or []:
        pid = str(raw).strip()
        if pid and pid in known and pid not in requested:
            requested.append(pid)
    if not requested:
        return json.dumps({"error": "候选中没有可保存的论文，请先检索", "saved": []}, ensure_ascii=False), {}
    from langgraph.types import interrupt
    decision = interrupt({"type": "confirm_workspace", "paper_ids": requested, "hint": "确认将哪些论文加入当前工作区"})
    approved = []
    if isinstance(decision, dict):
        for raw in decision.get("approved_ids") or []:
            pid = str(raw).strip()
            if pid in requested and pid not in approved:
                approved.append(pid)
    if not approved:
        return json.dumps({"saved": [], "note": "用户未批准任何论文"}, ensure_ascii=False), {
            "gaps": list(state.get("gaps") or []) + ["用户未批准保存论文"],
            "events_extra": [{"step": "tool", "name": "save_papers_to_workspace", "summary": "用户拒绝保存", "approved": 0}],
        }
    papers_by_id = {paper.get("id"): paper for paper in state.get("papers") or []}
    saved = []
    for pid in approved:
        paper = PaperOut.model_validate(papers_by_id[pid])
        stored, _ = await asyncio.to_thread(library.save_paper, paper, pid)
        if state.get("workspace_id") and getattr(stored, "local_id", None):
            await asyncio.to_thread(workspaces.add, state["workspace_id"], stored.local_id)
        saved.append(pid)
    return json.dumps({"saved": saved}, ensure_ascii=False), {
        "events_extra": [{"step": "tool", "name": "save_papers_to_workspace", "summary": f"已保存 {len(saved)} 篇到工作区", "approved": len(saved)}],
        "past_extra": [["save_workspace", saved]],
    }


async def _tool_request_deep_analysis(state: dict, args: dict) -> tuple[str, dict]:
    """*特权工具*：全文深度分析。中断确认后只对批准且未读、预算内的论文执行。"""
    known = set(_known_paper_ids(state))
    requested = []
    for raw in args.get("paper_ids") or []:
        pid = str(raw).strip()
        if pid and pid in known and pid not in requested:
            requested.append(pid)
    if not requested:
        return json.dumps({"error": "候选中没有可深读的论文，请先检索", "analyzed": []}, ensure_ascii=False), {}
    from langgraph.types import interrupt
    decision = interrupt({"type": "confirm_analysis", "paper_ids": requested, "hint": "确认对哪些论文进行全文深度分析"})
    approved = []
    if isinstance(decision, dict):
        for raw in decision.get("approved_ids") or []:
            pid = str(raw).strip()
            if pid in requested and pid not in approved:
                approved.append(pid)
    if not approved:
        return json.dumps({"analyzed": [], "note": "用户未批准深度分析"}, ensure_ascii=False), {
            "events_extra": [{"step": "tool", "name": "request_deep_analysis", "summary": "用户拒绝深读", "approved": 0}],
        }
    read_ids = list(state.get("read_ids") or [])
    deep_reads = state.get("deep_reads", 0)
    max_deep = state.get("max_deep_reads", MAX_DEEP)
    papers_by_id = {paper.get("id"): paper for paper in state.get("papers") or []}
    pool = dict(state.get("evidence_pool") or {})
    attribution_map = dict(state.get("evidence_papers") or {})
    evidence_ids = list(state.get("evidence_ids") or [])
    gaps = list(state.get("gaps") or [])
    llm_calls = state.get("llm_calls", 0)
    events, pasts, analyzed = [], [], []
    for pid in approved:
        if deep_reads >= max_deep:
            gaps.append(f"深读预算不足:{pid}")
            continue
        if pid in read_ids:
            continue
        paper = PaperOut.model_validate(papers_by_id[pid])
        try:
            prepared = await fulltext.prepare(paper)
        except Exception:
            prepared = None
        if prepared is None:
            gaps.append(f"全文不可得:{pid}")
            read_ids.append(pid)
            deep_reads += 1
            events.append({"step": "read", "paper_id": pid, "reason": "全文不可得，保留摘要缺口"})
            pasts.append(["read", pid, 0])
            continue
        hybrid.index_prepared(paper, prepared, getattr(paper, "local_id", None))
        report, cache_hit = await cached_call(
            "analyze", {"ids": [pid], "depth": "deep"},
            lambda: analyze_tool.analyze_paper([pid], depth="deep", papers={pid: paper}, prepared={pid: prepared}, evidence_start=len(evidence_ids) + 1),
        )
        llm_calls += 0 if cache_hit else (report.get("llm_calls") or 0)
        item = (report.get("papers") or [{}])[0]
        new_pool, new_map, new_ids, new_items = _bind_report_evidence({**state, "evidence_pool": pool, "evidence_papers": attribution_map}, report)
        pool, attribution_map = new_pool, new_map
        evidence_ids = list(dict.fromkeys(evidence_ids + new_ids))
        read_ids.append(pid)
        deep_reads += 1
        analyzed.append(pid)
        if item.get("status") in {"failed", "not_found", "out_of_scope"}:
            gaps.append(f"分析失败:{pid}")
            events.append({"step": "read", "paper_id": pid, "tool": "analyze_paper", "reason": "精读解析失败，已保留全文索引"})
        else:
            events.append({"step": "read", "paper_id": pid, "tool": "analyze_paper", "reason": "补读正文并建立证据"})
        pasts.append(["read", pid, len(new_ids)])
    content = json.dumps({
        "analyzed": analyzed,
        "skipped": [pid for pid in approved if pid not in analyzed],
        "gaps_added": [gap for gap in gaps if gap not in (state.get("gaps") or [])][:5],
        "deep_reads_used": deep_reads,
    }, ensure_ascii=False)
    return content, {
        "read_ids": read_ids,
        "deep_reads": deep_reads,
        "gaps": gaps,
        "evidence_pool": pool,
        "evidence_papers": attribution_map,
        "evidence_ids": evidence_ids,
        "llm_calls": llm_calls,
        "events_extra": events,
        "past_extra": pasts,
    }


async def _tool_catalog(state: dict, name: str, args: dict) -> tuple[str, dict]:
    context = ToolContext(**{key: state[key] for key in ToolContext.model_fields if key in state})
    args = dict(args)
    if name in {"add_papers_to_workspace", "remove_paper_from_workspace"} or (name == "analyze_paper" and args.get("depth") == "deep"):
        # Keep product HITL for writes and costly deep analyses; never execute unapproved IDs.
        from langgraph.types import interrupt
        ids = args.get("paper_ids") or [args.get("paper_id")]
        ids = [ref for ref in ids if ref]
        decision = interrupt({"type": "confirm_analysis" if name == "analyze_paper" else "confirm_workspace",
                              "tool": name, "workspace_id": context.workspace_id or args.get("workspace_id"),
                              "paper_ids": ids, "hint": "确认本次操作及论文范围"})
        approved = decision.get("approved_ids", []) if isinstance(decision, dict) else []
        ids = [ref for ref in ids if ref in approved]
        if ids and isinstance(decision, dict) and decision.get("workspace_id") and not (context.workspace_id or args.get("workspace_id")):
            args["workspace_id"] = decision["workspace_id"]
        if not ids:
            return json.dumps({"status": "cancelled", "message": "用户未批准操作"}, ensure_ascii=False), {}
        if name == "remove_paper_from_workspace":
            args["paper_id"] = ids[0]
        else:
            args["paper_ids"] = ids
    report = await research_tools.execute(name, args, context)
    updates = {"events_extra": [{"step": "tool", "name": name, "summary": report.get("status", "completed")}],
               "past_extra": [[name, report.get("status")]]}
    if name in {"get_paper_details", "find_related_papers", "get_paper_references", "get_paper_citations", "retrieve_paper_evidence"}:
        updates["api_calls"] = state.get("api_calls", 0) + 1
    if name == "analyze_paper":
        pool, mapping, ids, items = _bind_report_evidence(state, report)
        updates.update(evidence_pool=pool, evidence_papers=mapping,
                       evidence_ids=list(dict.fromkeys([*(state.get("evidence_ids") or []), *ids])),
                       llm_calls=state.get("llm_calls", 0) + report.get("llm_calls", 0))
        report["new_evidence"] = items
    if name in {"retrieve_paper_evidence", "read_paper_context"}:
        pool = dict(state.get("evidence_pool") or {})
        mapping = dict(state.get("evidence_papers") or {})
        for item in report.get("evidence", report.get("blocks", [])):
            quote = item.get("quote") or item.get("text") or ""
            if not quote:
                continue
            eid = f"E{len(pool) + 1:04d}"
            item["evidence_id"] = eid
            pool[eid] = quote
            mapping[eid] = item["paper_id"]
        updates.update(evidence_pool=pool, evidence_papers=mapping, evidence_ids=list(pool))
    if name == "get_paper_analysis" and report.get("result"):
        pool = dict(state.get("evidence_pool") or {})
        mapping = dict(state.get("evidence_papers") or {})
        originals = []
        for paragraph in report["result"].get("source_paragraphs") or []:
            text = paragraph.get("text") or ""
            if not text:
                continue
            eid = f"E{len(pool) + 1:04d}"
            pool[eid] = text
            mapping[eid] = report["paper_id"]
            originals.append({"evidence_id": eid, "quote": text, "section": paragraph.get("section"), "material": "original"})
        report["original_evidence"] = originals
        updates.update(evidence_pool=pool, evidence_papers=mapping, evidence_ids=list(pool))
    records = []
    if name == "get_paper_details" and report.get("paper"):
        records = [{key: report["paper"].get(key) for key in ("id", "title", "authors", "year", "doi", "url")}]
    elif name == "list_workspace_papers":
        records = [{"paper_id": row["paper"]["id"], "title": row["paper"]["title"], "index": row["index"], "analysis": row["analysis"]}
                   for row in report.get("papers", [])]
    elif name in {"get_paper_references", "get_paper_citations"} and report.get("status") == "completed":
        records = [{"relation": name, "seed": report.get("paper_id"), "paper_id": paper["id"], "title": paper["title"]}
                   for paper in report.get("papers", [])]
    elif name == "find_related_papers":
        records = report.get("items", [])
    elif name == "analyze_research_trends":
        records = [{key: report[key] for key in ("sample_size", "year_distribution", "topics", "missing_analysis_count", "missing_year_count", "warnings")}]
    elif name == "query_knowledge_graph":
        records = [{"scope": report["scope"], "total_papers": report["total_papers"], "edges": report["edges"]}]
    if records:
        pool = dict(updates.get("evidence_pool", state.get("evidence_pool") or {}))
        mapping = dict(updates.get("evidence_papers", state.get("evidence_papers") or {}))
        sources = dict(state.get("evidence_sources") or {})
        record_evidence = []
        for record in records:
            eid = f"E{len(pool) + 1:04d}"
            text = json.dumps(record, ensure_ascii=False)
            pool[eid] = text
            sources[eid] = {"tool": name, "material": "sample_statistics" if name == "analyze_research_trends" else "metadata"}
            # Metadata/statistics must not count toward multi-paper scientific evidence coverage.
            record_evidence.append({"evidence_id": eid, "record": record, **sources[eid]})
        report["record_evidence"] = record_evidence
        updates.update(evidence_pool=pool, evidence_papers=mapping, evidence_ids=list(pool), evidence_sources=sources)
    # Metadata/discovery results become valid candidates for later steps.
    candidates = ([report["paper"]] if report.get("paper") else report.get("papers", []))
    known = {paper["id"]: paper for paper in state.get("papers") or []}
    for paper in candidates:
        if isinstance(paper, dict) and paper.get("id"):
            known[paper["id"]] = paper
    if candidates:
        updates["papers"] = list(known.values())
    return json.dumps(report, ensure_ascii=False), updates


def _catalog_handler(name):
    async def handle(state, args):
        return await _tool_catalog(state, name, args)
    return handle


TOOL_REGISTRY = {
    **{name: _catalog_handler(name) for name in research_tools.CATALOG},
    "search_papers": _tool_search_papers,
    "retrieve_paper_evidence": _tool_retrieve_paper_evidence,
    "analyze_paper": _tool_analyze_paper,
    "assess_coverage": _tool_assess_coverage,
    "save_papers_to_workspace": _tool_save_papers_to_workspace,
    "request_deep_analysis": _tool_request_deep_analysis,
}


async def _run_business_tool(state: dict, name: str, args: dict) -> tuple[str, dict]:
    handler = TOOL_REGISTRY.get(name)
    if handler is None:
        return json.dumps({"error": f"未知工具：{name}"}, ensure_ascii=False), {}
    return await handler(state, args or {})


def _is_privileged(call: dict) -> bool:
    args = call.get("args") or {}
    return call.get("name") in PRIVILEGED_TOOLS or (call.get("name") == "analyze_paper" and args.get("depth") == "deep")


async def tool_node(state: dict) -> dict:
    """统一工具执行节点：一次执行 react_agent 发出的全部工具调用（按顺序），结果以 ToolMessage 回注。"""
    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    calls = (_msg_attr(last, "tool_calls") if last is not None else None) or []
    acc = dict(state)
    tool_messages, events, pasts = [], [], []
    privileged = [_is_privileged(call) for call in calls]
    for index, call in enumerate(calls):
        name = call.get("name")
        call_id = call.get("id") or f"call_{name}"
        if privileged[index] and not (index == 0 and all(privileged)):
            tool_messages.append({"role": "tool", "name": name, "tool_call_id": call_id, "content": json.dumps(
                {"status": "deferred", "message": "需用户确认的操作每轮单独执行，请在其他结果返回后重新发起"}, ensure_ascii=False)})
            continue
        try:
            content, updates = await _run_business_tool(acc, name, call.get("args") or {})
        except Exception as exc:  # 工具异常不应中断整轮；HITL 中断（GraphInterrupt）除外
            from langgraph.errors import GraphInterrupt
            if isinstance(exc, GraphInterrupt):
                raise
            content = json.dumps({"error": f"工具执行失败：{str(exc)[:150]}"}, ensure_ascii=False)
            updates = {"events_extra": [{"step": "tool", "name": name, "summary": f"执行失败：{str(exc)[:120]}"}]}
        events.extend(updates.pop("events_extra", []))
        pasts.extend(updates.pop("past_extra", []))
        for key, value in updates.items():
            acc[key] = value
        tool_messages.append({"role": "tool", "name": name, "content": content, "tool_call_id": call_id})
    result = {key: acc[key] for key in acc if key not in state or acc[key] != state.get(key)}
    result["messages"] = tool_messages
    result["events"] = with_event(state, events)
    result["past_steps"] = pasts
    return result


# ---------------------------------------------------------------------------
# Judge 质量检查（四维：引用有效性 / 证据充分性 / 任务完成度 / 多篇覆盖）
# ---------------------------------------------------------------------------


def _review_skeleton() -> dict:
    return {"rounds": 0, "results": [], "to_verify": [], "semantics_done": False, "fixed_claims": [], "downgraded_claims": [], "coverage_note": None, "multi_paper_note": None}


def _pool_excerpt(pool: dict, evidence_ids: list, limit: int = 300) -> list[dict]:
    return [{"evidence_id": eid, "quote": str(pool.get(eid) or "")[:limit]} for eid in evidence_ids if eid in pool]


async def _llm_extract_claims(state: dict) -> list[dict]:
    pool = state.get("evidence_pool") or {}
    material = {
        "goal": state.get("goal"),
        "answer": state.get("answer") or "",
        "evidence": [{"evidence_id": eid, "quote": str(quote)[:200]} for eid, quote in list(pool.items())[:20]],
    }
    messages = [{"role": "system", "content": CLAIMS_SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    try:
        parsed = ClaimsOutput.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("主张提取不可用") from exc
    return [item.model_dump() for item in parsed.claims]


async def claims_node(state: dict) -> dict:
    """把当前答案规范化为可复核主张（c1..cn），并初始化复核记录。"""
    pool_items = [{"evidence_id": eid, "quote": str(quote)[:200]} for eid, quote in list((state.get("evidence_pool") or {}).items())[:20]]
    try:
        drafts, cache_hit = await cached_call(
            "claims",
            {"goal": state.get("goal"), "answer": state.get("answer") or "", "evidence": pool_items},
            lambda: _llm_extract_claims(state),
        )
        llm_delta = 0 if cache_hit else 1
        reason = "提取可复核主张"
    except llm.AnalysisError:
        drafts = [{"text": state.get("answer") or "", "evidence_ids": list((state.get("evidence_pool") or {}).keys())}]
        llm_delta = 0
        reason = "主张提取不可用，整段作为单条主张"
    claims = []
    for index, draft in enumerate(drafts, start=1):
        text = str(draft.get("text") or "").strip()
        if not text:
            continue
        claims.append({
            "claim_id": f"c{index}",
            "text": text[:MAX_CLAIM_CHARS],
            "evidence_ids": [str(eid) for eid in draft.get("evidence_ids") or []],
            "status": "pending",
        })
    if not claims and (state.get("answer") or "").strip():
        claims = [{"claim_id": "c1", "text": state["answer"].strip()[:MAX_CLAIM_CHARS], "evidence_ids": [], "status": "pending"}]
    review = state.get("review") or _review_skeleton()
    return {
        "claims": claims,
        "review": dict(review),
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events": with_event(state, {"step": "claims", "count": len(claims), "reason": reason}),
        "past_steps": [["claims", len(claims)]],
    }


def _rule_check(claim: dict, pool: dict) -> str | None:
    """程序化预检（不调用 LLM）：证据 ID 格式、越界、数字出处。"""
    evidence_ids = claim.get("evidence_ids") or []
    if any(not EVIDENCE_ID_RE.match(str(eid)) for eid in evidence_ids):
        return "证据ID无效"
    if any(eid not in pool for eid in evidence_ids):
        return "证据越界"
    quotes = [str(pool.get(eid) or "") for eid in evidence_ids]
    for match in llm.NUMBER.finditer(claim.get("text") or ""):
        token = match.group(0)
        if llm.significant(token):
            needle = token[:-1] if token.endswith("%") else token
            if not any(needle.replace(",", "") in quote.replace(",", "") for quote in quotes):
                return "数字无出处"
    return None


def _coverage_note(state: dict) -> str | None:
    """任务完成度：覆盖度评估仍显示缺口时给出复核提示。"""
    cov = state.get("coverage") or {}
    if not cov or cov.get("next_action") in (None, "synthesize"):
        return None
    if cov.get("coverage") == "sufficient":
        return None
    missing = cov.get("missing") or []
    if not missing and cov.get("coverage") not in ("insufficient", "partial"):
        return None
    return f"任务完成度：覆盖度{cov.get('coverage')}，尚缺 {'、'.join(missing) if missing else '关键方面'}"


def _multi_paper_note(state: dict, claims: list[dict]) -> str | None:
    """多篇覆盖：对比/综述类目标要求证据 spanning 至少两篇论文；单篇问题豁免。"""
    goal = state.get("goal") or ""
    comparative = bool(COMPARATIVE_HINT.search(goal)) or len(state.get("plan") or []) >= 2
    if not comparative:
        return None
    attribution_map = state.get("evidence_papers") or {}
    cited = set()
    for claim in claims:
        for eid in claim.get("evidence_ids") or []:
            pid = attribution_map.get(eid)
            if pid:
                cited.add(pid)
    if len(cited) >= 2:
        return None
    return f"多篇覆盖：对比/综述类目标要求主张引用至少两篇论文的证据，当前仅 {len(cited)} 篇"


async def _llm_judge(state: dict, pending: list[dict]) -> dict:
    pool = state.get("evidence_pool") or {}
    material = {
        "source_types": state.get("evidence_sources") or {},
        "source_rules": "metadata 仅支持书目信息、成员状态、引用记录和图谱关系；sample_statistics 仅支持当前样本统计，二者都不能支持论文科研结论或整个领域趋势。未特别标注的原文证据仍按原文核验。",
        "goal": state.get("goal"),
        "claims": [
            {"claim_id": claim["claim_id"], "text": claim["text"], "evidence": _pool_excerpt(pool, claim.get("evidence_ids") or [])}
            for claim in pending
        ],
    }
    messages = [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    try:
        parsed = JudgeBatch.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("语义复核不可用") from exc
    return {item.claim_id: item.model_dump() for item in parsed.reviews}


async def judge_node(state: dict) -> dict:
    """四维质量检查：程序化预检（引用有效性）→ 批量语义复核（证据充分性）→ 覆盖度与多篇覆盖注记。"""
    claims = [dict(claim) for claim in state.get("claims") or []]
    pending = [claim for claim in claims if claim.get("status") == "pending"]
    review = dict(state.get("review") or _review_skeleton())
    new_round = (review.get("rounds") or 0) + 1
    pool = state.get("evidence_pool") or {}
    results = list(review.get("results") or [])
    # 1) 引用有效性：确定性预检，失败的主张跳过 LLM 评审
    still_pending = []
    for claim in pending:
        issue = _rule_check(claim, pool)
        results.append({
            "claim_id": claim["claim_id"],
            "verdict": "rule_failed" if issue else "pass",
            "issue": issue,
            "action": "fix" if issue else "keep",
            "round": new_round,
        })
        if issue:
            claim["status"] = "rule_failed"
        else:
            still_pending.append(claim)
    # 2) 证据充分性：批量语义复核
    summary = {}
    llm_delta = 0
    action = "无待评审主张"
    if still_pending:
        judge_payload = {
            "goal": state.get("goal"),
            "claims": [
                {"claim_id": claim["claim_id"], "text": claim["text"], "evidence": _pool_excerpt(pool, claim.get("evidence_ids") or [])}
                for claim in still_pending
            ],
        }
        try:
            verdicts, cache_hit = await cached_call("judge", judge_payload, lambda: _llm_judge(state, still_pending))
            llm_delta = 0 if cache_hit else 1
            review["semantics_done"] = True
            action = "语义复核"
        except llm.AnalysisError:
            verdicts = {}
            action = "语义复核不可用，保留规则检查结果"
        by_id = {claim["claim_id"]: claim for claim in claims}
        for claim_id, verdict in verdicts.items():
            claim = by_id.get(claim_id)
            if claim is None:
                continue
            decision = verdict.get("action") or "keep"
            outcome = verdict.get("verdict") or "unknown"
            summary[claim_id] = outcome
            results.append({
                "claim_id": claim_id,
                "verdict": outcome,
                "issue": verdict.get("issue"),
                "action": decision,
                "round": new_round,
            })
            if decision == "keep" and outcome == "supported":
                claim["status"] = "supported"
            elif decision in ("keep", "flag"):
                claim["status"] = "flagged"
            # revise/drop：保持 pending，由 revise 反馈回 ReAct 循环
    # 3) 任务完成度 / 4) 多篇覆盖
    coverage_note = _coverage_note(state)
    multi_note = _multi_paper_note(state, claims)
    review["coverage_note"] = coverage_note
    review["multi_paper_note"] = multi_note
    has_work = bool(pending) or bool(coverage_note) or bool(multi_note)
    if not has_work:
        return {}
    review["results"] = results
    review["rounds"] = new_round
    return {
        "claims": claims,
        "review": review,
        "judge_round": new_round,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events": with_event(state, {"step": "judge", "round": new_round, "verdicts": summary, "action": action, "coverage_note": coverage_note, "multi_paper_note": multi_note}),
        "past_steps": [["judge", new_round]],
    }


def judge_route(state: dict) -> Literal["revise", "finalize"]:
    if state.get("cancelled") or budget_exhausted(state):
        return "finalize"
    review = state.get("review") or {}
    rounds = review.get("rounds") or state.get("judge_round") or 0
    failing = any(claim.get("status") in ("pending", "rule_failed") for claim in state.get("claims") or [])
    failing = failing or bool(review.get("coverage_note")) or bool(review.get("multi_paper_note"))
    if failing and rounds < MAX_JUDGE_ROUNDS:
        return "revise"
    return "finalize"


def _latest_directives(review: dict) -> dict:
    """每条主张最近一轮的处置指令（revise/drop/fix）。"""
    directives = {}
    for result in review.get("results") or []:
        if result.get("action") in ("revise", "drop", "fix"):
            directives[result["claim_id"]] = result
    return directives


async def revise_node(state: dict) -> dict:
    """定向修复：规则问题确定性处理；其余失败通过反馈消息回 ReAct 循环补充检索/补读。"""
    pool = state.get("evidence_pool") or {}
    claims = [dict(claim) for claim in state.get("claims") or []]
    review = dict(state.get("review") or _review_skeleton())
    fixed_claims = list(review.get("fixed_claims") or [])
    downgraded = list(review.get("downgraded_claims") or [])
    directives = _latest_directives(review)
    rule_fixed = dropped = 0
    feedback = []
    for claim in claims:
        claim_id = claim["claim_id"]
        directive = directives.get(claim_id) or {}
        if claim.get("status") == "rule_failed":
            issue = directive.get("issue")
            feedback.append(f"主张 {claim_id}（{issue}）：{claim.get('text', '')[:80]}")
            if issue in ("证据ID无效", "证据越界"):
                valid = [eid for eid in claim.get("evidence_ids") or [] if EVIDENCE_ID_RE.match(str(eid)) and eid in pool]
                if valid:
                    claim["evidence_ids"] = valid
                    claim["status"] = "fixed"
                    fixed_claims.append(claim_id)
                    rule_fixed += 1
                else:
                    claim["status"] = "dropped"
                    downgraded.append(claim_id)
                    dropped += 1
            else:
                claim["status"] = "downgraded"
                downgraded.append(claim_id)
        elif claim.get("status") == "pending" and directive.get("action") in ("revise", "drop"):
            feedback.append(f"主张 {claim_id}（{directive.get('issue') or directive.get('action')}）：{claim.get('text', '')[:80]}")
    if review.get("coverage_note"):
        feedback.append(review["coverage_note"])
    if review.get("multi_paper_note"):
        feedback.append(review["multi_paper_note"])
    rounds = review.get("rounds") or state.get("judge_round") or 1
    message = {
        "role": "user",
        "content": (
            f"【复核反馈·第{rounds}轮】以下结论未通过质量检查：\n- " + "\n- ".join(feedback)
            + "\n请针对上述问题补充检索、补读或修正，然后输出修正后的最终答案（保留证据ID标注）。"
        ),
    }
    review["fixed_claims"] = fixed_claims
    review["downgraded_claims"] = downgraded
    return {
        "claims": claims,
        "review": review,
        "messages": [message],
        "events": with_event(state, {"step": "revise", "round": rounds, "rule_fixed": rule_fixed, "dropped": dropped, "feedback": len(feedback)}),
        "past_steps": [["revise", rule_fixed + dropped]],
    }


def finalize_answer_node(state: dict) -> dict:
    """落地处置：删除/保守改写未通过的主张，汇总待核实清单，必要时加复核说明。"""
    answer = state.get("answer") or ""
    claims = [dict(claim) for claim in state.get("claims") or []]
    review = dict(state.get("review") or _review_skeleton())
    to_verify = list(review.get("to_verify") or [])
    downgraded = list(review.get("downgraded_claims") or [])
    for claim in claims:
        text = (claim.get("text") or "").strip()
        if not text:
            continue
        status = claim.get("status")
        if status == "dropped":
            answer = answer.replace(text, "（该结论证据不足，已移除）。")
            if claim["claim_id"] not in downgraded:
                downgraded.append(claim["claim_id"])
        elif status in ("downgraded", "pending"):
            conservative = text.rstrip("。") + "（该结论证据不足，待核实）。"
            answer = answer.replace(text, conservative)
            if claim["claim_id"] not in downgraded:
                downgraded.append(claim["claim_id"])
            if text not in to_verify:
                to_verify.append(text)
        elif status == "flagged":
            if text not in to_verify:
                to_verify.append(text)
    if to_verify or not review.get("semantics_done"):
        answer = answer.rstrip() + "\n\n【复核说明】部分结论证据不足，已按保守表述处理或标记待核实。"
        if not review.get("semantics_done"):
            answer += "语义复核未完成，以上结论请谨慎参考。"
    review["to_verify"] = to_verify
    review["downgraded_claims"] = downgraded
    review["rounds"] = state.get("judge_round", review.get("rounds", 0))
    return {
        "answer": answer,
        "claims": claims,
        "review": review,
        "stop_reason": state.get("stop_reason") or "目标覆盖完成",
        "events": with_event(state, {"step": "finalize", "to_verify": len(to_verify), "downgraded": len(downgraded), "reason": "复核处置落地"}),
        "past_steps": [["finalize", len(to_verify)]],
    }


# ---------------------------------------------------------------------------
# Gate / Finish
# ---------------------------------------------------------------------------


def gate_node(state: dict) -> dict:
    """检查取消/暂停标记：取消则转入 finish；暂停则以 paused 中断挂起，恢复后清除标记继续。"""
    task_id = state.get("task_id")
    if not task_id:
        return {}
    flag = _flag_of(task_id)
    if flag == "cancel":
        return {"cancelled": True, "stop_reason": "用户取消"}
    if flag == "pause":
        from langgraph.types import interrupt
        interrupt({"type": "paused", "goal": state.get("goal")})
        _clear_flag(task_id)
    return {}


def finish_node(state: dict) -> dict:
    task_id = state.get("task_id")
    reason = state.get("stop_reason") or budget_exhausted(state) or "没有可继续的动作"
    if task_id and (state.get("cancelled") or is_cancel_requested(task_id)):
        _clear_flag(task_id)
    if limited(state) and state.get("papers"):
        reason = "来源限流，返回部分结果"
    return {
        "stop_reason": reason,
        "events": with_event(state, {"step": "stop", "reason": reason}),
    }


def build(checkpointer=None):
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("react", react_node)
    graph.add_node("plan", plan_node)
    graph.add_node("gate", gate_node)
    graph.add_node("react_agent", react_agent)
    graph.add_node("tool_node", tool_node)
    graph.add_node("gate_judge", gate_node)
    graph.add_node("claims", claims_node)
    graph.add_node("judge", judge_node)
    graph.add_node("revise", revise_node)
    graph.add_node("finalize", finalize_answer_node)
    graph.add_node("gate_done", gate_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", route_route, {"plan": "plan", "react": "react"})
    graph.add_edge("react", "gate")
    graph.add_edge("plan", "gate")
    graph.add_conditional_edges("gate", gate_route, {"finish": "finish", "continue": "react_agent"})
    graph.add_conditional_edges("react_agent", react_route, {"tools": "tool_node", "done": "gate_judge"})
    graph.add_edge("tool_node", "react_agent")
    graph.add_conditional_edges("gate_judge", gate_route, {"finish": "finish", "continue": "claims"})
    graph.add_edge("claims", "judge")
    graph.add_conditional_edges("judge", judge_route, {"revise": "revise", "finalize": "finalize"})
    graph.add_edge("revise", "react_agent")
    graph.add_edge("finalize", "gate_done")
    graph.add_edge("gate_done", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


_GRAPH = None
_CHECKPOINTER = None


async def make_async_checkpointer():
    if os.getenv("RESEARCH_CHECKPOINT", "memory") == "memory":
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


def initial_state(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False, task_id: str | None = None, mode: str = "auto", context: dict | None = None) -> dict:
    return {
        **ToolContext.model_validate(context or {}).model_dump(),
        "task_id": task_id,
        "goal": goal,
        "mode": mode,
        "route": None,
        "year_from": year_from,
        "year_to": year_to,
        "auto_approve": auto_approve,
        "papers": [],
        "read_ids": [],
        "evidence_ids": [],
        "evidence_pool": {},
        "evidence_papers": {},
        "gaps": [],
        "events": [],
        "past_steps": [],
        "messages": [],
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


def list_tasks(limit: int = 50) -> list[dict]:
    """列出调研任务（重启恢复入口）：状态、时间、目标与中断摘要。"""
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
            "interrupt": interrupt.get("type") if isinstance(interrupt, dict) else None,
        })
    return tasks


async def recover_state(task_id: str) -> dict | None:
    """从检查点恢复任务状态：waiting（存在中断）/ running（节点未完成）/ done。

    图尚未构建时会先构建；检查点文件路径跨进程稳定（backend/data 下的 sqlite）。
    检查点不可用时回退到 research_tasks 行。
    """
    compiled = await graph()
    config = {"configurable": {"thread_id": task_id}}
    snapshot = None
    try:
        snapshot = await compiled.aget_state(config)
    except Exception:
        snapshot = None
    if snapshot is not None and snapshot.values:
        state = dict(snapshot.values)
        paused = None
        try:
            interrupts = getattr(snapshot, "interrupts", None) or []
            if interrupts:
                paused = getattr(interrupts[0], "value", interrupts[0])
        except Exception:
            paused = None
        nxt = list(getattr(snapshot, "next", None) or [])
        if paused is not None:
            status = "waiting"
        elif nxt:
            status = "running"
        else:
            status = "done"
        payload = public_state(state)
        payload["status"] = status
        payload["interrupt"] = paused
        return {"task_id": task_id, "status": status, "state": payload, "interrupt": paused, "next": nxt}
    current = get_task(task_id)
    if current is None:
        return None
    return {"task_id": task_id, "status": current.get("status"), "state": current, "interrupt": current.get("interrupt"), "next": []}


def _compact_update(update: dict) -> dict:
    """SSE 进度事件的精简状态片段：只保留 ID 与关键标量，绝不携带论文/消息全量负载。"""
    compact = {}
    for key, value in (update or {}).items():
        if key == "papers" and isinstance(value, list):
            compact[key] = [str(paper.get("id")) for paper in value][:20]
        elif key == "messages" and isinstance(value, list):
            compact[key] = {"count": len(value), "last_role": _msg_attr(value[-1], "role") if value else None}
        elif key in ("events", "past_steps") and isinstance(value, list):
            compact[key] = value[-3:]
        else:
            compact[key] = value
    return compact


async def stream_run(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False, task_id: str | None = None, mode: str = "auto", context: dict | None = None):
    """通过 astream 运行图并产出 SSE 友好的进度事件（run() 之外的独立入口，互不影响）。

    产出顺序：每个节点更新一个 {"type": "node", "node", "update"}；结束后产出
    {"type": "interrupt", ...}（挂起，客户端走 resume 端点）或 {"type": "state", ...}（完成）。
    """
    task_id = task_id or uuid4().hex
    thread_id = task_id
    config = {"configurable": {"thread_id": thread_id}}
    compiled = await graph()
    initial = initial_state(goal, year_from, year_to, auto_approve, task_id=task_id, mode=mode, context=context)
    async for chunk in compiled.astream(initial, config, stream_mode="updates"):
        if "__interrupt__" in chunk:
            break
        for node, update in chunk.items():
            yield {"type": "node", "node": node, "update": _compact_update(update)}
    snapshot = await compiled.aget_state(config)
    state = dict(snapshot.values)
    paused = None
    try:
        interrupts = getattr(snapshot, "interrupts", None) or []
        if interrupts:
            paused = getattr(interrupts[0], "value", interrupts[0])
    except Exception:
        paused = None
    payload = public_state(state)
    if paused is not None:
        payload["interrupt"] = paused
        payload["status"] = "waiting"
        save_task(task_id, thread_id, "waiting", payload)
        yield {"type": "interrupt", "task_id": task_id, "status": "waiting", "interrupt": paused}
        return
    payload["status"] = "done"
    save_task(task_id, thread_id, "done", payload)
    yield {"type": "state", "task_id": task_id, "status": "done", "task": payload}


async def run(goal: str = "", year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False, resume=None, task_id: str | None = None, mode: str = "auto", context: dict | None = None) -> dict:
    from langgraph.types import Command

    task_id = task_id or uuid4().hex
    thread_id = task_id
    config = {"configurable": {"thread_id": thread_id}}
    compiled = await graph()
    if resume is not None:
        result = await compiled.ainvoke(Command(resume=resume), config)
        result = {
            **result,
            "events": list(result.get("events") or []) + [{"step": "recover", "reason": "任务恢复后继续执行", "task_id": task_id}],
        }
    else:
        result = await compiled.ainvoke(initial_state(goal, year_from, year_to, auto_approve, task_id=task_id, mode=mode, context=context), config)
    payload = public_state(result)
    paused = _interrupt_payload(result)
    if paused is not None:
        payload["interrupt"] = paused
        payload["status"] = "waiting"
        save_task(task_id, thread_id, "waiting", payload)
        return {"task_id": task_id, "status": "waiting", **payload}
    payload["status"] = "done"
    save_task(task_id, thread_id, "done", payload)
    return {"task_id": task_id, "status": "done", **payload}


async def resume_task(task_id: str, value=None) -> dict:
    """恢复被中断（计划审批/工具确认/暂停）的任务：value 作为 interrupt 的返回值传入。"""
    return await run("", resume=True if value is None else value, task_id=task_id)

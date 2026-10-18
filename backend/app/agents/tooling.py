"""Researcher tooling: domain catalog, tool handlers, HITL confirmation, operation ledger and tool_node.

Tools are deterministic functions; tool_node is the graph node that executes them in order.
Side-effecting operations (workspace writes, deep analysis) run through `_once`, keyed by the
stable operation ID task_id + tool_call_id + full arguments, so a node replay after an interrupt or
restart returns the recorded result instead of writing again, while a new tool call always runs.
"""

import asyncio
import contextvars
import json
import os
import re

from pydantic import ValidationError

from app.agents import store
from app.agents.prompts import ASSESS_SYSTEM, DOMAIN_PROMPTS
from app.agents.state import (
    ABSTRACT_CHARS, DOMAINS, MAX_ASSESS, MAX_ASSESS_PAPERS, MAX_DEEP, MAX_EXTRA_SEARCH, MAX_TOOL_PAPERS,
    SEARCH_PAGE_SIZE, CoverageAssessment, QuoteVerifier, add_evidence, budget_exhausted, msg_attr,
    remaining_budget, with_event,
)
from app.infrastructure import llm
from app.schemas import PaperOut
from app.services.analysis import attribution, evidence_analysis as ea, fulltext
from app.services.papers import library, workspaces
from app.services.retrieval import hybrid, search
import app.tools.analyze_paper as analyze_tool
from app.tools import research as research_tools
from app.tools.research_scope import ToolContext

ASSESS_TOOL = {
    "name": "assess_coverage", "module": "discovery",
    "description": "评估当前候选与证据的覆盖度，返回 next_action（search_more/paginate/read_next/synthesize/stop）和 new_query；由你据此决定是否换检索词、翻页（search_papers 的 page）或补取证据。",
    "parameters": {"type": "object", "properties": {}},
}
ALL_TOOLS = (*research_tools.CHAT_TOOLS, ASSESS_TOOL)
DOMAIN_TOOLS = {domain: {tool["name"] for tool in ALL_TOOLS if tool["module"] == domain} for domain in DOMAINS}
# Tools a domain needs to satisfy its own preconditions (e.g. a recommendation seed must be in the library).
DOMAIN_DEPENDENCIES = {
    "discovery": {"list_workspace_papers", "add_papers_to_workspace"},
    "workspace": {"search_papers", "get_paper_details"},
    "analysis": {"search_papers", "get_paper_details", "list_workspace_papers", "add_papers_to_workspace"},
    "knowledge": {"list_workspace_papers", "get_paper_analysis"},
}
LEGACY_TOOLS = {"save_papers_to_workspace", "request_deep_analysis"}
PRIVILEGED_TOOLS = {"add_papers_to_workspace", "remove_paper_from_workspace", "save_papers_to_workspace", "request_deep_analysis"}
API_TOOLS = {"search_papers", "get_paper_details", "find_related_papers", "get_paper_references", "get_paper_citations", "retrieve_paper_evidence"}
LLM_TOOLS = {"analyze_paper", "request_deep_analysis"}
PRODUCER_TOOLS = API_TOOLS | {"list_workspace_papers"}
TOOL_TIMEOUT = float(os.getenv("RESEARCH_TOOL_TIMEOUT", "120"))
LLM_TOOL_TIMEOUT = float(os.getenv("RESEARCH_LLM_TOOL_TIMEOUT", "600"))
WAITING_PROCESSING = {"pending", "running"}

_CURRENT_OP: contextvars.ContextVar[str | None] = contextvars.ContextVar("research_tool_op", default=None)


# ---------------------------------------------------------------------------
# 领域目录
# ---------------------------------------------------------------------------


def allowed_tools(state: dict) -> set[str]:
    legacy = LEGACY_TOOLS if state.get("accessible_paper_ids") is None else set()
    domains = [domain for domain in state.get("domains") or [] if domain in DOMAINS or domain == "*"]
    if not domains or "*" in domains:
        return {tool["name"] for tool in ALL_TOOLS} | legacy
    names = set()
    for domain in domains:
        names |= DOMAIN_TOOLS[domain] | DOMAIN_DEPENDENCIES[domain]
    return names | legacy


def catalog_for(state: dict) -> list[dict]:
    allowed = allowed_tools(state)
    return [tool for tool in ALL_TOOLS if tool["name"] in allowed]


def domain_prompt(state: dict) -> str:
    modules = {tool["module"] for tool in catalog_for(state)}
    return "\n".join(DOMAIN_PROMPTS[domain] for domain in DOMAINS if domain in modules)


# ---------------------------------------------------------------------------
# 覆盖度评估（规则回退 + LLM + 预算降级）
# ---------------------------------------------------------------------------


def limited(state: dict) -> bool:
    return any("限流" in str(value) or "429" in str(value) for value in (state.get("source_errors") or {}).values())


def route(state: dict):
    """规则路由：assess_coverage 在 LLM 不可用时的确定性回退。"""
    if budget_exhausted(state):
        return "stop"
    papers = state.get("papers") or []
    if limited(state) and not papers:
        return "stop"
    queries = state.get("queries") or []
    index = state.get("query_index", 0)
    if len(papers) < 5 and state.get("extra_search", 0) < state.get("max_extra_search", MAX_EXTRA_SEARCH) and index + 1 < len(queries):
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
    decision = route(state)
    if decision == "expand":
        return {"coverage": "insufficient", "missing": ["候选数量或相关性"], "next_action": "search_more", "rationale": "候选不足，按计划继续补检索", "new_query": None}
    if decision == "read":
        return {"coverage": "partial", "missing": ["关键事实的全文证据"], "next_action": "read_next", "rationale": "已有候选但缺少全文证据，先精读", "new_query": None}
    if decision == "synthesize":
        return {"coverage": "sufficient", "missing": [], "next_action": "synthesize", "rationale": "证据充分，进入综合", "new_query": None}
    return {"coverage": "partial" if state.get("papers") else "insufficient", "missing": [], "next_action": "stop", "rationale": budget_exhausted(state) or "没有可继续的动作", "new_query": None}


def _downgrade_action(state: dict) -> str:
    read_ids = set(state.get("read_ids") or [])
    unread = [paper for paper in state.get("papers") or [] if paper.get("id") not in read_ids]
    if unread and state.get("deep_reads", 0) < state.get("max_deep_reads", MAX_DEEP):
        return "read_next"
    if state.get("papers"):
        return "synthesize"
    return "stop"


YEAR_TOKEN = re.compile(r"\b(19|20)\d{2}\b")


def _clean_query(query, state: dict) -> str | None:
    """设置年份硬约束时剔除检索式中夹带的年份词，约束只由接口参数执行。"""
    if not isinstance(query, str):
        return None
    cleaned = query.strip()
    if not cleaned:
        return None
    if state.get("year_from") is not None and state.get("year_to") is not None:
        cleaned = re.sub(r"\s+", " ", YEAR_TOKEN.sub("", cleaned)).strip(" ()")
    return cleaned[:300] or None


def _assess_cache_payload(state: dict) -> dict:
    read_ids = set(state.get("read_ids") or [])
    papers = [{
        "id": paper.get("id"),
        "title": paper.get("title"),
        "abstract": str(paper.get("abstract") or "")[:ABSTRACT_CHARS],
        "已读": paper.get("id") in read_ids,
    } for paper in (state.get("papers") or [])[:MAX_ASSESS_PAPERS]]
    return {
        "goal": state.get("goal"),
        "year_from": state.get("year_from"),
        "year_to": state.get("year_to"),
        "queries": state.get("queries") or [],
        "query_index": state.get("query_index", 0),
        "papers": papers,
        "read_ids": sorted(read_ids),
        "evidence_count": len(state.get("evidence_ids") or []),
        "gaps": (state.get("gaps") or [])[-5:],
        "source_errors": state.get("source_errors") or {},
        "search_pages": state.get("search_pages") or {},
    }


async def _llm_assess(state: dict) -> dict:
    material = {**_assess_cache_payload(state), "remaining_budget": remaining_budget(state)}
    messages = [{"role": "system", "content": ASSESS_SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    try:
        assessment = CoverageAssessment.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("覆盖度评估不可用") from exc
    result = assessment.model_dump()
    new_query = result.get("new_query")
    result["new_query"] = str(new_query).strip()[:300] if new_query else None
    return result


async def run_coverage_assessment(state: dict) -> tuple[dict, int, bool]:
    """返回 (assessment, llm_delta, fallback)。失败的 LLM 尝试同样计入 llm_delta。"""
    reason = budget_exhausted(state)
    if reason:
        return {"coverage": "partial" if state.get("papers") else "insufficient", "missing": [], "next_action": "stop", "rationale": reason, "new_query": None}, 0, False
    try:
        assessment, cache_hit = await store.cached_call("assess", _assess_cache_payload(state), lambda: _llm_assess(state))
        llm_delta, fallback = (0 if cache_hit else 1), False
    except llm.AnalysisError:
        assessment, llm_delta, fallback = _rule_assess(state), 1, True
    if assessment.get("next_action") in ("search_more", "paginate") and (
        state.get("assessments", 0) + 1 > MAX_ASSESS
        or state.get("extra_search", 0) >= state.get("max_extra_search", MAX_EXTRA_SEARCH)
        or remaining_budget(state)["api_calls_left"] == 0
    ):
        assessment = {**assessment, "next_action": _downgrade_action(state),
                      "rationale": f"{assessment.get('rationale', '')}；补检索预算不足，降级处理"}
    return assessment, llm_delta, fallback


def assessment_updates(state: dict, assessment: dict, llm_delta: int, fallback: bool) -> dict:
    return {
        "coverage": assessment,
        "assessments": state.get("assessments", 0) + 1,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events_extra": [{
            "step": "assess",
            "coverage": assessment.get("coverage"),
            "next_action": assessment.get("next_action"),
            "reason": (assessment.get("rationale") or "") + ("；评估不可用，使用规则回退" if fallback else ""),
            "budget": remaining_budget(state),
        }],
        "past_extra": [["assess", assessment.get("next_action")]],
    }


async def _tool_assess_coverage(state: dict, args: dict) -> tuple[str, dict]:
    assessment, llm_delta, fallback = await run_coverage_assessment(state)
    content = json.dumps({**assessment, "fallback": fallback, "remaining_budget": remaining_budget(state)}, ensure_ascii=False)
    return content, assessment_updates(state, assessment, llm_delta, fallback)


# ---------------------------------------------------------------------------
# HITL 确认、操作账本、后台处理续接
# ---------------------------------------------------------------------------


def _confirm(kind: str, requested: list[str], **payload) -> tuple[list[str], dict]:
    from langgraph.types import interrupt
    decision = interrupt({"type": kind, **payload, "paper_ids": requested, "op_id": _CURRENT_OP.get()})
    decision = decision if isinstance(decision, dict) else {}
    granted = {str(ref).strip() for ref in decision.get("approved_ids") or []}
    return [ref for ref in requested if ref in granted], decision


_MISSING = object()

# 账本写入失败（副作用已发生但记录未提交）时的进程内不确定结果：重放优先取它，
# 避免同一操作在同进程内被盲目重做；跨重启仍可能重做，属已声明的不确定窗口。
_UNCOMMITTED: dict[str, object] = {}


async def _once(state: dict, tool: str, args: dict, producer) -> tuple[dict, bool]:
    """执行副作用操作；同一稳定操作 ID 重放时返回账本结果。返回 (result, replayed)。"""
    task_id, op_id = state.get("task_id"), _CURRENT_OP.get()
    if not task_id or not op_id:
        return await producer(), False
    key = store.operation_key(task_id, op_id, tool, args)
    hit = await asyncio.to_thread(store.operation_get, key)
    if hit is not None:
        return hit, True
    pending = _UNCOMMITTED.get(key, _MISSING)
    if pending is not _MISSING:
        return pending, True
    result = await producer()
    try:
        await asyncio.to_thread(store.operation_put, key, task_id, op_id, tool, args, result)
    except Exception:
        _UNCOMMITTED[key] = result
        raise
    return result, False


def _operation(state: dict, tool: str, status: str, replayed: bool = False, **extra) -> list[dict]:
    record = {"op_id": _CURRENT_OP.get(), "tool": tool, "status": status, "replayed": replayed, **extra}
    return list(state.get("operations") or []) + [record]


def _processing_updates(state: dict, entries: list[dict]) -> dict:
    """把后台处理阶段写入 pending_processing / gaps，供等待后续接与部分结束说明。"""
    if not entries:
        return {}
    pending = {item.get("local_id") or item.get("paper_id"): item for item in state.get("pending_processing") or []}
    gaps = list(state.get("gaps") or [])
    for entry in entries:
        key = entry.get("local_id") or entry.get("paper_id")
        status = entry.get("status")
        if not key:
            continue
        if status in WAITING_PROCESSING:
            pending[key] = {"paper_id": entry.get("paper_id"), "local_id": entry.get("local_id"), "status": status}
            gap = f"等待后台处理:{entry.get('paper_id') or key}"
        else:
            pending.pop(key, None)
            gap = {"failed": f"全文处理失败:{entry.get('paper_id') or key}",
                   "unavailable": f"全文不可得:{entry.get('paper_id') or key}",
                   None: f"未排队后台处理:{entry.get('paper_id') or key}"}.get(status)
        if gap and gap not in gaps:
            gaps.append(gap)
    return {"pending_processing": list(pending.values()), "gaps": gaps}


def _entries_from_results(results: list[dict]) -> list[dict]:
    return [{"paper_id": item.get("paper_id"), "local_id": item["local_id"],
             "status": (item.get("processing") or {}).get("status"), "message": (item.get("processing") or {}).get("message")}
            for item in results or [] if item.get("local_id") and "processing" in item]


# ---------------------------------------------------------------------------
# 证据绑定
# ---------------------------------------------------------------------------


def _report_evidence(state: dict, report: dict, tool: str, texts: dict | None = None) -> tuple[list[dict], int]:
    """分析报告的引文须在原文/摘要中逐字核对后才入池；返回 (证据条目, 未通过核对数)。"""
    verifier = QuoteVerifier(state, texts)
    items, rejected = [], 0
    for paper_report in report.get("papers") or []:
        pid = paper_report.get("paper_id")
        for field in paper_report.get("fields") or []:
            for ev in field.get("evidence") or []:
                quote = str(ev.get("quote") or "").strip()
                if len(quote) < 8:
                    continue
                located = verifier.check(pid, quote)
                if not located:
                    rejected += 1
                    continue
                items.append({"paper_id": pid, "quote": quote, "section": ev.get("section"),
                              "page_number": ev.get("page"), "tool": tool, **located})
    return items, rejected


def _chunk_evidence(rows: list[dict], tool: str) -> list[dict]:
    items = []
    for row in rows:
        quote = row.get("quote") or row.get("text") or ""
        if not quote:
            continue
        items.append({
            "paper_id": row.get("paper_id"), "quote": quote, "tool": tool,
            "material": "abstract" if row.get("basis") == "abstract" else "original",
            "section": row.get("section"), "page_number": row.get("page_number"), "chunk_id": row.get("chunk_id"),
            "doc_version": f"index:{row['created_at']}" if row.get("created_at") else None, "verified": True,
        })
    return items


# ---------------------------------------------------------------------------
# 业务工具
# ---------------------------------------------------------------------------


def _compact_papers(papers: list, limit: int = 8) -> list[dict]:
    return [{
        "id": paper.get("id"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "abstract": str(paper.get("abstract") or "")[:200],
    } for paper in papers[:limit]]


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
    key = store.cache_key("search", {
        "query": query, "page": page, "page_size": page_size,
        "sort": sort, "source": source, "year_from": year_from, "year_to": year_to,
    })
    cached = store.cache_get(key)
    if cached is not None:
        result, api_delta, from_cache = cached, 0, True
    else:
        raw = await search.search_papers(query, page, page_size, sort, source, year_from, year_to)
        result = {
            "papers": [item.model_dump() if hasattr(item, "model_dump") else item for item in raw.get("papers") or []],
            "source_errors": raw.get("source_errors") or {},
            "source_retry_after": raw.get("source_retry_after") or {},
        }
        api_delta, from_cache = 1, False
        if not result["source_errors"]:
            store.cache_put(key, result)
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
        "step": "search", "query": query, "page": page, "count": len(result.get("papers") or []),
        "errors": errors, "cached": from_cache,
        "reason": "来源部分失败，保留已取得结果" if errors and known else "检索候选",
    }
    content = json.dumps({
        "found": len(result.get("papers") or []),
        "papers": _compact_papers(result.get("papers") or []),
        "workspace_total": len(known),
        "source_errors": errors,
        "cached": from_cache,
    }, ensure_ascii=False)
    return content, {
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


def _has_scope(state: dict) -> bool:
    return bool(state.get("workspace_id") or state.get("current_paper_id") or state.get("selected_paper_ids"))


async def _tool_retrieve_paper_evidence(state: dict, args: dict) -> tuple[str, dict]:
    if _has_scope(state) or args.get("query"):
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
    items = []
    for hit in hits:
        actual_id = hit.get("paper_id") or paper_id
        if not actual_id:
            continue
        for item in attribution.evidence_from_chunks(actual_id, [hit], start=1):
            items.append({"paper_id": item.paper_id, "quote": item.quote, "section": item.section,
                          "tool": "retrieve_paper_evidence", "chunk_id": hit.get("chunk_id"),
                          "material": "abstract" if hit.get("basis") == "abstract" else "original"})
    evidence_updates, added = add_evidence(state, items)
    new_evidence = [{"evidence_id": item["evidence_id"], "quote": item["quote"][:250], "section": item.get("section"),
                     "material": item["material"]} for item in added]
    content = json.dumps({"count": len(new_evidence), "evidence": new_evidence[:10],
                          "total_in_pool": len(evidence_updates["evidence_pool"])}, ensure_ascii=False)
    return content, {
        **evidence_updates,
        "events_extra": [{"step": "tool", "name": "retrieve_paper_evidence", "summary": f"{paper_id or 'workspace'} 取回证据 {len(new_evidence)} 条"}],
        "past_extra": [["retrieve", paper_id or "workspace", len(new_evidence)]],
    }


def _evidence_brief(items: list[dict], limit: int = 10) -> list[dict]:
    return [{"evidence_id": item["evidence_id"], "quote": item["quote"][:200], "section": item.get("section"),
             "material": item.get("material")} for item in items[:limit]]


async def _tool_analyze_paper(state: dict, args: dict) -> tuple[str, dict]:
    if args.get("depth") == "deep" and not _has_scope(state):
        return await _tool_request_deep_analysis(state, {"paper_ids": args.get("paper_ids")})
    if _has_scope(state):
        return await _tool_catalog(state, "analyze_paper", args)
    ids = []
    for raw in args.get("paper_ids") or []:
        pid = str(raw).strip()
        if pid and pid not in ids:
            ids.append(pid)
    ids = ids[:MAX_TOOL_PAPERS]
    if not ids:
        return json.dumps({"error": "请提供至少一篇论文"}, ensure_ascii=False), {}
    if remaining_budget(state)["llm_calls_left"] <= 0:
        return json.dumps({"error": "模型调用次数用尽，本调用未执行"}, ensure_ascii=False), {
            "events_extra": [{"step": "tool", "name": "analyze_paper", "summary": "LLM 预算不足，调用被拦截"}],
        }
    try:
        report, cache_hit = await store.cached_call(
            "analyze", {"ids": ids, "depth": "standard"},
            lambda: analyze_tool.analyze_paper(ids, depth="standard", llm_calls_left=remaining_budget(state)["llm_calls_left"]),
        )
    except (llm.AnalysisError, ValueError) as exc:
        return json.dumps({"error": str(exc)[:200]}, ensure_ascii=False), {"llm_calls": state.get("llm_calls", 0) + 1}
    llm_delta = 0 if cache_hit else (report.get("llm_calls") or 0)
    items, rejected = await asyncio.to_thread(_report_evidence, state, report, "analyze_paper")
    evidence_updates, added = add_evidence(state, items)
    content = json.dumps({
        "status": report.get("status"),
        "papers": [{
            "paper_id": item.get("paper_id"),
            "title": item.get("title"),
            "status": item.get("status"),
            "basis": item.get("basis"),
            "fields": [{"label": field.get("label"), "status": field.get("status"), "content": str(field.get("content") or "")[:200]}
                       for field in (item.get("fields") or [])],
        } for item in report.get("papers") or []][:MAX_TOOL_PAPERS],
        "new_evidence": _evidence_brief(added),
        "unverified_quotes": rejected,
    }, ensure_ascii=False)
    return content, {
        **evidence_updates,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events_extra": [{"step": "tool", "name": "analyze_paper", "summary": f"分析 {len(ids)} 篇，新增证据 {len(added)} 条，未通过原文核对 {rejected} 条"}],
        "past_extra": [["analyze", ids, len(added)]],
    }


def _known_paper_ids(state: dict) -> list[str]:
    return [paper.get("id") for paper in state.get("papers") or [] if paper.get("id")]


def _requested_ids(state: dict, args: dict) -> list[str]:
    known = set(_known_paper_ids(state))
    requested = []
    for raw in args.get("paper_ids") or []:
        pid = str(raw).strip()
        if pid and pid in known and pid not in requested:
            requested.append(pid)
    return requested


async def _tool_save_papers_to_workspace(state: dict, args: dict) -> tuple[str, dict]:
    """*特权工具（兼容旧名）*：收藏候选论文到工作区，确认后只处理批准范围。"""
    requested = _requested_ids(state, args)
    if not requested:
        return json.dumps({"error": "候选中没有可保存的论文，请先检索", "saved": []}, ensure_ascii=False), {}
    approved, _ = _confirm("confirm_workspace", requested, hint="确认将哪些论文加入当前工作区")
    if not approved:
        return json.dumps({"status": "denied", "saved": [], "note": "用户未批准任何论文"}, ensure_ascii=False), {
            "gaps": list(state.get("gaps") or []) + ["用户未批准保存论文"],
            "operations": _operation(state, "save_papers_to_workspace", "denied"),
            "events_extra": [{"step": "tool", "name": "save_papers_to_workspace", "summary": "用户拒绝保存", "approved": 0}],
        }
    workspace_id = state.get("workspace_id")
    papers_by_id = {paper.get("id"): paper for paper in state.get("papers") or []}

    async def produce():
        from app.services.papers import processing
        results = []
        for pid in approved:
            try:
                stored, _ = await asyncio.to_thread(library.save_paper, PaperOut.model_validate(papers_by_id[pid]), pid)
                local_id = getattr(stored, "local_id", None)
                item = {"paper_id": pid, "local_id": local_id, "status": "saved"}
                if workspace_id and local_id:
                    await asyncio.to_thread(workspaces.add, workspace_id, local_id)
                    item["processing"] = await asyncio.to_thread(processing.ensure_queued, local_id, True)
                results.append(item)
            except Exception as exc:
                results.append({"paper_id": pid, "status": "failed", "message": str(exc)[:120]})
        return {"results": results}

    result, replayed = await _once(state, "save_papers_to_workspace", {"workspace_id": workspace_id, "paper_ids": approved}, produce)
    saved = [item["paper_id"] for item in result["results"] if item["status"] == "saved"]
    status = "ok" if len(saved) == len(approved) else ("partial" if saved else "failed")
    content = json.dumps({"status": status, "saved": saved, "results": result["results"], "replayed": replayed}, ensure_ascii=False, default=str)
    return content, {
        **_processing_updates(state, _entries_from_results(result["results"])),
        "operations": _operation(state, "save_papers_to_workspace", status, replayed, paper_ids=approved),
        "events_extra": [{"step": "tool", "name": "save_papers_to_workspace", "summary": f"已保存 {len(saved)} 篇到工作区", "approved": len(saved)}],
        "past_extra": [["save_workspace", saved]],
    }


async def _tool_request_deep_analysis(state: dict, args: dict) -> tuple[str, dict]:
    """*特权工具（兼容旧名）*：全文深度分析；确认后逐篇执行，每篇单独记入操作账本。"""
    requested = _requested_ids(state, args)
    if not requested:
        return json.dumps({"error": "候选中没有可深读的论文，请先检索", "analyzed": []}, ensure_ascii=False), {}
    approved, _ = _confirm("confirm_analysis", requested, hint="确认对哪些论文进行全文深度分析")
    if not approved:
        return json.dumps({"status": "denied", "analyzed": [], "note": "用户未批准深度分析"}, ensure_ascii=False), {
            "operations": _operation(state, "request_deep_analysis", "denied"),
            "events_extra": [{"step": "tool", "name": "request_deep_analysis", "summary": "用户拒绝深读", "approved": 0}],
        }
    acc = dict(state)
    read_ids = list(state.get("read_ids") or [])
    deep_reads = state.get("deep_reads", 0)
    max_deep = state.get("max_deep_reads", MAX_DEEP)
    papers_by_id = {paper.get("id"): paper for paper in state.get("papers") or []}
    gaps = list(state.get("gaps") or [])
    llm_calls = state.get("llm_calls", 0)
    events, pasts, analyzed, outcomes = [], [], [], {}
    for pid in approved:
        if deep_reads >= max_deep:
            gaps.append(f"深读预算不足:{pid}")
            continue
        if remaining_budget(acc)["llm_calls_left"] <= 0:
            gaps.append(f"模型额度不足:{pid}")
            continue
        if pid in read_ids:
            continue
        paper = PaperOut.model_validate(papers_by_id[pid])

        async def produce(pid=pid, paper=paper):
            try:
                prepared = await fulltext.prepare(paper)
            except Exception:
                prepared = None
            if prepared is None:
                return {"status": "unavailable"}
            hybrid.index_prepared(paper, prepared, getattr(paper, "local_id", None))
            report, cache_hit = await store.cached_call(
                "analyze", {"ids": [pid], "depth": "deep"},
                lambda: analyze_tool.analyze_paper([pid], depth="deep", papers={pid: paper}, prepared={pid: prepared}, evidence_start=1),
            )
            items, rejected = await asyncio.to_thread(_report_evidence, acc, report, "request_deep_analysis", {pid: prepared.text})
            item = (report.get("papers") or [{}])[0]
            return {"status": "analyzed", "paper_status": item.get("status"), "evidence": items, "rejected": rejected,
                    "llm_calls": 0 if cache_hit else (report.get("llm_calls") or 0)}

        outcome, replayed = await _once(acc, "request_deep_analysis", {"paper_id": pid}, produce)
        outcomes[pid] = {"status": outcome["status"], "replayed": replayed}
        read_ids.append(pid)
        deep_reads += 1
        if outcome["status"] == "unavailable":
            gaps.append(f"全文不可得:{pid}")
            events.append({"step": "read", "paper_id": pid, "reason": "全文不可得，保留摘要缺口"})
            pasts.append(["read", pid, 0])
            continue
        llm_calls += outcome.get("llm_calls") or 0
        evidence_updates, added = add_evidence(acc, outcome.get("evidence") or [])
        acc.update(evidence_updates)
        analyzed.append(pid)
        if outcome.get("paper_status") in {"failed", "not_found", "out_of_scope"}:
            gaps.append(f"分析失败:{pid}")
            events.append({"step": "read", "paper_id": pid, "tool": "analyze_paper", "reason": "精读解析失败，已保留全文索引"})
        else:
            events.append({"step": "read", "paper_id": pid, "tool": "analyze_paper", "reason": "补读正文并建立证据"})
        pasts.append(["read", pid, len(added)])
    content = json.dumps({
        "analyzed": analyzed,
        "skipped": [pid for pid in approved if pid not in analyzed],
        "outcomes": outcomes,
        "gaps_added": [gap for gap in gaps if gap not in (state.get("gaps") or [])][:5],
        "deep_reads_used": deep_reads,
    }, ensure_ascii=False)
    status = "ok" if analyzed and len(analyzed) == len(approved) else ("partial" if analyzed else "failed")
    return content, {
        **{key: acc[key] for key in ("evidence_pool", "evidence_papers", "evidence_sources", "evidence_items", "evidence_ids") if key in acc},
        "read_ids": read_ids,
        "deep_reads": deep_reads,
        "gaps": gaps,
        "llm_calls": llm_calls,
        "operations": _operation(state, "request_deep_analysis", status, any(item["replayed"] for item in outcomes.values()), paper_ids=approved),
        "events_extra": events,
        "past_extra": pasts,
    }


def _metadata_records(name: str, report: dict) -> list[dict]:
    if name == "get_paper_details" and report.get("paper"):
        paper = report["paper"]
        return [{"paper_id": paper.get("id"), "record": {key: paper.get(key) for key in ("id", "title", "authors", "year", "doi", "url")}}]
    if name == "list_workspace_papers":
        return [{"paper_id": row["paper"]["id"], "record": {"paper_id": row["paper"]["id"], "title": row["paper"]["title"],
                                                            "index": row.get("index"), "analysis": row.get("analysis"),
                                                            "processing": row.get("processing")}}
                for row in report.get("papers", [])]
    if name in {"get_paper_references", "get_paper_citations"} and report.get("status") == "completed":
        return [{"paper_id": paper["id"], "record": {"relation": name, "seed": report.get("paper_id"), "paper_id": paper["id"], "title": paper["title"]}}
                for paper in report.get("papers", [])]
    if name == "find_related_papers":
        return [{"paper_id": None, "record": item} for item in report.get("items", [])]
    if name == "analyze_research_trends":
        return [{"paper_id": None, "record": {key: report.get(key) for key in ("sample_size", "year_distribution", "topics", "missing_analysis_count", "missing_year_count", "warnings")}}]
    if name == "query_knowledge_graph":
        return [{"paper_id": None, "record": {"scope": report.get("scope"), "total_papers": report.get("total_papers"), "edges": report.get("edges")}}]
    return []


async def _tool_catalog(state: dict, name: str, args: dict) -> tuple[str, dict]:
    context = ToolContext(**{key: state[key] for key in ToolContext.model_fields if key in state})
    args = dict(args)
    if name == "analyze_paper":
        llm_left = remaining_budget(state)["llm_calls_left"]
        if llm_left <= 0:
            return json.dumps({"tool": name, "status": "blocked", "message": "模型调用次数用尽，本调用未执行"}, ensure_ascii=False), {
                "operations": _operation(state, name, "budget_blocked"),
                "events_extra": [{"step": "tool", "name": name, "summary": "LLM 预算不足，调用被拦截"}],
                "past_extra": [[name, "blocked"]],
            }
        # 逐篇约消耗一次模型额度：按剩余额度截断范围，不足的留给后续轮次或缺口说明。
        if args.get("paper_ids"):
            args["paper_ids"] = list(args["paper_ids"])[:llm_left]
    privileged = name in {"add_papers_to_workspace", "remove_paper_from_workspace"} or (name == "analyze_paper" and args.get("depth") == "deep")
    updates: dict = {}
    if privileged:
        if name == "analyze_paper" and args.get("depth") == "deep" and remaining_budget(state)["deep_reads_left"] <= 0:
            return json.dumps({"tool": name, "status": "blocked", "message": "深读篇数用尽，本调用未执行"}, ensure_ascii=False), {
                "operations": _operation(state, name, "budget_blocked"),
                "events_extra": [{"step": "tool", "name": name, "summary": "深读预算不足，调用被拦截"}],
                "past_extra": [[name, "blocked"]],
            }
        ids = [ref for ref in (args.get("paper_ids") or [args.get("paper_id")]) if ref]
        approved, decision = _confirm("confirm_analysis" if name == "analyze_paper" else "confirm_workspace", ids, tool=name,
                                      workspace_id=context.workspace_id or args.get("workspace_id"), hint="确认本次操作及论文范围")
        if not approved:
            return json.dumps({"tool": name, "status": "denied", "message": "用户未批准操作"}, ensure_ascii=False), {
                "operations": _operation(state, name, "denied"),
                "events_extra": [{"step": "tool", "name": name, "summary": "用户拒绝操作"}],
                "past_extra": [[name, "denied"]],
            }
        if decision.get("workspace_id") and not (context.workspace_id or args.get("workspace_id")):
            args["workspace_id"] = decision["workspace_id"]
        if name == "remove_paper_from_workspace":
            args["paper_id"] = approved[0]
        else:
            args["paper_ids"] = approved
        if name == "analyze_paper" and args.get("depth") == "deep":
            approved = approved[:remaining_budget(state)["deep_reads_left"]]
            args["paper_ids"] = approved
        report, replayed = await _once(state, name, {"args": args, "workspace_id": context.workspace_id},
                                       lambda: research_tools.execute(name, args, context))
        report = dict(report)
        updates["operations"] = _operation(state, name, report.get("status", "completed"), replayed, paper_ids=approved)
        if name == "analyze_paper" and args.get("depth") == "deep" and approved:
            updates["deep_reads"] = state.get("deep_reads", 0) + len(approved)
    else:
        report = await research_tools.execute(name, args, context)
    updates.update({"events_extra": [{"step": "tool", "name": name, "summary": report.get("status", "completed")}],
                    "past_extra": [[name, report.get("status")]]})
    acc = {**state}
    if name in API_TOOLS:
        updates["api_calls"] = state.get("api_calls", 0) + 1
    if name == "analyze_paper":
        items, rejected = await asyncio.to_thread(_report_evidence, state, report, "analyze_paper")
        evidence_updates, added = add_evidence(acc, items)
        acc.update(evidence_updates)
        updates.update(evidence_updates, llm_calls=state.get("llm_calls", 0) + (report.get("llm_calls") or 0))
        report["new_evidence"] = _evidence_brief(added)
        report["unverified_quotes"] = rejected
    if name in {"retrieve_paper_evidence", "read_paper_context"}:
        rows = report.get("evidence", report.get("blocks", []))
        evidence_updates, added = add_evidence(acc, _chunk_evidence(rows, name))
        acc.update(evidence_updates)
        updates.update(evidence_updates)
        by_chunk = {item.get("chunk_id"): item["evidence_id"] for item in added}
        for row in rows:
            if row.get("chunk_id") in by_chunk:
                row["evidence_id"] = by_chunk[row["chunk_id"]]
        updates.update(_processing_updates(acc, report.get("processing") or []))
    if name == "get_paper_analysis" and report.get("result"):
        verifier = QuoteVerifier(state)
        items = []
        for paragraph in report["result"].get("source_paragraphs") or []:
            text = paragraph.get("text") or ""
            located = await asyncio.to_thread(verifier.check, report.get("paper_id"), text)
            if located:
                items.append({"paper_id": report.get("paper_id"), "quote": text, "section": paragraph.get("section"),
                              "tool": name, **located})
        evidence_updates, added = add_evidence(acc, items)
        acc.update(evidence_updates)
        updates.update(evidence_updates)
        report["original_evidence"] = _evidence_brief(added, limit=20)
    if name in {"add_papers_to_workspace", "remove_paper_from_workspace"}:
        updates.update(_processing_updates(acc, _entries_from_results(report.get("results") or [])))
    records = _metadata_records(name, report)
    if records:
        material = "sample_statistics" if name == "analyze_research_trends" else "metadata"
        evidence_updates, added = add_evidence(acc, [
            {"paper_id": record["paper_id"], "quote": json.dumps(record["record"], ensure_ascii=False, default=str),
             "material": material, "tool": name, "verified": False}
            for record in records
        ])
        acc.update(evidence_updates)
        updates.update(evidence_updates)
        report["record_evidence"] = [{"evidence_id": item["evidence_id"], "record": record["record"], "tool": name, "material": material}
                                     for item, record in zip(added, records)]
    candidates = [report["paper"]] if report.get("paper") else report.get("papers", [])
    known = {paper["id"]: paper for paper in state.get("papers") or []}
    for paper in candidates:
        if isinstance(paper, dict) and paper.get("id"):
            known[paper["id"]] = paper
    if candidates:
        updates["papers"] = list(known.values())
    return json.dumps(report, ensure_ascii=False, default=str), updates


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


# ---------------------------------------------------------------------------
# tool_node：每次调用前检查取消/暂停/期限/预算，依赖本轮结果的调用延后到下一轮
# ---------------------------------------------------------------------------


def _loop_halt(state: dict) -> str | None:
    flag = store.flag_of(state.get("task_id"))
    if flag == "cancel" or state.get("cancelled"):
        return "用户取消"
    if flag == "pause":
        return "用户请求暂停"
    if store.deadline_passed(state.get("task_id")):
        return "任务超时"
    return None


def _call_blocked(state: dict, name: str, args: dict) -> str | None:
    budget = remaining_budget(state)
    if name in API_TOOLS and budget["api_calls_left"] == 0:
        return "学术接口次数用尽"
    if (name in LLM_TOOLS or name == "assess_coverage") and budget["llm_calls_left"] == 0:
        return "模型调用次数用尽"
    return None


def _refs_of(args: dict) -> list[str]:
    refs = [args.get("paper_id"), *(args.get("paper_ids") or [])]
    return [str(ref).strip() for ref in refs if ref]


def _depends_on_turn(args: dict, known_before: set[str], acc: dict) -> bool:
    if args.get("chunk_id"):
        return True
    produced = set(_known_paper_ids(acc)) - known_before
    return any(ref in produced for ref in _refs_of(args))


async def tool_node(state: dict) -> dict:
    """统一工具执行节点：按顺序执行 react_agent 发出的工具调用，结果以 tool 消息回注。"""
    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    calls = (msg_attr(last, "tool_calls") if last is not None else None) or []
    acc = dict(state)
    tool_messages, events, pasts = [], [], []
    allowed = allowed_tools(state)
    privileged = [_is_privileged(call) for call in calls]
    known_before = set(_known_paper_ids(state)) | set(state.get("selected_paper_ids") or []) | {state.get("current_paper_id")}
    producer_seen = False
    halt = None

    def reply(name, call_id, payload):
        tool_messages.append({"role": "tool", "name": name, "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)})

    for index, call in enumerate(calls):
        name = call.get("name")
        call_id = call.get("id") or f"call_{name}"
        args = call.get("args") or {}
        halt = halt or _loop_halt(acc)
        if halt:
            reply(name, call_id, {"status": "skipped", "message": f"{halt}，本调用未执行"})
            continue
        if name in TOOL_REGISTRY and name not in allowed:
            acc["domains"] = ["*"]
            allowed = allowed_tools(acc)
            events.append({"step": "tool", "name": name, "summary": "工具不在当前领域目录，目录已扩展为全部工具"})
            reply(name, call_id, {"status": "deferred", "message": "该工具不在当前领域目录中，目录已扩展为全部工具，请在下一轮重新发起"})
            continue
        if privileged[index] and not (index == 0 and all(privileged)):
            reply(name, call_id, {"status": "deferred", "message": "需用户确认的操作每轮单独执行，请在其他结果返回后重新发起"})
            continue
        if producer_seen and _depends_on_turn(args, known_before, acc):
            reply(name, call_id, {"status": "deferred", "message": "参数依赖本轮前序工具的返回值，请在下一轮调用"})
            continue
        blocked = _call_blocked(acc, name, args)
        if blocked:
            reply(name, call_id, {"status": "skipped", "message": f"{blocked}，本调用未执行"})
            continue
        token = _CURRENT_OP.set(call_id)
        try:
            runner = _run_business_tool(acc, name, args)
            timeout = LLM_TOOL_TIMEOUT if name in LLM_TOOLS or name == "assess_coverage" else TOOL_TIMEOUT
            # HITL 确认走 interrupt 立即抛出，等待不计入此处；批准后执行统一施加调用超时。
            content, updates = await asyncio.wait_for(runner, timeout=timeout)
        except asyncio.TimeoutError:
            content = json.dumps({"status": "failed", "error": {"code": "timeout", "retryable": True, "message": f"工具执行超过 {int(timeout)} 秒"}}, ensure_ascii=False)
            updates = {"events_extra": [{"step": "tool", "name": name, "summary": "执行超时"}]}
        except Exception as exc:
            from langgraph.errors import GraphInterrupt
            if isinstance(exc, GraphInterrupt):
                raise
            content = json.dumps({"error": f"工具执行失败：{str(exc)[:150]}"}, ensure_ascii=False)
            updates = {"events_extra": [{"step": "tool", "name": name, "summary": f"执行失败：{str(exc)[:120]}"}]}
        finally:
            _CURRENT_OP.reset(token)
        events.extend(updates.pop("events_extra", []))
        pasts.extend(updates.pop("past_extra", []))
        acc.update(updates)
        tool_messages.append({"role": "tool", "name": name, "content": content, "tool_call_id": call_id})
        if name in PRODUCER_TOOLS:
            producer_seen = True
    if halt == "用户取消":
        acc["cancelled"] = True
        acc["stop_reason"] = "用户取消"
    if halt:
        events.append({"step": "tool_halt", "reason": halt})
    result = {key: acc[key] for key in acc if key not in state or acc[key] != state.get(key)}
    result["messages"] = tool_messages
    result["events"] = with_event(state, events)
    result["past_steps"] = pasts
    return result

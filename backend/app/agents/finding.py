"""Paper finding: model plan -> academic APIs -> model selection.

No lexical intent/quantity/topic rules. Python enforces resource limits and
record provenance; the two model tools own interpretation and relevance.
"""
import asyncio

from app.agents import store
from app.agents.state import hard_stop, remaining_budget, with_event
from app.schemas import PaperOut
from app.services.retrieval.search import merge_papers
from app.tools.decompose_question import decompose_question
from app.tools.rerank_papers import rerank_papers
from app.tools.search_papers import search_papers


def event(state, step, reason, **extra):
    return with_event(state, {"step": step, "reason": reason, **extra})


def stopped(state):
    return state.get("stop_reason") or hard_stop(state)


async def plan_node(state):
    base = {"output_mode": "bibliography", "recommended_ids": [], "recommendation_reasons": {},
            "papers": [], "result_limit": None, "workflow_stage": "prepare",
            "search_plan": {"pipeline": "semantic-v1"}}
    if stopped(state):
        return {**base, "stop_reason": stopped(state)}
    try:
        history = [state["conversation_history"]] if state.get("conversation_history") else []
        report, cached = await store.cached_call("finding-plan", {"task": state.get("task_id"), "goal": state["goal"], "history": history},
            lambda: asyncio.wait_for(decompose_question(state["goal"], history, max_attempts=remaining_budget(state)["llm_calls_left"]), timeout=90))
    except (TimeoutError, ValueError):
        report, cached = {"status": "failed", "error": "理解检索需求超时，请重试。", "llm_calls": 1}, False
    base["llm_calls"] = state.get("llm_calls", 0) + (0 if cached else report["llm_calls"])
    if report["status"] != "completed":
        answer = report.get("clarification_question") or report.get("error") or "暂时无法理解检索需求。"
        return {**base, "answer": answer, "stop_reason": report["status"],
                "events": event(state, "prepare", answer)}
    plan = {**report, "pipeline": "semantic-v1"}
    constraints = {**report["constraints"], "exclusions": report["exclusions"], "core_topics": report["core_topics"]}
    # Explicit API filters take precedence over model interpretation.
    for name in ("year_from", "year_to"):
        if state.get(name) is not None:
            constraints[name] = state[name]
    plan["constraints"] = constraints
    return {**base, "search_plan": plan, "queries": report["queries"], "query_index": 0,
            "goal": report["resolved_question"], "result_limit": report["target_count"],
            "year_from": constraints.get("year_from"), "year_to": constraints.get("year_to"),
            "answer": report["interpretation"], "workflow_action": "search",
            "events": event(state, "prepare", report["interpretation"], queries=report["queries"])}


def plan_route(state):
    return "finish" if stopped(state) else "search"


async def search_node(state):
    if stopped(state):
        return {"stop_reason": stopped(state)}
    if remaining_budget(state)["api_calls_left"] < 1:
        return {"stop_reason": "检索预算已用尽"}
    query = state["queries"][state["query_index"]]
    plan = state["search_plan"]
    args = {"query": query, "page_size": 20, "source": "all", "sort": "date" if plan["constraints"].get("temporal") == "recent" else "relevance",
            "year_from": state.get("year_from"), "year_to": state.get("year_to")}
    try:
        report, _ = await store.cached_call("finding-search", {"task": state.get("task_id"), **args},
            lambda: asyncio.wait_for(search_papers(**args), timeout=90))
    except (TimeoutError, ValueError):
        report = {"papers": [], "source_errors": {"search": "检索超时或返回数据无效"}}
    pool = merge_papers([[PaperOut.model_validate(p) for p in state.get("papers") or []],
                         [PaperOut.model_validate(p) for p in report.get("papers") or []]], "relevance")
    errors = dict(state.get("source_errors") or {})
    for source in (report.get("source_counts") or {}):
        errors.pop(source, None)
    errors.update(report.get("source_errors") or {})
    count = state.get("search_count", 0) + 1
    return {"papers": [p.model_dump(mode="json") for p in pool], "source_errors": errors,
            "source_retry_after": report.get("source_retry_after") or {},
            "api_calls": state.get("api_calls", 0) + 1, "search_count": count, "extra_search": count - 1,
            "workflow_stage": "search", "workflow_searches": [*(state.get("workflow_searches") or []), query],
            "events": event(state, "search", f"取得 {len(report.get('papers') or [])} 篇候选，合并去重后 {len(pool)} 篇",
                            query=query, errors=report.get("source_errors") or {}, warnings=report.get("source_warnings") or {})}


async def select_node(state):
    if stopped(state):
        return {"stop_reason": stopped(state)}
    plan = state["search_plan"]
    report = {"status": "completed", "recommended_ids": [], "reasons": {}, "recommended": [],
              "enough": False, "llm_calls": 0, "shortfall_reason": "这次检索没有取得候选论文。"}
    if state.get("papers"):
        try:
            report, cached = await store.cached_call("finding-select", {"task": state.get("task_id"), "plan": plan, "papers": state["papers"]},
                lambda: asyncio.wait_for(rerank_papers(plan["resolved_question"], state["papers"],
                    target_count=plan["target_count"], constraints=plan["constraints"], max_attempts=remaining_budget(state)["llm_calls_left"]), timeout=90))
            if cached:
                report = {**report, "llm_calls": 0}
        except (TimeoutError, ValueError):
            report = {**report, "status": "failed", "llm_calls": 1,
                      "shortfall_reason": "候选已取得，但相关性筛选超时或失败，请重试。"}
    if report["status"] == "failed":
        # Preserve only previously validated choices, never promote unjudged candidates.
        return {"llm_calls": state.get("llm_calls", 0) + report["llm_calls"],
                "stop_reason": "相关性筛选失败", "answer": report["shortfall_reason"],
                "events": event(state, "select", report["shortfall_reason"])}
    ids = report["recommended_ids"]
    queries = list(state["queries"])
    next_query = (report.get("next_query") or "").strip()
    seen = {q.casefold() for q in state.get("workflow_searches") or []}
    if not report["enough"] and next_query and next_query.casefold() not in seen:
        queries = [*queries[:state["query_index"] + 1], next_query,
                   *[q for q in queries[state["query_index"] + 1:] if q.casefold() != next_query.casefold()]]
    calls = state.get("llm_calls", 0) + report["llm_calls"]
    can_search = (not report["enough"] and state["query_index"] + 1 < len(queries)
                  and state.get("search_count", 0) < min(3, 1 + state.get("max_extra_search", 2))
                  and remaining_budget(state)["api_calls_left"] > 0
                  and calls + 2 <= state.get("max_llm_calls", 24))
    answer = f"{plan['interpretation']}\n\n为你找到 {len(ids)} 篇相关论文，下面列出推荐及具体理由。" if ids else f"{plan['interpretation']}\n\n本次候选中还没有筛选出足够相关的论文。"
    if len(ids) < plan["target_count"]:
        answer += f"\n目标是 {plan['target_count']} 篇，本次筛选出 {len(ids)} 篇；没有用无关论文补足数量。"
    if plan["constraints"].get("temporal") == "early":
        answer += "\n已优先展示本次相关候选中发表较早的论文，不代表已穷尽该领域的最早文献。"
    if state.get("source_errors"):
        answer += "\n部分数据源查询失败，结果可能不完整：" + "；".join(f"{key}：{value}" for key, value in state["source_errors"].items())
    return {"recommended_ids": ids, "recommendation_reasons": report["reasons"], "llm_calls": calls,
            "queries": queries, "query_index": state["query_index"] + (1 if can_search else 0),
            "workflow_action": "search" if can_search else "finish", "workflow_stage": "select", "answer": answer,
            "review": {"final_status": "bibliography"},
            "coverage": {"coverage": "sufficient" if report["enough"] else "partial", "rationale": report.get("shortfall_reason")},
            "events": event(state, "select", f"大模型根据完整需求和标题摘要筛选出 {len(ids)} 篇", count=len(ids))}


def select_route(state):
    return "search" if not stopped(state) and state.get("workflow_action") == "search" else "finish"

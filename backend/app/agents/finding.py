"""A single researcher chooses tools and reads their observations in a loop.

No planning JSON gate, lexical relevance rules, extra ranking model, or writes
to the user's library. Only tool-returned records can become recommendation cards.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from pydantic import ValidationError

from app.agents import store
from app.agents.prompts import FINDING_SYSTEM
from app.agents.state import hard_stop, is_cancel_requested, with_event
from app.infrastructure import llm
from app.schemas import PaperOut
from app.services.retrieval import search
from app.tools import finding_tools as tools

log = logging.getLogger(__name__)
MAX_TURNS, MAX_SEARCHES, MAX_DETAILS = 8, 4, 4
MAX_PARAMETER_RETRIES, MAX_SECONDS = 2, 180


def _seconds(state):
    return max(0, MAX_SECONDS - (time.time() - state.get("finding_started_at", time.time())))


def _halt(state, *, model=False):
    if state.get("stop_reason"):
        return state["stop_reason"]
    if state.get("cancelled") or is_cancel_requested(state.get("task_id")):
        return "用户取消"
    if store.deadline_passed(state.get("task_id")) or _seconds(state) <= 0:
        return "论文搜索已达到时间上限"
    if model:
        if state.get("finding_turns", 0) >= min(MAX_TURNS, state.get("max_react", MAX_TURNS)):
            return "论文搜索已达到模型轮次上限"
        return hard_stop(state)
    return None


def _event(state, step, reason, **extra):
    state["events"] = with_event(state, {"step": step, "reason": reason, **extra})


def _source_notes(state):
    notes = []
    for source, failure in (state.get("finding_source_failures") or {}).items():
        status = f" HTTP {failure['http_status']}" if failure.get("http_status") else ""
        retry = failure.get("retry_after_seconds") or 0
        message = f"{search.SOURCE_LABELS.get(source, source)}{status}：{failure.get('message') or failure['status']}"
        if retry:
            message += f"（该次调用提示约 {retry} 秒后可重试）"
        notes.append(message)
    notes.extend(state.get("finding_notices") or [])
    return list(dict.fromkeys(notes))


def finalize(state, reason=None, answer=None):
    """Never promote unjudged candidates when stopping or exhausting a budget."""
    result = dict(state)
    ids = result.get("recommended_ids") or []
    result["recommended_ids"] = ids
    text = (answer or result.get("answer") or "").strip()
    if not text:
        if result.get("finding_submitted"):
            text = f"本次筛选出 {len(ids)} 篇相关论文。" if ids else "本轮尚未选出符合要求的论文，没有用无关候选补足数量。"
        elif result.get("search_count"):
            text = "本轮已执行检索，但没有提交完成相关性判断的论文；未将未筛选候选作为推荐。"
        else:
            text = "尚未取得可推荐的论文。"
    if reason and reason not in text:
        text += f"\n\n本轮已停止：{reason}。"
    failures = _source_notes(result)
    if failures:
        text += "\n\n本轮来源提示（包含检索过程中发生的部分失败）：\n" + "\n".join(f"- {note}" for note in failures)
    if result.get("search_count") and not result.get("papers") and result.get("finding_all_failed"):
        text += "\n检索来源调用失败，不能据此判断该主题没有论文。"
    result.update(answer=text, stop_reason=reason or ("论文筛选完成" if result.get("finding_submitted") else "研究员结束检索"),
                  finding_done=True, workflow_stage="select" if result.get("finding_submitted") else "finding")
    return result


def _start(state):
    state = dict(state)
    # These legacy channels have append reducers; returning a full old value
    # would duplicate them at every graph step. Native history has its own key.
    state.pop("messages", None)
    state.pop("past_steps", None)
    if state.get("finding_started_at") is None:
        state.update(finding_started_at=time.time(), finding_turns=0, finding_messages=[], finding_pending=[],
                     finding_search_cache={}, finding_query_counts={}, finding_details={}, finding_detail_count=0,
                     finding_arg_errors={}, finding_source_failures={}, finding_notices=[], finding_aliases={},
                     finding_submitted=False, finding_done=False, finding_all_failed=True,
                     output_mode="bibliography", recommended_ids=[], recommendation_reasons={}, papers=[],
                     search_plan={"pipeline": "finding-tools-v1"}, workflow_stage="finding", answer="")
        _event(state, "prepare", "根据你的需求搜索论文，并结合实际结果决定是否补搜或查看详情")
    return state


def _search_limit(state):
    # Dedicated limits must not expand the task's existing budget preset.
    return min(MAX_SEARCHES, 1 + state.get("max_extra_search", MAX_SEARCHES - 1))


def _context(state):
    return {"today": datetime.now(timezone(timedelta(hours=8))).date().isoformat(), "goal": state["goal"],
            "conversation_history": state.get("conversation_history") or "",
            "explicit_year_filter": {"year_from": state.get("year_from"), "year_to": state.get("year_to")},
            "remaining": {"model_turns": max(0, min(MAX_TURNS, state.get("max_react", MAX_TURNS)) - state.get("finding_turns", 0)),
                          "searches": max(0, _search_limit(state) - state.get("search_count", 0)),
                          "details": max(0, MAX_DETAILS - state.get("finding_detail_count", 0)),
                          "api_calls": max(0, state.get("max_api_calls", 8) - state.get("api_calls", 0)),
                          "seconds": round(_seconds(state))}}


async def agent_node(state):
    state = _start(state)
    reason = _halt(state, model=True)
    if reason:
        return finalize(state, reason)
    messages = [{"role": "system", "content": FINDING_SYSTEM},
                {"role": "user", "content": json.dumps(_context(state), ensure_ascii=False)},
                *(state.get("finding_messages") or [])]
    state["llm_calls"] = state.get("llm_calls", 0) + 1
    state["finding_turns"] = state.get("finding_turns", 0) + 1
    try:
        response = await asyncio.wait_for(llm.complete_with_tools(messages, tools.FUNCTION_TOOLS), timeout=min(90, _seconds(state)))
    except llm.AnalysisError as exc:
        return finalize(state, exc.message)
    except TimeoutError:
        return finalize(state, "模型理解或决策超时")
    state["finding_messages"] = [*state["finding_messages"], response]
    state["finding_pending"] = response.get("tool_calls") or []
    if not state["finding_pending"]:
        return finalize(state, answer=response.get("content"))
    _event(state, "finding", "正在执行论文检索工具", tools=[c["function"]["name"] for c in state["finding_pending"]])
    return state


def agent_route(state):
    return "finish" if state.get("finding_done") or state.get("stop_reason") else "tools"


def _error(code, message, *, retryable=False, **extra):
    return {"status": "failed", "error": {"code": code, "message": message, "retryable": retryable, **extra}}


def _parameter_error(state, name, details):
    counts = dict(state.get("finding_arg_errors") or {})
    count = counts[name] = counts.get(name, 0) + 1
    state["finding_arg_errors"] = counts
    return _error("invalid_arguments", "工具参数不合法，请根据字段错误修正；不要重复原参数。" if count <= MAX_PARAMETER_RETRIES
                  else "该工具参数纠错次数已用尽，请采用其他工具或说明无法继续。",
                  retryable=count <= MAX_PARAMETER_RETRIES, fields=details,
                  remaining_corrections=max(0, MAX_PARAMETER_RETRIES - count))


def _has_api_budget(state):
    return state.get("api_calls", 0) < state.get("max_api_calls", 8)


async def _search(state, args):
    lower = max([v for v in (state.get("year_from"), args.year_from) if v is not None], default=None)
    upper = min([v for v in (state.get("year_to"), args.year_to) if v is not None], default=None)
    if lower is not None and upper is not None and lower > upper:
        return _parameter_error(state, "search_papers", [{"field": "year_from/year_to", "message": "工具年份范围与明确指定的接口过滤条件没有交集"}])
    args = args.model_copy(update={"year_from": lower, "year_to": upper})
    key = json.dumps([" ".join(args.query.casefold().split()), lower, upper], ensure_ascii=False)
    counts = dict(state.get("finding_query_counts") or {})
    counts[key] = counts.get(key, 0) + 1
    state["finding_query_counts"] = counts
    if counts[key] > 2:
        return _error("repeated_query", "同一检索式已调用两次。请换检索式或提交当前结果。")
    cached = (state.get("finding_search_cache") or {}).get(key)
    if cached is not None:
        return {**cached, "reused": True, "notice": [*cached.get("notice", []), "已搜索过，复用上次结果，未再次访问来源。"]}
    if state.get("search_count", 0) >= _search_limit(state) or not _has_api_budget(state):
        return _error("search_budget", "真实搜索次数已用尽，请根据已有候选提交或说明不足。")
    state["search_count"] = state.get("search_count", 0) + 1
    state["extra_search"] = max(0, state["search_count"] - 1)
    state["api_calls"] = state.get("api_calls", 0) + 1
    state["queries"] = [*(state.get("queries") or []), args.query]
    state["workflow_searches"] = list(state["queries"])
    report = await tools.search_candidates(args)
    state["finding_all_failed"] = state.get("finding_all_failed", True) and report["status"] == "failed"
    # Reapply hard filters at the tool boundary even if an upstream adapter
    # returns unfiltered metadata. Unknown years are never eligible with bounds.
    eligible = []
    for row in report["papers"]:
        paper = PaperOut.model_validate(row)
        if search.year_matches(paper, lower, upper):
            eligible.append(row)
        elif paper.year is None:
            report["omitted_unknown_year"] += 1
    pool, fresh, updated, aliases, duplicates = tools.merge_candidates(state["papers"], eligible, state["finding_aliases"])
    state.update(papers=pool, finding_aliases=aliases,
                 source_errors={**(state.get("source_errors") or {}), **report["source_errors"]},
                 source_retry_after={**(state.get("source_retry_after") or {}), **report["source_retry_after"]})
    failures = dict(state.get("finding_source_failures") or {})
    for source in report["sources"]:
        if source["status"] != "completed":
            failures[source["source"]] = source
    state["finding_source_failures"] = failures
    state["finding_notices"] = list(dict.fromkeys([*state["finding_notices"], *report["notice"]]))
    output = {key: value for key, value in report.items() if key not in {"papers", "source_errors", "source_retry_after"}}
    output.update(papers=[tools.paper_view(p) for p in fresh], updated_papers=[tools.paper_view(p) for p in updated],
                  duplicate_count=duplicates, seen_paper_ids=[p["id"] for p in pool],
                  query=args.query, year_from=lower, year_to=upper, reused=False)
    state["finding_search_cache"] = {**state["finding_search_cache"], key: output}
    _event(state, "search", f"取得 {len(fresh)} 篇新候选，已见 {len(pool)} 篇；尚未作为推荐提交", query=args.query,
           errors=report["source_errors"], warnings=report["notice"], sources=report["sources"])
    return output


def _resolve(state, paper_id):
    canonical = (state.get("finding_aliases") or {}).get(paper_id, paper_id)
    return next((p for p in state.get("papers") or [] if p["id"] == canonical), None)


async def _detail(state, args):
    record = _resolve(state, args.paper_id)
    if record is None:
        return _parameter_error(state, "get_paper_detail", [{"field": "paper_id", "message": "只能查看本轮搜索返回过的ID", "available_ids": [p["id"] for p in state["papers"]]}])
    key = record["id"]
    if key in state["finding_details"]:
        return {**state["finding_details"][key], "reused": True}
    if len((record.get("abstract") or "").strip()) >= 400:
        return {"status": "completed", "paper": tools.paper_view(record), "reused": True, "notice": "摘要已在本轮候选中，未再次请求。"}
    if state.get("finding_detail_count", 0) >= MAX_DETAILS or not _has_api_budget(state):
        return _error("detail_budget", "详情调用次数已用尽，请根据已取得的信息判断，信息不足的论文不要强行推荐。")
    state["finding_detail_count"] += 1
    state["api_calls"] = state.get("api_calls", 0) + 1
    report = await tools.paper_detail(record)
    if report["status"] == "completed":
        updated = report["paper"]
        if not search.year_matches(PaperOut.model_validate(updated), state.get("year_from"), state.get("year_to")):
            # Keep the real updated metadata, so submitting the original ID
            # cannot silently restore the previously incorrect year.
            state["papers"] = [updated if p["id"] == key else p for p in state["papers"]]
            report = _error("year_mismatch", "详情年份不符合明确的年份过滤条件，不能推荐该论文。")
            state["finding_details"] = {**state["finding_details"], key: report}
            return report
        state["papers"] = [updated if p["id"] == key else p for p in state["papers"]]
        report = {**report, "paper": tools.paper_view(updated)}
    state["finding_details"] = {**state["finding_details"], key: report}
    _event(state, "detail", "已获取候选详情" if report["status"] == "completed" else "候选详情获取失败，保留原记录", paper_id=key)
    return report


def _submit(state, args):
    selected, reasons, invalid = [], {}, []
    for item in args.items:
        record = _resolve(state, item.paper_id)
        if record is None or not search.year_matches(PaperOut.model_validate(record), state.get("year_from"), state.get("year_to")):
            invalid.append(item.paper_id)
            continue
        if record["id"] not in reasons:
            selected.append(record["id"])
            reasons[record["id"]] = item.reason
    if invalid:
        return _parameter_error(state, "submit_papers", [{"field": "items.paper_id", "message": "存在未返回过或不符合明确年份条件的ID", "invalid_ids": invalid,
                                                          "available_ids": [p["id"] for p in state["papers"]]}])
    state.update(recommended_ids=selected, recommendation_reasons=reasons, finding_submitted=True,
                 answer=args.note, workflow_stage="select")
    _event(state, "select", f"已根据需求提交 {len(selected)} 篇论文", paper_ids=selected)
    return {"status": "completed", "submitted_ids": selected, "done": True}


async def _execute(state, call):
    name = call["function"]["name"]
    if name not in tools.MODELS:
        return _error("unknown_tool", "只允许 search_papers、get_paper_detail、submit_papers。", retryable=True)
    if (state.get("finding_arg_errors") or {}).get(name, 0) > MAX_PARAMETER_RETRIES:
        return _error("argument_retries_exhausted", "该工具的连续参数纠错次数已用尽，请使用其他工具或结束。")
    try:
        raw = call["function"].get("arguments", "{}")
        args = tools.MODELS[name].model_validate(json.loads(raw) if isinstance(raw, str) else raw)
    except (ValueError, TypeError) as exc:
        fields = ([{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors(include_input=False, include_context=False)]
                  if isinstance(exc, ValidationError) else [{"field": "arguments", "message": "必须提供合法JSON对象，不能是说明文字。"}])
        return _parameter_error(state, name, fields)
    try:
        if name == "submit_papers":
            report = _submit(state, args)
        else:
            report = await asyncio.wait_for(_search(state, args) if name == "search_papers" else _detail(state, args), timeout=min(75, _seconds(state)))
    except search.SourceFailure as exc:
        source = "detail" if name == "get_paper_detail" else "search"
        state["finding_source_failures"] = {**state["finding_source_failures"], source: exc.detail(source)}
        report = _error(exc.code, exc.message, http_status=exc.http_status, retry_after_seconds=exc.retry_after)
    except (httpx.HTTPError, TimeoutError) as exc:
        code = "timeout" if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else "network"
        report = _error(code, "工具请求超时，不能把本次失败当成零结果。" if code == "timeout" else "工具网络请求失败，已取得的候选仍保留。")
    except Exception:
        log.exception("finding tool failed: %s", name)
        report = _error("internal", "工具内部执行失败，已取得的候选仍保留；可以换一种方法或说明限制。")
    if report.get("status") == "failed" and report.get("error", {}).get("code") in {"timeout", "network", "internal", "not_found", "identity_mismatch"}:
        # Persist external failures too, so the user sees them even if the
        # model omits them from its final note. They are not argument retries.
        failure = {"source": name, "status": report["error"]["code"], "message": report["error"]["message"],
                   "http_status": report["error"].get("http_status"), "retry_after_seconds": 0, "retryable": False}
        state["finding_source_failures"] = {**state["finding_source_failures"], name: failure}
        if name == "get_paper_detail":
            record = _resolve(state, args.paper_id)
            if record:
                state["finding_details"] = {**state["finding_details"], record["id"]: report}
        elif name == "search_papers":
            query_key = json.dumps([" ".join(args.query.casefold().split()),
                max([v for v in (state.get("year_from"), args.year_from) if v is not None], default=None),
                min([v for v in (state.get("year_to"), args.year_to) if v is not None], default=None)], ensure_ascii=False)
            state["finding_search_cache"] = {**state["finding_search_cache"], query_key: {**report, "papers": [], "notice": []}}
    if report.get("error", {}).get("code") != "invalid_arguments":
        state["finding_arg_errors"] = {**state["finding_arg_errors"], name: 0}
    return report


async def tools_node(state):
    state = dict(state)
    state.pop("messages", None)
    state.pop("past_steps", None)
    messages = list(state.get("finding_messages") or [])
    for index, call in enumerate(state.get("finding_pending") or []):
        reason = _halt(state)
        if state.get("finding_submitted"):
            output = _error("already_submitted", "本轮已提交结束，未执行后续调用。")
        elif reason:
            output = _error("stopped", reason)
        elif index >= 6:
            output = _error("batch_limit", "单轮工具调用过多；请分轮执行。")
        else:
            output = await _execute(state, call)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(output, ensure_ascii=False)})
    state.update(finding_messages=messages, finding_pending=[])
    if state.get("finding_submitted"):
        return finalize(state)
    reason = _halt(state)
    return finalize(state, reason) if reason else state


def tools_route(state):
    return "finish" if state.get("finding_done") or state.get("stop_reason") else "agent"

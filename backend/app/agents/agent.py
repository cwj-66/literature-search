"""LangGraph assistant: deterministic evidence workflow plus explicit operations.

Module boundaries:
  prompts.py  role system prompts + AGENT_PROMPT_VERSION
  state.py    AgentState, intermediate models, budgets, unified evidence registry
  store.py    task snapshots/run claims, pause-cancel flags, call cache, operation ledger
  tooling.py  domain catalog, tool handlers, HITL confirmation, tool_node
  workflow.py discovery, per-paper reading, verified quotes, gap retrieval, synthesis
  agent.py    routing, review, task runtime (background / resume / continue / SSE)

Graph:
  router → clarify / direct / plan or simple research
  research → prepare → optional search → read → quotes → assess
             ↑                           ↑             ├ expand → search
             └ shared state/checkpoints  └ next paper  └ synthesize → claims → judge
  judge → bounded repair → synthesize / finalize → finish
  Explicit workspace operations retain the separate confirmation/tool path.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from app.agents import store, workflow
from app.agents.prompts import (
    AGENT_PROMPT_VERSION, CLAIMS_SYSTEM, DIRECT_SYSTEM, JUDGE_SOURCE_RULES, JUDGE_SYSTEM, REACT_FORMAT_RETRY,
    REACT_SYSTEM, ROUTER_SYSTEM, OPERATION_SYSTEM,
)
from app.agents.state import (
    CRITICAL_SLOTS, DOMAINS, EVIDENCE_ID_RE, MAX_API, MAX_ASSESS, MAX_CLAIM_CHARS, MAX_CLARIFY, MAX_DEEP,
    MAX_EXTRA_SEARCH, MAX_JUDGE_ROUNDS, MAX_LLM, MAX_REACT, MAX_CANDIDATE_PAPERS, SCIENTIFIC_MATERIALS, SLOT_NAMES, AgentState,
    ClaimsOutput, CoverageAssessment, DirectAnswer, JudgeBatch, ReactOutput, TaskRoute, budget_exhausted,
    hard_stop, initial_state, is_cancel_requested, material_of, msg_attr, msg_role, public_state,
    remaining_budget, with_event,
)
from app.agents.store import (
    TaskConflict, cached_call, checkpoint_path, ensure_cache, ensure_flags, ensure_tasks, get_task, list_tasks,
    save_task,
)
from app.agents.tooling import (
    ASSESS_TOOL, PRIVILEGED_TOOLS, TOOL_REGISTRY, _clean_query, _downgrade_action, _is_privileged, _rule_assess,
    _run_business_tool, _tool_analyze_paper, _tool_assess_coverage, _tool_catalog, _tool_request_deep_analysis,
    _tool_retrieve_paper_evidence, _tool_save_papers_to_workspace, _tool_search_papers, assessment_updates,
    catalog_for, domain_prompt, limited, route, run_coverage_assessment, tool_node,
)
from app.infrastructure import llm
from app.services.analysis import attribution, evidence_analysis as ea
from app.services.papers import workspaces
from app.tools.research_scope import ToolContext

log = logging.getLogger(__name__)

_cache_key = store.cache_key
_cache_get = store.cache_get
_cache_put = store.cache_put
_flag_of = store.flag_of
_set_flag = store.set_flag
_clear_flag = store.clear_flag
_msg_attr = msg_attr
_remaining_budget = remaining_budget
_run_coverage_assessment = run_coverage_assessment

ARXIV_REF = re.compile(r"^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$", re.IGNORECASE)
DOI_REF = re.compile(r"^(doi:)?10\.\d{4,9}/\S+$", re.IGNORECASE)
PAPER_REF = re.compile(r"(?:arxiv:)?\d{4}\.\d{4,5}|(?:doi:)?10\.\d{4,9}/", re.IGNORECASE)
PAPER_SPECIFIC = re.compile(
    r"(?:这|那|该)(?:一)?篇|该论文|(?:所选|已选|选中|当前)(?:的)?(?:论文|文献)|工作区|"
    r"(?:arxiv:)?\d{4}\.\d{4,5}|(?:doi:)?10\.\d{4,9}/", re.IGNORECASE)
THIS_PAPER = re.compile(r"(?:这|那|该|本)(?:一)?篇|该论文|这个论文|这份论文|当前(?:的)?论文")
THESE_PAPERS = re.compile(r"这些(?:论文|文献)|(?:所选|已选|选中)(?:的)?(?:论文|文献)")
WORKSPACE_REF = re.compile(r"工作区")
WORKSPACE_OP = re.compile(r"(?:加入|添加|加到|收藏|移出|移除|删除|放入).{0,12}工作区|工作区.{0,8}(?:有哪些|成员|列表|里有)")
WHOLE_WORKSPACE = re.compile(r"整个工作区|工作区(?:内|中|里)?的?(?:全部|所有)")
RECENT_YEARS = re.compile(r"(?:近|最近|过去)\s*([0-9一二两三四五六七八九十]{1,3})\s*年")
VAGUE_RECENT = re.compile(r"(?:近|最近)(?:几|些)年")
COMPARATIVE_HINT = re.compile(r"对比|比较|综述|梳理|差异|各自|横向|区别")
CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
QUANTITY_HINT = re.compile(r"([0-9一二两三四五六七八九十]{1,3})\s*篇(?:论文|文献|文章)?")
EARLIEST_HINT = re.compile(r"最早|最先")
SORT_WORDS = re.compile(r"最早|最先|最新")


# ---------------------------------------------------------------------------
# Router / Clarify（路由、槽位校验与澄清状态机）
# ---------------------------------------------------------------------------


def _router_shortcut(goal: str) -> bool:
    text = (goal or "").strip()
    return bool(ARXIV_REF.match(text) or DOI_REF.match(text))


def _cn_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十"):
        return 10 + CN_DIGITS.get(token[1:], 0)
    if token.endswith("十"):
        return CN_DIGITS.get(token[:-1], 0) * 10 or None
    return CN_DIGITS.get(token)


def _recent_years(goal: str) -> tuple[int, int] | None:
    match = RECENT_YEARS.search(goal or "")
    years = _cn_number(match.group(1)) if match else None
    if not years or years > 30:
        return None
    current = datetime.now().year
    return current - years + 1, current


def _missing_slots(goal: str, state: dict, suggested: list[dict] | None = None) -> list[dict]:
    """必要槽位以当前 State 为准重新校验；能从上下文补全的不追问。"""
    slots = []
    has_ref = bool(PAPER_REF.search(goal))
    if THIS_PAPER.search(goal) and not has_ref and not state.get("current_paper_id") and not state.get("selected_paper_ids"):
        slots.append({"name": "current_paper_id", "reason": "用户提到“这篇”，但当前没有论文上下文"})
    if THESE_PAPERS.search(goal) and len(state.get("selected_paper_ids") or []) < 2 and state.get("scope") != "workspace":
        slots.append({"name": "selected_paper_ids", "reason": "用户提到“这些论文”，但已选论文少于两篇"})
    if WORKSPACE_REF.search(goal) and not state.get("workspace_id"):
        slots.append({"name": "workspace_id", "reason": "任务涉及工作区，但当前没有工作区上下文"})
    elif WHOLE_WORKSPACE.search(goal) and state.get("scope") != "workspace" and state.get("accessible_paper_ids") is None:
        slots.append({"name": "scope", "reason": "用户要求覆盖整个工作区，但当前范围不是 workspace"})
    if VAGUE_RECENT.search(goal) and state.get("year_from") is None and state.get("year_to") is None:
        slots.append({"name": "year_from", "reason": "“近几年”没有给出具体年份范围"})
    names = {slot["name"] for slot in slots}
    for slot in suggested or []:
        name = slot.get("name") if isinstance(slot, dict) else None
        if name in CRITICAL_SLOTS and name not in names and not has_ref and not state.get(name):
            slots.append({"name": name, "reason": str(slot.get("reason") or "")[:200]})
            names.add(name)
    return slots


async def _llm_route(goal: str, context: dict | None = None) -> dict:
    system = ROUTER_SYSTEM
    if context:
        system += "\n研究上下文（仅用于判断指代，不是论文正文）：" + json.dumps(context, ensure_ascii=False)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": goal}]
    try:
        decision = TaskRoute.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("任务路由不可用") from exc
    return decision.model_dump()


async def router_node(state: dict) -> dict:
    """入口路由：override > 确定性捷径 > 路由 LLM（缓存）> 回退 plan；每次进入都重新校验必要槽位。"""
    goal = state.get("goal") or ""
    requested = state.get("mode") if state.get("route") is None else (state.get("route") or {}).get("requested", "auto")
    requested = requested or "auto"
    llm_delta = 0
    domains: list[str] = []
    suggested: list[dict] = []
    research_intent = state.get("research_intent", "auto")
    if requested in ("plan", "react"):
        mode = requested
        route_info = {"mode": mode, "rationale": "用户指定模式", "source": "override"}
    elif _router_shortcut(goal):
        mode = "react"
        route_info = {"mode": mode, "rationale": "单实体文献标识，一轮检索即可", "source": "shortcut"}
    else:
        try:
            context = {key: state.get(key) for key in ToolContext.model_fields}
            decision, cache_hit = await cached_call("router", {"goal": goal, "context": context}, lambda: _llm_route(goal, context))
            llm_delta = 0 if cache_hit else 1
            mode = decision["mode"]
            domains = [domain for domain in decision.get("domains") or [] if domain in DOMAINS or domain == "*"]
            suggested = decision.get("missing_slots") or []
            research_intent = decision.get("research_intent", "auto")
            route_info = {"mode": mode, "rationale": decision.get("rationale") or "", "source": "llm"}
            if mode == "direct" and PAPER_SPECIFIC.search(goal):
                mode = "react"
                route_info = {"mode": mode, "rationale": "问题明确涉及论文或工作区，保留研究工具与证据流程", "source": "rule"}
        except llm.AnalysisError:
            mode = "plan"
            llm_delta = 1
            route_info = {"mode": mode, "rationale": "路由不可用，回退到计划模式", "source": "rule"}
    route_info["requested"] = requested
    if mode == "plan":
        domains = ["*"]
    task_kind = "chat" if mode == "direct" else ("operation" if WORKSPACE_OP.search(goal) or domains == ["knowledge"] else "research")
    updates = {
        "mode": mode,
        "route": route_info,
        "task_kind": task_kind,
        "domains": domains,
        "research_intent": research_intent,
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
    }
    years = _recent_years(goal) if state.get("year_from") is None and state.get("year_to") is None else None
    if years and mode != "direct":
        updates["year_from"], updates["year_to"] = years
    slots = [] if mode == "direct" else _missing_slots(goal, {**state, **updates}, suggested)
    event = {"step": "route", "mode": mode, "reason": route_info["rationale"], "source": route_info["source"]}
    count = state.get("clarify_count", 0)
    if slots and count >= MAX_CLARIFY:
        gaps = list(state.get("gaps") or []) + [f"未澄清:{slot['name']}" for slot in slots]
        critical = [slot for slot in slots if slot["name"] in CRITICAL_SLOTS]
        updates["gaps"] = gaps
        if critical:
            names = "、".join(slot["reason"] for slot in critical)
            updates.update(stop_reason="缺少必要信息",
                           answer=f"无法继续：{names}。请先选中论文或指定工作区后重新提问，系统不会猜测要操作的对象。")
        event["unresolved"] = [slot["name"] for slot in slots]
        slots = []
    updates["missing_slots"] = slots
    updates["clarify_resolved"] = not slots and count > 0
    if slots:
        event["missing_slots"] = [slot["name"] for slot in slots]
    updates["events"] = with_event(state, event)
    updates["past_steps"] = [["route", mode]]
    return updates


def route_route(state: dict) -> Literal["direct", "plan", "react", "clarify", "finish"]:
    if state.get("stop_reason"):
        return "finish"
    if state.get("missing_slots"):
        return "clarify"
    mode = state.get("mode")
    return mode if mode in ("direct", "plan") else "react"


def _clarify_hint(slots: list[dict]) -> str:
    return "请补充：" + "；".join(slot.get("reason") or slot.get("name", "") for slot in slots) + "。也可以取消本次任务。"


def _merge_clarification(state: dict, decision: dict) -> dict:
    """只合并 ToolContext 字段与年份；非法值忽略，工作区必须已注册。"""
    context = {key: state.get(key) for key in ToolContext.model_fields}
    for key in ToolContext.model_fields:
        if key == "accessible_paper_ids" and decision.get(key) is not None:
            context[key] = decision[key]
            continue
        if decision.get(key) not in (None, "", []):
            context[key] = decision[key]
    if decision.get("approved_ids") and not decision.get("selected_paper_ids"):
        context["selected_paper_ids"] = [str(ref) for ref in decision["approved_ids"]]
    try:
        merged = ToolContext.model_validate({key: value for key, value in context.items() if value is not None}).model_dump()
    except ValidationError:
        merged = {key: state.get(key) for key in ToolContext.model_fields}
    if merged.get("workspace_id") and merged["workspace_id"] != state.get("workspace_id"):
        try:
            workspaces.require(merged["workspace_id"])
        except Exception:
            merged["workspace_id"] = state.get("workspace_id")
    years = {key: decision.get(key) for key in ("year_from", "year_to")
             if isinstance(decision.get(key), int) and 1900 <= decision[key] <= 2100}
    if years.get("year_from") and years.get("year_to") and years["year_from"] > years["year_to"]:
        years = {}
    return {**merged, **years}


async def clarify_node(state: dict) -> dict:
    """追问必要槽位；恢复后只合并允许字段并回到 router 重新校验，abort 直达结束。"""
    from langgraph.types import interrupt
    slots = state.get("missing_slots") or []
    count = state.get("clarify_count", 0) + 1
    decision = interrupt({
        "type": "clarify",
        "goal": state.get("goal"),
        "slots": slots,
        "hint": _clarify_hint(slots),
        "suggestions": [paper.get("id") for paper in (state.get("papers") or [])[:5]],
        "allowed_fields": list(SLOT_NAMES),
        "attempt": count,
        "max_attempts": MAX_CLARIFY,
    })
    decision = decision if isinstance(decision, dict) else {}
    if decision.get("abort") or decision.get("approved") is False:
        return {
            "cancelled": True, "stop_reason": "用户取消", "missing_slots": [], "clarify_count": count, "interrupt": None,
            "events": with_event(state, {"step": "clarify", "attempt": count, "result": "abort"}),
            "past_steps": [["clarify", "abort"]],
        }
    merged = await asyncio.to_thread(_merge_clarification, state, decision)
    changed = sorted(key for key, value in merged.items() if value != state.get(key))
    return {
        **merged, "missing_slots": [], "clarify_count": count, "interrupt": None,
        "events": with_event(state, {"step": "clarify", "attempt": count, "result": "merged", "fields": changed}),
        "past_steps": [["clarify", count]],
    }


def clarify_route(state: dict) -> Literal["router", "finish"]:
    return "finish" if state.get("stop_reason") else "router"


# ---------------------------------------------------------------------------
# Direct / React 准备 / Plan（计划审批 HITL）
# ---------------------------------------------------------------------------


def _result_hint(goal: str) -> tuple[int | None, str | None, str]:
    """解析数量词与“最早/最新”排序词：返回 (数量上限, "asc" 或 None, 去掉数量/排序短语后的检索式)。"""
    text = goal or ""
    match = QUANTITY_HINT.search(text)
    count = _cn_number(match.group(1)) if match else None
    if count is None or not 1 <= count <= MAX_CANDIDATE_PAPERS:
        count = None
    order = "asc" if EARLIEST_HINT.search(text) else None
    query = SORT_WORDS.sub("", QUANTITY_HINT.sub("", text)).strip() if (match or order or SORT_WORDS.search(text)) else text
    if not query:
        query = text
    return count, order, query


def react_node(state: dict) -> dict:
    goal = state.get("goal") or ""
    count, order, query = _result_hint(goal)
    updates = {
        "queries": [query],
        "plan": [{"action": "search", "query": query}],
        "plan_version": 1,
        "events": with_event(state, {"step": "react", "reason": "简单任务跳过计划，直接进入检索执行"}),
        "past_steps": [["react", query]],
    }
    if count is not None:
        updates["result_limit"] = count
    if order:
        updates["sort_order"] = order
    return updates


async def direct_answer_node(state: dict) -> dict:
    """普通聊天绕过研究计划、工具、主张提取与论文证据 Judge。"""
    halt = budget_exhausted(state)
    if halt:
        return {"stop_reason": halt}
    messages = [{"role": "system", "content": DIRECT_SYSTEM}]
    if state.get("conversation_history"):
        messages.append({"role": "user", "content": "此前对话（仅作背景）：\n" + state["conversation_history"]})
    messages.append({"role": "user", "content": state.get("goal") or ""})
    try:
        output = DirectAnswer.model_validate(ea.parse_json(await llm.complete(messages)))
        answer = output.answer.strip()
        if not answer:
            raise ValueError("普通聊天回复为空")
    except (llm.AnalysisError, ValueError, TypeError) as exc:
        return {
            "answer": "暂时无法生成回复，请稍后重试。", "stop_reason": "普通聊天回复失败",
            "llm_calls": state.get("llm_calls", 0) + 1,
            "events": with_event(state, {"step": "direct", "status": "failed", "reason": "普通聊天生成失败"}),
            "past_steps": [["direct", "failed"]],
        }
    return {
        "answer": answer, "stop_reason": "普通聊天完成",
        "llm_calls": state.get("llm_calls", 0) + 1,
        "messages": [{"role": "user", "content": state.get("goal") or ""}, {"role": "assistant", "content": answer}],
        "events": with_event(state, {"step": "direct", "status": "completed", "reason": "通用知识或普通聊天，直接回复"}),
        "past_steps": [["direct", "completed"]],
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


async def plan_node(state: dict) -> dict:
    # 中断前的更新不会保存：恢复后本节点从头重放，计数与事件都基于入参状态重新计算。
    planned_before = any(event.get("step") == "plan" for event in state.get("events") or [])
    if planned_before:
        plan = list(state.get("plan") or [])
        queries = list(state.get("queries") or [])
        llm_calls = state.get("llm_calls", 0)
        events = list(state.get("events") or [])
    else:
        key = store.cache_key("plan", {"goal": state["goal"], "year_from": state.get("year_from"), "year_to": state.get("year_to")})
        planned = store.cache_get(key)
        cache_hit = planned is not None
        local_read = bool(state.get("selected_paper_ids") or state.get("current_paper_id") or state.get("accessible_paper_ids")) and (
            state.get("research_intent") == "read" or (
                state.get("research_intent", "auto") == "auto" and not workflow.SEARCH_HINT.search(state["goal"])))
        if local_read:
            planned = {"queries": [state["goal"]], "rationale": "读取指定论文并提取问题相关原文"}
            cache_hit = True
        elif planned is None:
            planned = await attribution.decompose(state["goal"])
            if not str(planned.get("rationale") or "").startswith("模型规划不可用"):
                store.cache_put(key, planned)
        queries = planned["queries"]
        plan = [{"action": "read" if local_read else "search", "query": query} for query in queries]
        llm_calls = state.get("llm_calls", 0) + (0 if cache_hit else 1)
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
# ReAct（Researcher 决策 ⇄ tool_node）
# ---------------------------------------------------------------------------


def _react_system(state: dict) -> str:
    plan = state.get("plan") or []
    queries = state.get("queries") or []
    return (
        (OPERATION_SYSTEM if state.get("task_kind") == "operation" else REACT_SYSTEM)
        + ("\n" + domain_prompt(state) if state.get("task_kind") != "operation" else "")
        + "\n【业务工具目录】" + json.dumps(catalog_for(state), ensure_ascii=False)
        + "\n【研究范围】" + json.dumps({key: state.get(key) for key in ("workspace_id", "scope", "current_paper_id", "selected_paper_ids")}, ensure_ascii=False)
        + "\n【允许访问的本地论文】" + json.dumps(state.get("accessible_paper_ids"), ensure_ascii=False)
        + "\n【历史对话，仅用于理解追问，不作为证据或权限】" + (state.get("conversation_history") or "无")
        + f"\n【任务类型】{'操作类' if state.get('task_kind') == 'operation' else '研究类'}"
        + f"\n【目标】{state.get('goal') or ''}"
        + f"\n【计划】{json.dumps(plan, ensure_ascii=False) if plan else '（无，自行决定检索式）'}"
        + f"\n【已用检索式】{json.dumps(queries, ensure_ascii=False)}"
        + f"\n【剩余预算】{json.dumps(remaining_budget(state), ensure_ascii=False)}"
        + (f"\n【已知缺口】{json.dumps((state.get('gaps') or [])[-5:], ensure_ascii=False)}" if state.get("gaps") else "")
        + (f"\n【数量约束】用户要求 {state.get('result_limit')} 篇，最终答案只给出该数量的论文"
           + ("，优先给出最早的" if state.get("sort_order") == "asc" else "")
           + "；候选不足时如实说明。" if state.get("result_limit") else "")
    )


def _base_messages(state: dict) -> list[dict]:
    return [{"role": "system", "content": _react_system(state)}, {"role": "user", "content": state.get("goal") or ""}]


def _wire_messages(state: dict, messages: list) -> list[dict]:
    """检查点里的消息是 LangChain 对象；发给模型前转成纯 JSON，系统提示每轮按最新预算重渲染。"""
    wire = [{"role": "system", "content": _react_system(state)}]
    for message in messages[1:]:
        role = msg_role(message)
        content = msg_attr(message, "content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        if role == "tool":
            wire.append({"role": "user", "content": f"【工具结果 {msg_attr(message, 'name') or ''}】{content}"})
        else:
            wire.append({"role": "assistant" if role == "assistant" else "user", "content": content})
    return wire


async def _react_decide(state: dict, prompt: list[dict]) -> tuple[ReactOutput | None, int]:
    """调用 Researcher；格式错误重试一次。返回 (输出或 None, 实际模型调用次数)。"""
    attempts = 0
    for attempt in range(2):
        attempts += 1
        try:
            raw = await llm.complete(prompt)
        except llm.AnalysisError:
            return None, attempts
        try:
            return ReactOutput.model_validate(ea.parse_json(raw)), attempts
        except (ValueError, TypeError, ValidationError):
            if attempt == 1 or hard_stop({**state, "llm_calls": state.get("llm_calls", 0) + attempts}):
                return None, attempts
            prompt = [*prompt, {"role": "assistant", "content": str(raw)[:2000]}, {"role": "user", "content": REACT_FORMAT_RETRY}]
    return None, attempts


async def react_agent(state: dict) -> dict:
    halt = state.get("stop_reason") or hard_stop(state)
    if halt:
        return {"stop_reason": halt, "events": with_event(state, {"step": "react_stop", "reason": halt})}
    turns = state.get("react_turns", 0)
    if turns + 1 > state.get("max_react", MAX_REACT):
        return {"stop_reason": "达到最大迭代次数", "events": with_event(state, {"step": "react_stop", "reason": "达到最大迭代次数"})}
    messages = list(state.get("messages") or [])
    fresh = not (messages and msg_role(messages[0]) == "system")
    if fresh:
        messages = _base_messages(state)
    output, attempts = await _react_decide(state, messages if fresh else _wire_messages(state, messages))
    llm_calls = state.get("llm_calls", 0) + attempts
    if output is None:
        thought = "模型不可用，保底检索后直接作答"
        answer = "生成模型不可用，已保留检索与已有证据。"
        updates, tool_events = {}, []
        if remaining_budget(state)["api_calls_left"] > 0:
            queries = state.get("queries") or [state.get("goal") or ""]
            query = queries[min(state.get("query_index", 0), len(queries) - 1)]
            _, updates = await _tool_search_papers(state, {"query": query or state.get("goal") or ""})
            tool_events = updates.pop("events_extra", [])
            updates.pop("past_extra", None)
        assistant = {"role": "assistant", "content": json.dumps({"thought": thought, "answer": answer}, ensure_ascii=False)}
        events = with_event({"events": with_event(state, tool_events)}, {"step": "react", "turn": turns + 1, "action": "answer", "thought": thought, "fallback": True})
        return {
            **updates,
            "messages": (messages if fresh else []) + [assistant],
            "react_turns": turns + 1,
            "llm_calls": llm_calls,
            "answer": answer,
            "events": events,
            "past_steps": [["react", turns + 1]],
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
        "llm_calls": llm_calls,
        "events": with_event(state, {"step": "react", "turn": turns + 1, "action": "tools" if has_calls else "answer", "thought": (output.thought or "")[:200]}),
        "past_steps": [["react", turns + 1]],
    }
    if not has_calls:
        updates["answer"] = (output.answer or "").strip() or "（未能生成有效答复）"
        if state.get("mode") == "plan" and state.get("task_kind") != "operation" and not state.get("assessments"):
            # 调研任务由编排保证作答前至少评估一次覆盖度；缺口交给 Judge 的完成度检查处理。
            assess_state = {**state, "llm_calls": llm_calls}
            assessment, delta, fallback = await run_coverage_assessment(assess_state)
            extra = assessment_updates(assess_state, assessment, delta, fallback)
            updates["events"] = updates["events"] + extra.pop("events_extra")
            updates["past_steps"] = updates["past_steps"] + extra.pop("past_extra")
            updates.update(extra)
    return updates


def react_route(state: dict) -> Literal["tools", "done"]:
    if state.get("stop_reason"):
        return "done"
    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    if last is not None and msg_attr(last, "tool_calls"):
        return "tools"
    return "done"


# ---------------------------------------------------------------------------
# Judge（引用有效性 / 证据充分性 / 任务完成度 / 多篇覆盖）与终态规则
# ---------------------------------------------------------------------------

FAILING_STATUSES = {"pending", "rule_failed", "contradicted"}


def _review_skeleton() -> dict:
    return {"rounds": 0, "results": [], "to_verify": [], "semantics_done": False, "fixed_claims": [], "downgraded_claims": [],
            "coverage_note": None, "multi_paper_note": None, "final_status": None}


def _pool_excerpt(state: dict, evidence_ids: list, limit: int = 900) -> list[dict]:
    pool = state.get("evidence_pool") or {}
    return [{"evidence_id": eid, "quote": str(pool.get(eid) or "")[:limit], "material": material_of(state, eid)}
            for eid in evidence_ids if eid in pool]


def _claim_evidence_table(state: dict) -> list[dict]:
    return [{"evidence_id": eid, "quote": str(quote)[:900], "material": material_of(state, eid)}
            for eid, quote in list((state.get("evidence_pool") or {}).items())[:20]]


async def _llm_extract_claims(state: dict) -> list[dict]:
    material = {"goal": state.get("goal"), "answer": state.get("answer") or "", "evidence": _claim_evidence_table(state)}
    messages = [{"role": "system", "content": CLAIMS_SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    try:
        parsed = ClaimsOutput.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("主张提取不可用") from exc
    return [item.model_dump() for item in parsed.claims]


async def claims_node(state: dict) -> dict:
    if state.get("task_kind") == "operation":
        return {
            "claims": [],
            "review": {**_review_skeleton(), "skipped": "operation"},
            "events": with_event(state, {"step": "claims", "count": 0, "reason": "操作类任务只报告工具结果，不做科研主张核验"}),
            "past_steps": [["claims", 0]],
        }
    try:
        drafts, cache_hit = await cached_call(
            "claims",
            {"goal": state.get("goal"), "answer": state.get("answer") or "", "evidence": _claim_evidence_table(state)},
            lambda: _llm_extract_claims(state),
        )
        llm_delta = 0 if cache_hit else 1
        reason = "提取可复核主张"
    except llm.AnalysisError:
        drafts = [{"text": state.get("answer") or "", "evidence_ids": list((state.get("evidence_pool") or {}).keys())}]
        llm_delta = 1
        reason = "主张提取不可用，整段作为单条主张"
    claims = []
    for index, draft in enumerate(drafts, start=1):
        text = str(draft.get("text") or "").strip()
        if not text:
            continue
        claims.append({"claim_id": f"c{index}", "text": text[:MAX_CLAIM_CHARS],
                       "evidence_ids": [str(eid) for eid in draft.get("evidence_ids") or []], "status": "pending"})
    if not claims and (state.get("answer") or "").strip():
        claims = [{"claim_id": "c1", "text": state["answer"].strip()[:MAX_CLAIM_CHARS], "evidence_ids": [], "status": "pending"}]
    return {
        "claims": claims,
        "review": dict(state.get("review") or _review_skeleton()),
        "llm_calls": state.get("llm_calls", 0) + llm_delta,
        "events": with_event(state, {"step": "claims", "count": len(claims), "reason": reason}),
        "past_steps": [["claims", len(claims)]],
    }


def _rule_check(claim: dict, pool: dict) -> str | None:
    """程序化预检（不调用 LLM）：证据 ID 格式、越界、数字出处。"""
    evidence_ids = claim.get("evidence_ids") or []
    if not evidence_ids:
        return "无证据出处"
    if any(not EVIDENCE_ID_RE.match(str(eid)) for eid in evidence_ids):
        return "证据ID无效"
    if any(eid not in pool for eid in evidence_ids):
        return "证据越界"
    quotes = [str(pool.get(eid) or "") for eid in evidence_ids]
    claim_text = re.sub(r"\[E\d{4}\]", "", claim.get("text") or "")
    for match in llm.NUMBER.finditer(claim_text):
        token = match.group(0)
        if llm.significant(token):
            needle = token[:-1] if token.endswith("%") else token
            if not any(needle.replace(",", "") in quote.replace(",", "") for quote in quotes):
                return "数字无出处"
    return None


def _coverage_note(state: dict) -> str | None:
    cov = state.get("coverage") or {}
    if not cov or cov.get("next_action") in (None, "synthesize") or cov.get("coverage") == "sufficient":
        return None
    missing = cov.get("missing") or []
    if not missing and cov.get("coverage") not in ("insufficient", "partial"):
        return None
    return f"任务完成度：覆盖度{cov.get('coverage')}，尚缺 {'、'.join(missing) if missing else '关键方面'}"


def _multi_paper_note(state: dict, claims: list[dict]) -> str | None:
    """对比/综述类目标要求科研证据（original/abstract）覆盖至少两篇论文；单篇问题豁免。"""
    goal = state.get("goal") or ""
    if state.get("research_intent") == "discover" or state.get("result_limit") == 1:
        return None
    if not (COMPARATIVE_HINT.search(goal) or len(state.get("plan") or []) >= 2):
        return None
    attribution_map = state.get("evidence_papers") or {}
    cited = {attribution_map[eid] for claim in claims for eid in claim.get("evidence_ids") or []
             if attribution_map.get(eid) and material_of(state, eid) in SCIENTIFIC_MATERIALS}
    if len(cited) >= 2:
        return None
    return f"多篇覆盖：对比/综述类目标要求主张引用至少两篇论文的证据，当前仅 {len(cited)} 篇"


async def _llm_judge(state: dict, pending: list[dict]) -> dict:
    material = {
        "source_rules": JUDGE_SOURCE_RULES,
        "goal": state.get("goal"),
        "claims": [{"claim_id": claim["claim_id"], "text": claim["text"], "evidence": _pool_excerpt(state, claim.get("evidence_ids") or [])}
                   for claim in pending],
    }
    messages = [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    try:
        parsed = JudgeBatch.model_validate(ea.parse_json(await llm.complete(messages)))
    except (llm.AnalysisError, ValueError, TypeError, ValidationError) as exc:
        raise llm.AnalysisError("语义复核不可用") from exc
    return {item.claim_id: item.model_dump() for item in parsed.reviews}


async def judge_node(state: dict) -> dict:
    claims = [dict(claim) for claim in state.get("claims") or []]
    pending = [claim for claim in claims if claim.get("status") == "pending"]
    review = dict(state.get("review") or _review_skeleton())
    skipped = review.get("skipped")
    new_round = (review.get("rounds") or 0) + 1
    pool = state.get("evidence_pool") or {}
    results = list(review.get("results") or [])
    still_pending = []
    for claim in pending:
        issue = _rule_check(claim, pool)
        results.append({"claim_id": claim["claim_id"], "verdict": "rule_failed" if issue else "pass", "issue": issue,
                        "action": "fix" if issue else "keep", "round": new_round})
        if issue:
            claim["status"] = "rule_failed"
        else:
            still_pending.append(claim)
    summary, llm_delta, action = {}, 0, "无待评审主张"
    if still_pending and remaining_budget(state)["llm_calls_left"] <= 0:
        for claim in still_pending:
            claim["status"] = "unverified"
        action = "模型额度用尽，待评审主张标记为未验证"
        still_pending = []
        results.append({"claim_id": "*", "verdict": "unverified", "issue": "模型调用次数用尽", "action": "flag", "round": new_round})
    if still_pending:
        judge_payload = {
            "goal": state.get("goal"),
            "claims": [{"claim_id": claim["claim_id"], "text": claim["text"], "evidence": _pool_excerpt(state, claim.get("evidence_ids") or [])}
                       for claim in still_pending],
        }
        try:
            verdicts, cache_hit = await cached_call("judge", judge_payload, lambda: _llm_judge(state, still_pending))
            llm_delta = 0 if cache_hit else 1
            review["semantics_done"] = True
            action = "语义复核"
        except llm.AnalysisError:
            verdicts, llm_delta = {}, 1
            action = "语义复核不可用，相关主张标记为未验证"
        by_id = {claim["claim_id"]: claim for claim in claims}
        for claim_id, verdict in verdicts.items():
            claim = by_id.get(claim_id)
            if claim is None or claim.get("status") != "pending":
                continue
            decision = verdict.get("action") or "keep"
            outcome = verdict.get("verdict") or "unknown"
            summary[claim_id] = outcome
            claim["verdict"] = outcome
            results.append({"claim_id": claim_id, "verdict": outcome, "issue": verdict.get("issue"), "action": decision, "round": new_round})
            if outcome == "contradiction":
                claim["status"] = "contradicted"
            elif decision == "keep" and outcome == "supported":
                claim["status"] = "supported"
            elif decision in ("keep", "flag"):
                claim["status"] = "flagged"
        for claim in still_pending:
            if claim["status"] == "pending" and claim["claim_id"] not in verdicts:
                claim["status"] = "unverified"
    coverage_note = None if skipped else _coverage_note(state)
    multi_note = None if skipped else _multi_paper_note(state, claims)
    review["coverage_note"] = coverage_note
    review["multi_paper_note"] = multi_note
    if not (pending or coverage_note or multi_note):
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
    if state.get("cancelled") or hard_stop(state):
        return "finalize"
    review = state.get("review") or {}
    rounds = review.get("rounds") or state.get("judge_round") or 0
    failing = any(claim.get("status") in FAILING_STATUSES for claim in state.get("claims") or [])
    failing = failing or bool(review.get("coverage_note")) or bool(review.get("multi_paper_note"))
    if failing and rounds < state.get("max_judge_rounds", MAX_JUDGE_ROUNDS):
        return "revise"
    return "finalize"


def _latest_directives(review: dict) -> dict:
    directives = {}
    for result in review.get("results") or []:
        if result.get("action") in ("revise", "drop", "fix"):
            directives[result["claim_id"]] = result
    return directives


async def revise_node(state: dict) -> dict:
    """规则问题确定性处理；矛盾与证据不足通过反馈消息回 ReAct 循环补证或纠正。"""
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
        text = claim.get("text", "")[:80]
        if claim.get("status") == "rule_failed":
            issue = directive.get("issue")
            feedback.append(f"主张 {claim_id}（{issue}）：{text}")
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
        elif claim.get("status") == "contradicted":
            feedback.append(f"主张 {claim_id}（与证据矛盾）：{text}——必须删除该结论，或依据证据原文纠正后重新标注证据ID")
        elif claim.get("status") == "pending" and directive.get("action") in ("revise", "drop"):
            feedback.append(f"主张 {claim_id}（{directive.get('issue') or directive.get('action')}）：{text}")
    if review.get("coverage_note"):
        feedback.append(review["coverage_note"] + "（预算不足时可带着缺口作答，并在末尾用“尚缺：”列出）")
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


def _replace_claim(answer: str, text: str, replacement: str) -> tuple[str, bool]:
    for needle in (text, text.rstrip("。.")):
        if needle and needle in answer:
            return answer.replace(needle, replacement), True
    return answer, False


def finalize_answer_node(state: dict) -> dict:
    """终态：矛盾/删除的主张移出答案，证据不足保守改写，未验证明确标注；答案与复核状态一致。"""
    answer = state.get("answer") or ""
    claims = [dict(claim) for claim in state.get("claims") or []]
    review = dict(state.get("review") or _review_skeleton())
    skipped = review.get("skipped")
    to_verify = list(review.get("to_verify") or [])
    downgraded = list(review.get("downgraded_claims") or [])
    unlocated = []

    def mark(claim_id, text, verify=True):
        if claim_id not in downgraded:
            downgraded.append(claim_id)
        if verify and text not in to_verify:
            to_verify.append(text)

    for claim in claims:
        text = (claim.get("text") or "").strip()
        if not text:
            continue
        status = claim.get("status")
        invalid_citation = status == "rule_failed" and any(
            not EVIDENCE_ID_RE.match(str(eid)) or eid not in (state.get("evidence_pool") or {})
            for eid in claim.get("evidence_ids") or [])
        if status in ("dropped", "contradicted") or invalid_citation:
            label = "与证据矛盾" if status == "contradicted" else "证据不足"
            answer, found = _replace_claim(answer, text, f"（该结论{label}，已移除）。")
            if not found:
                unlocated.append(f"{claim['claim_id']}「{text[:60]}」{label}，应视为已移除")
            mark(claim["claim_id"], text, verify=False)
        elif status in ("downgraded", "pending", "rule_failed"):
            answer, found = _replace_claim(answer, text, text.rstrip("。") + "（该结论证据不足，待核实）。")
            if not found:
                unlocated.append(f"{claim['claim_id']}「{text[:60]}」证据不足，待核实")
            mark(claim["claim_id"], text)
        elif status in ("flagged", "unverified", "fixed"):
            if text not in to_verify:
                to_verify.append(text)
    notes = []
    if to_verify:
        notes.append("部分结论证据不足，已按保守表述处理或标记待核实。")
    if not skipped and claims and not review.get("semantics_done"):
        notes.append("语义复核未完成，以上结论未经验证，请谨慎参考。")
    if review.get("coverage_note"):
        notes.append(f"尚缺：{review['coverage_note']}。")
    if review.get("multi_paper_note"):
        notes.append(f"{review['multi_paper_note']}。")
    if unlocated:
        notes.append("以下主张未能在答案中逐字定位，按复核结论处理：" + "；".join(unlocated) + "。")
    if notes:
        answer = answer.rstrip() + "\n\n【复核说明】" + "".join(notes)
    statuses = [claim.get("status") for claim in claims]
    if skipped:
        final_status = "skipped"
    elif not review.get("semantics_done"):
        final_status = "unverified"
    elif statuses and all(status == "supported" for status in statuses) and not notes:
        final_status = "verified"
    else:
        final_status = "partially_verified"
    review.update(to_verify=to_verify, downgraded_claims=downgraded, final_status=final_status,
                  rounds=state.get("judge_round", review.get("rounds", 0)))
    exhausted = budget_exhausted(state)
    return {
        "answer": answer,
        "claims": claims,
        "review": review,
        "stop_reason": state.get("stop_reason") or (f"{exhausted}，基于已有证据结束" if exhausted else "目标覆盖完成"),
        "events": with_event(state, {"step": "finalize", "to_verify": len(to_verify), "downgraded": len(downgraded), "final_status": final_status, "reason": "复核处置落地"}),
        "past_steps": [["finalize", len(to_verify)]],
    }


# ---------------------------------------------------------------------------
# Gate / Finish
# ---------------------------------------------------------------------------


def gate_route(state: dict) -> Literal["finish", "continue"]:
    if state.get("cancelled") or state.get("stop_reason") or hard_stop(state):
        return "finish"
    return "continue"


def gate_node(state: dict) -> dict:
    """取消则转入 finish；暂停则以 paused 中断挂起，恢复后清除标记继续。"""
    task_id = state.get("task_id")
    if not task_id:
        return {}
    flag = store.flag_of(task_id)
    if flag == "cancel":
        return {"cancelled": True, "stop_reason": "用户取消"}
    if flag == "pause":
        from langgraph.types import interrupt
        interrupt({"type": "paused", "goal": state.get("goal")})
        store.clear_flag(task_id)
    return {}


def _partial_answer(state: dict, reason: str) -> str | None:
    """预算耗尽或中途停止时，输出已收集的材料与缺口（确定性生成，不调用模型）。"""
    papers = state.get("papers") or []
    items = [item for item in (state.get("evidence_items") or {}).values() if item.get("material") in SCIENTIFIC_MATERIALS]
    gaps = state.get("gaps") or []
    pending = state.get("pending_processing") or []
    if not (papers or items or gaps or pending):
        return None
    lines = [f"任务在形成经复核的结论前停止（{reason}）。以下是已收集的材料与缺口，未经主张复核："]
    if papers:
        lines.append("候选论文：" + "；".join(f"{paper.get('title')}（{paper.get('id')}）" for paper in papers[:8]))
    for item in items[:5]:
        lines.append(f"[{item['evidence_id']}] {item.get('paper_id')}：{str(item.get('quote') or '')[:120]}")
    if gaps:
        lines.append("尚缺：" + "、".join(gaps[-5:]))
    if pending:
        lines.append(f"{len(pending)} 篇论文仍在后台处理，完成后可通过 continue 接口继续本任务。")
    return "\n".join(lines)


def finish_node(state: dict) -> dict:
    task_id = state.get("task_id")
    reason = state.get("stop_reason") or budget_exhausted(state) or "没有可继续的动作"
    if task_id and (state.get("cancelled") or is_cancel_requested(task_id)):
        store.clear_flag(task_id)
    if limited(state):
        reason = "来源限流，返回部分结果" if state.get("papers") else "来源限流，未取得候选论文"
    updates = {"stop_reason": reason, "events": with_event(state, {"step": "stop", "reason": reason})}
    answer = state.get("answer")
    finalized = any(event.get("step") == "finalize" for event in state.get("events") or [])
    if state.get("mode") != "direct" and (state.get("react_turns") or state.get("workflow_stage")):
        if not answer:
            partial = _partial_answer(state, reason)
            if partial:
                updates["answer"] = partial
        elif not finalized and state.get("task_kind") != "operation" and "【复核说明】" not in answer:
            updates["answer"] = answer.rstrip() + f"\n\n【复核说明】任务在主张复核前停止（{reason}），以上结论未经核验。"
    return updates


def _tracing_enabled() -> bool:
    return os.getenv("LANGCHAIN_TRACING_V2", "").strip().lower() in ("1", "true") \
        and bool(os.getenv("LANGCHAIN_API_KEY", "").strip())


def build(checkpointer=None):
    from langgraph.graph import END, START, StateGraph

    if _tracing_enabled():
        try:
            from langsmith import Client
            Client()
            log.info("LangSmith tracing enabled: project=%s", os.getenv("LANGCHAIN_PROJECT", "default"))
        except Exception as exc:
            log.warning("LangSmith tracing unavailable, running without tracing: %s", type(exc).__name__)

    graph = StateGraph(AgentState)
    def guarded(node):
        async def run_node(state):
            updates = gate_node(state)
            if updates.get("stop_reason"):
                return updates
            access = await asyncio.to_thread(workflow.check_access, state)
            if access:
                return access
            result = node({**state, **updates})
            return await result if asyncio.iscoroutine(result) else result
        return run_node

    graph.add_node("router", router_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("gate_direct", gate_node)
    graph.add_node("direct_answer", direct_answer_node)
    graph.add_node("react", react_node)
    graph.add_node("plan", plan_node)
    graph.add_node("gate", gate_node)
    graph.add_node("prepare_research", guarded(workflow.prepare_node))
    graph.add_node("search_literature", guarded(workflow.search_node))
    graph.add_node("read_paper", guarded(workflow.read_node))
    graph.add_node("extract_quotes", guarded(workflow.quote_node))
    graph.add_node("assess_evidence", guarded(workflow.assess_node))
    graph.add_node("select_next_papers", guarded(workflow.select_next_node))
    graph.add_node("expand_research", guarded(workflow.expand_node))
    graph.add_node("synthesize_evidence", guarded(workflow.synthesize_node))
    graph.add_node("repair_answer", guarded(workflow.repair_node))
    graph.add_node("react_agent", react_agent)
    graph.add_node("tool_node", tool_node)
    graph.add_node("gate_tools", gate_node)
    graph.add_node("gate_judge", gate_node)
    graph.add_node("claims", claims_node)
    graph.add_node("judge", judge_node)
    graph.add_node("revise", revise_node)
    graph.add_node("finalize", finalize_answer_node)
    graph.add_node("gate_done", gate_node)
    graph.add_node("finish", finish_node)
    graph.add_edge(START, "router")
    graph.add_conditional_edges("router", route_route, {"direct": "gate_direct", "plan": "plan", "react": "react", "clarify": "clarify", "finish": "finish"})
    graph.add_conditional_edges("clarify", clarify_route, {"router": "router", "finish": "finish"})
    graph.add_conditional_edges("gate_direct", gate_route, {"finish": "finish", "continue": "direct_answer"})
    graph.add_edge("direct_answer", "gate_done")
    graph.add_edge("react", "gate")
    graph.add_edge("plan", "gate")
    def research_entry(state):
        if gate_route(state) == "finish":
            return "finish"
        return "operation" if state.get("task_kind") == "operation" else "research"

    graph.add_conditional_edges("gate", research_entry, {"finish": "finish", "operation": "react_agent", "research": "prepare_research"})
    graph.add_conditional_edges("prepare_research", workflow.prepare_route, {"finish": "finish", "search": "search_literature", "read": "read_paper"})
    graph.add_edge("search_literature", "read_paper")
    graph.add_edge("read_paper", "extract_quotes")
    graph.add_conditional_edges("extract_quotes", workflow.read_route, {"finish": "finish", "read": "read_paper", "assess": "assess_evidence"})
    graph.add_conditional_edges("assess_evidence", workflow.assess_route, {"finish": "finish", "more_read": "select_next_papers", "expand": "expand_research", "synthesize": "synthesize_evidence"})
    graph.add_edge("select_next_papers", "read_paper")
    graph.add_edge("expand_research", "search_literature")
    graph.add_conditional_edges("synthesize_evidence", workflow.synthesis_route, {"finish": "finish", "claims": "gate_judge"})
    graph.add_conditional_edges("react_agent", react_route, {"tools": "tool_node", "done": "gate_judge"})
    graph.add_edge("tool_node", "gate_tools")
    graph.add_conditional_edges("gate_tools", gate_route, {"finish": "finish", "continue": "react_agent"})
    graph.add_conditional_edges("gate_judge", gate_route, {"finish": "finish", "continue": "claims"})
    graph.add_edge("claims", "judge")
    def review_entry(state):
        decision = judge_route(state)
        if decision == "revise" and state.get("workflow_stage"):
            return "repair"
        return decision

    graph.add_conditional_edges("judge", review_entry, {"revise": "revise", "repair": "repair_answer", "finalize": "finalize"})
    graph.add_conditional_edges("repair_answer", workflow.repair_route, {
        "finish": "finish", "assess": "assess_evidence", "synthesize": "synthesize_evidence"})
    graph.add_edge("revise", "react_agent")
    graph.add_edge("finalize", "gate_done")
    graph.add_edge("gate_done", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 检查点与图生命周期（默认 SQLite：重启后仍可恢复；memory 仅用于测试）
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 任务运行时：单任务互斥、后台执行、SSE 订阅、恢复与续接
# ---------------------------------------------------------------------------

_ACTIVE: set[str] = set()
_BACKGROUND: dict[str, asyncio.Task] = {}
_CHANNELS: dict[str, "_Channel"] = {}


class _Channel:
    def __init__(self):
        self.history: list[dict] = []
        self.queues: set[asyncio.Queue] = set()
        self.closed = False

    def publish(self, item: dict) -> None:
        self.history = (self.history + [item])[-200:]
        for queue in list(self.queues):
            queue.put_nowait(item)

    def close(self) -> None:
        self.closed = True
        for queue in list(self.queues):
            queue.put_nowait(None)


def _compact_update(update: dict) -> dict:
    """SSE 进度事件的精简状态片段：只保留 ID 与关键标量，绝不携带论文/消息全量负载。"""
    compact = {}
    for key, value in (update or {}).items():
        if key == "papers" and isinstance(value, list):
            compact[key] = [str(paper.get("id")) for paper in value][:20]
        elif key == "messages" and isinstance(value, list):
            compact[key] = {"count": len(value), "last_role": msg_role(value[-1]) if value else None}
        elif key in ("events", "past_steps") and isinstance(value, list):
            compact[key] = value[-3:]
        elif key in ("evidence_pool", "evidence_items", "evidence_sources", "evidence_papers", "workflow_calls") and isinstance(value, dict):
            compact[key] = {"count": len(value)}
        elif key == "workflow_rows":
            compact[key] = {"count": len(value)}
        else:
            compact[key] = value
    return compact


async def _snapshot(task_id: str) -> tuple[dict | None, object, list]:
    compiled = await graph()
    snapshot = await compiled.aget_state({"configurable": {"thread_id": task_id}})
    if snapshot is None or not snapshot.values:
        return None, None, []
    interrupts = getattr(snapshot, "interrupts", None) or []
    paused = getattr(interrupts[0], "value", interrupts[0]) if interrupts else None
    return dict(snapshot.values), paused, list(getattr(snapshot, "next", None) or [])


def _claim_local(task_id: str) -> None:
    if task_id in _ACTIVE:
        raise TaskConflict("任务正在执行，不能重复启动或恢复")
    _ACTIVE.add(task_id)
    _CHANNELS[task_id] = _Channel()


def _release_local(task_id: str) -> None:
    _ACTIVE.discard(task_id)
    channel = _CHANNELS.get(task_id)
    if channel is not None:
        channel.close()


def _prepare_new(task_id: str, goal: str, year_from, year_to, auto_approve, mode, context, max_seconds, parent_task_id,
                 budget: str | None = None) -> dict:
    _claim_local(task_id)
    try:
        initial = initial_state(goal, year_from, year_to, auto_approve, task_id=task_id, mode=mode, context=context,
                                max_seconds=max_seconds, parent_task_id=parent_task_id, budget=budget)
        store.claim_new(task_id, public_state(initial))
        return initial
    except Exception:
        _release_local(task_id)
        raise


async def _prepare_resume(task_id: str, value) -> tuple[object, dict]:
    from langgraph.types import Command
    _claim_local(task_id)
    previous = None
    try:
        previous = store.claim_resume(task_id)
        try:
            state, paused, nxt = await _snapshot(task_id)
        except Exception:
            state, paused, nxt = None, None, []
        if not state:
            raise TaskConflict("检查点不可用（内存检查点在重启后会丢失），无法恢复；请使用 continue 重新发起")
        if value == {}:
            # langgraph 会把空 dict 恢复值当作“无新值”直接按原中断重挂（澄清计数不推进）；
            # 规范化为显式的“无补充信息”，让中断节点正常重入并计为一次无效补充。
            value = {"no_input": True}
        if paused is not None:
            update = {}
            if isinstance(value, dict) and value.get("accessible_paper_ids") is not None:
                update["accessible_paper_ids"] = ToolContext(accessible_paper_ids=value["accessible_paper_ids"]).accessible_paper_ids
            return Command(resume=True if value is None else value, update=update), {**state, **update}
        if nxt:
            return None, state
        raise TaskConflict("任务已结束，无需恢复")
    except Exception:
        store.release_claim(task_id, previous)
        _release_local(task_id)
        raise


async def _run_segment(task_id: str, graph_input, max_seconds, recovering: bool = False) -> dict:
    """执行一段图运行（直到完成或中断），逐节点心跳与广播，结束时落快照。"""
    channel = _CHANNELS.get(task_id) or _Channel()
    config = {"configurable": {"thread_id": task_id}, "recursion_limit": 128}
    store.set_deadline(task_id, max_seconds)
    meter = llm.UsageMeter()
    if recovering:
        prior = await asyncio.to_thread(get_task, task_id) or {}
        meter.add(prior.get("tokens_used") or 0, 0)
    meter_token = llm.attach_usage_meter(meter)
    try:
        compiled = await graph()
        if isinstance(graph_input, dict):
            live_state = dict(graph_input)
        else:
            snapshot, _, _ = await _snapshot(task_id)
            live_state = dict(snapshot or {})
            command_updates = getattr(graph_input, "update", None)
            if isinstance(command_updates, dict):
                live_state.update(command_updates)
        async for chunk in compiled.astream(graph_input, config, stream_mode="updates"):
            for node, update in chunk.items():
                if node == "__interrupt__":
                    continue
                await asyncio.to_thread(store.touch_task, task_id)
                if isinstance(update, dict):
                    live_state.update(update)
                    # Polling clients need the same stage/evidence progress as SSE clients.
                    await asyncio.to_thread(save_task, task_id, task_id, "running", {**public_state(live_state), "status": "running", "tokens_used": meter.total})
                channel.publish({"type": "node", "node": node, "update": _compact_update(update if isinstance(update, dict) else {})})
        state, paused, _ = await _snapshot(task_id)
        state = state or {}
        if recovering:
            state["events"] = list(state.get("events") or []) + [{"step": "recover", "reason": "任务恢复后继续执行", "task_id": task_id}]
        payload = public_state(state)
        payload["tokens_used"] = meter.total
        if paused is not None:
            payload.update(interrupt=paused, status="waiting")
            await asyncio.to_thread(save_task, task_id, task_id, "waiting", payload)
            channel.publish({"type": "interrupt", "task_id": task_id, "status": "waiting", "interrupt": paused})
            return {"task_id": task_id, "status": "waiting", **payload}
        payload["status"] = "done"
        await asyncio.to_thread(save_task, task_id, task_id, "done", payload)
        channel.publish({"type": "state", "task_id": task_id, "status": "done", "task": payload})
        return {"task_id": task_id, "status": "done", **payload}
    except Exception as exc:
        message = str(exc)[:300] or type(exc).__name__
        try:
            current = await asyncio.to_thread(get_task, task_id) or {}
            payload = {key: value for key, value in current.items() if key not in ("task_id", "thread_id", "created_at", "updated_at")}
            await asyncio.to_thread(save_task, task_id, task_id, "failed", {**payload, "status": "failed", "error": message})
        except Exception:
            log.exception("failed to record research task failure")
        channel.publish({"type": "error", "task_id": task_id, "message": message})
        raise
    finally:
        llm.detach_usage_meter(meter_token)
        store.clear_deadline(task_id)
        _release_local(task_id)


def _spawn(task_id: str, coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND[task_id] = task

    def done(finished: asyncio.Task):
        _BACKGROUND.pop(task_id, None)
        if not finished.cancelled() and finished.exception() is not None:
            log.error("background research task %s failed: %s", task_id, finished.exception())

    task.add_done_callback(done)


async def run(goal: str = "", year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False, resume=None,
              task_id: str | None = None, mode: str = "auto", context: dict | None = None, max_seconds: int | None = None,
              parent_task_id: str | None = None, budget: str | None = None) -> dict:
    """阻塞执行（新任务或 resume）。同一任务的并发执行或重复恢复抛 TaskConflict。"""
    task_id = task_id or uuid4().hex
    if resume is not None:
        graph_input, state = await _prepare_resume(task_id, resume)
        return await _run_segment(task_id, graph_input, state.get("max_seconds"), recovering=True)
    initial = _prepare_new(task_id, goal, year_from, year_to, auto_approve, mode, context, max_seconds, parent_task_id,
                           budget=budget)
    return await _run_segment(task_id, initial, initial["max_seconds"])


async def start_background(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False,
                           task_id: str | None = None, mode: str = "auto", context: dict | None = None,
                           max_seconds: int | None = None, parent_task_id: str | None = None,
                           budget: str | None = None) -> dict:
    """创建任务后立即返回 task_id，图在后台执行；进度通过 SSE 订阅。"""
    task_id = task_id or uuid4().hex
    initial = _prepare_new(task_id, goal, year_from, year_to, auto_approve, mode, context, max_seconds, parent_task_id,
                            budget=budget)
    _spawn(task_id, _run_segment(task_id, initial, initial["max_seconds"]))
    return {"task_id": task_id, **public_state(initial), "status": "running"}


async def resume_task(task_id: str, value=None, background: bool = False) -> dict:
    """恢复被中断（计划审批/工具确认/澄清/暂停）或执行失败、心跳过期的任务。"""
    if not background:
        return await run("", resume=True if value is None else value, task_id=task_id)
    graph_input, state = await _prepare_resume(task_id, True if value is None else value)
    _spawn(task_id, _run_segment(task_id, graph_input, state.get("max_seconds"), recovering=True))
    return {"task_id": task_id, **public_state(state), "status": "running", "interrupt": None}


async def continue_task(task_id: str, background: bool = False) -> dict | None:
    """已结束任务（如等待后台导入后部分结束）的续接入口：以相同目标与上下文新建子任务。"""
    current = await asyncio.to_thread(get_task, task_id)
    if current is None:
        return None
    if current.get("status") not in ("done", "failed"):
        raise TaskConflict("任务尚未结束，请使用 resume 继续")
    context = {key: current.get(key) for key in ToolContext.model_fields if current.get(key) is not None}
    mode = current.get("mode") if current.get("mode") in ("react", "plan") else "auto"
    kwargs = {"year_from": current.get("year_from"), "year_to": current.get("year_to"),
              "auto_approve": bool(current.get("auto_approve")), "mode": mode, "context": context, "parent_task_id": task_id}
    if background:
        return await start_background(current.get("goal") or "", **kwargs)
    return await run(current.get("goal") or "", **kwargs)


async def subscribe(task_id: str):
    """订阅本进程内任务的进度广播（先回放已发生的事件）。"""
    channel = _CHANNELS.get(task_id)
    if channel is None:
        return
    queue: asyncio.Queue = asyncio.Queue()
    for item in channel.history:
        queue.put_nowait(item)
    if channel.closed:
        queue.put_nowait(None)
    else:
        channel.queues.add(queue)
    try:
        while (item := await queue.get()) is not None:
            yield item
    finally:
        channel.queues.discard(queue)


async def watch(task_id: str):
    """SSE 只订阅：本进程有广播则订阅，否则下发检查点状态快照。"""
    if task_id in _CHANNELS:
        async for item in subscribe(task_id):
            yield item
        return
    recovered = await recover_state(task_id)
    if recovered is not None:
        yield {"type": "status", **recovered}


async def stream_run(goal: str, year_from: int | None = None, year_to: int | None = None, auto_approve: bool = False,
                     task_id: str | None = None, mode: str = "auto", context: dict | None = None):
    """兼容入口：未在执行的任务先在后台启动，再订阅进度；客户端断开不影响任务继续执行。"""
    task_id = task_id or uuid4().hex
    if task_id not in _ACTIVE:
        await start_background(goal, year_from, year_to, auto_approve, task_id=task_id, mode=mode, context=context)
    async for item in subscribe(task_id):
        yield item


async def recover_state(task_id: str) -> dict | None:
    """从检查点恢复任务状态：waiting（存在中断）/ running（节点未完成）/ done；检查点不可用时回退 research_tasks 行。"""
    try:
        state, paused, nxt = await _snapshot(task_id)
    except Exception:
        state, paused, nxt = None, None, []
    row = await asyncio.to_thread(get_task, task_id)
    if state:
        status = "waiting" if paused is not None else ("running" if nxt else "done")
        if status == "running" and row and row.get("status") == "failed":
            status = "failed"
        payload = public_state(state)
        payload["status"] = status
        payload["interrupt"] = paused
        return {"task_id": task_id, "status": status, "state": payload, "interrupt": paused, "next": nxt, "active": task_id in _ACTIVE}
    if row is None:
        return None
    return {"task_id": task_id, "status": row.get("status"), "state": row, "interrupt": row.get("interrupt"), "next": [],
            "active": task_id in _ACTIVE, "checkpoint_missing": True}


def request_cancel(task_id: str) -> None:
    store.set_flag(task_id, "cancel")


def request_pause(task_id: str) -> None:
    store.set_flag(task_id, "pause")

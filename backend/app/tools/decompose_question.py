"""decompose_question：把找论文的自然语言需求转成可执行的检索目标（不调用论文 API）。"""

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.infrastructure import llm
from app.services.analysis import evidence_analysis as ea

CONTINUATION = re.compile(
    r"^(那|好|行)?(继续|接着)(找|搜|检索|推荐)?"
    r"|^再(找|搜|来|推荐|给)"
    r"|^多(找|来|推荐)"
    r"|^还要(找|搜|推荐)"
    r"|^(继续|接着|再来)$")
YEAR_TOKEN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

RESOLVER_SYSTEM = """你是检索任务的指代消解员。用户当前轮是一条简短的续找/追问指令，对话上下文给你原始主题。
把当前指令还原成一条独立、完整的检索任务语句（包含原主题、数量和新增约束），不要编造上下文没有的主题。
如果上下文不足以确定主题，输出 ambiguous=true。
只输出JSON {"resolved_question": "...", "ambiguous": true|false}"""


class DecomposeQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    context: list[str] | None = Field(None, max_length=10)


async def _resolve_continuation(question: str, context: list[str]) -> tuple[str | None, int]:
    """返回 (resolved_question, llm_calls)。无法确定主题时返回 (None, llm_calls)。"""
    history = "\n".join(str(item)[:500] for item in context if str(item).strip())
    messages = [{"role": "system", "content": RESOLVER_SYSTEM},
                {"role": "user", "content": json.dumps({"context": history, "current": question}, ensure_ascii=False)}]
    try:
        payload = ea.parse_json(await llm.complete(messages))
        resolved = str(payload.get("resolved_question") or "").strip()
        if payload.get("ambiguous") or not resolved:
            return None, 1
        return resolved, 1
    except (llm.AnalysisError, ValueError, TypeError, ValidationError):
        return None, 1


def _extract_years(text: str) -> dict:
    years = [int(match.group(0)) for match in YEAR_TOKEN.finditer(text)]
    if not years:
        return {"year_from": None, "year_to": None}
    if len(years) == 1:
        return {"year_from": years[0], "year_to": years[0]}
    return {"year_from": min(years), "year_to": max(years)}


async def decompose_question(question: str, context: list[str] | None = None) -> dict:
    resolved = question.strip()
    llm_used = 0
    needs_clarification = False
    clarification_question = None
    if CONTINUATION.search(resolved):
        if not context:
            needs_clarification = True
        else:
            resolved, llm_used = await _resolve_continuation(resolved, context)
            if not resolved:
                needs_clarification = True
    if needs_clarification:
        return {
            "tool": "decompose_question",
            "status": "needs_clarification",
            "needs_clarification": True,
            "clarification_question": "想找哪个主题的论文？",
            "resolved_question": "",
            "target_count": 5,
            "core_topics": [],
            "constraints": {"year_from": None, "year_to": None},
            "exclusions": [],
            "queries": [],
            "search_plan": {},
            "llm_calls": llm_used,
        }

    from app.services.analysis.attribution import plan_search

    planned = await plan_search(resolved)
    from app.agents import discovery

    target_count = discovery.requested_count(resolved) or 5
    return {
        "tool": "decompose_question",
        "status": "completed",
        "needs_clarification": False,
        "clarification_question": None,
        "resolved_question": resolved,
        "target_count": target_count,
        "core_topics": (planned.get("search_plan") or {}).get("core_concepts") or [],
        "constraints": _extract_years(resolved),
        "exclusions": [str(item) for item in planned.get("exclusions") or []],
        "queries": [str(item) for item in planned.get("queries") or []],
        "search_plan": planned.get("search_plan") or {},
        "llm_calls": int(planned.get("_llm_used") or 0) + llm_used,
    }

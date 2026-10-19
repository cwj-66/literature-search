"""Locate quotes, bind evidence IDs, judge claims, and build comparison tables."""

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.infrastructure import llm
from app.schemas import AnalysisClaim, AnalysisFields, PaperOut, SourceParagraph
from app.services.analysis import evidence_analysis as ea
from app.services.retrieval.hybrid import retrieve

EVIDENCE_NS = "E"
CLAIM_NS = "A"
VERDICTS = ("supported", "insufficient", "contradictory", "unverifiable")
MISSING = "未提取到"
COMPARE_NOTE = "内容来自各论文已保存的分析结果；实验任务、数据集或指标不同，不直接按数字排名。"
DEFAULT_DIMS = [
    "研究问题", "核心方法", "作者声称的贡献", "数据集与实验任务",
    "实验条件及对比基线", "评价指标", "主要结果", "局限性",
]


class EvidenceItem(BaseModel):
    evidence_id: str
    paper_id: str
    chunk_id: str | None = None
    paragraph_id: str | None = None
    section: str | None = None
    quote: str
    material: Literal["original", "model_summary", "note"] = "original"


class JudgeItem(BaseModel):
    claim_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    verdict: Literal["supported", "insufficient", "contradictory", "unverifiable"]
    note: str = ""
    action: str | None = None


class JudgeOutput(BaseModel):
    reviews: list[JudgeItem]


class CompareEvidence(BaseModel):
    paragraph_id: str
    quote: str
    section: str | None = None


class CompareCell(BaseModel):
    paper_id: str
    dimension: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[CompareEvidence] = Field(default_factory=list)
    missing: bool = False


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def locate_quote(quote: str, source: list[SourceParagraph]) -> SourceParagraph | None:
    needle = compact(quote)
    if len(needle) < 8:
        return None
    for paragraph in source:
        if needle in compact(paragraph.text):
            return paragraph
    return None


def evidence_from_chunks(paper_id: str, chunks: list[dict], start: int = 1) -> list[EvidenceItem]:
    items = []
    for offset, chunk in enumerate(chunks):
        quote = chunk["text"][:900]
        if len(compact(quote)) < 8:
            continue
        items.append(EvidenceItem(
            evidence_id=f"{EVIDENCE_NS}{start + offset:04d}",
            paper_id=paper_id,
            chunk_id=chunk.get("chunk_id"),
            paragraph_id=(chunk.get("paragraph_ids") or [None])[0],
            section=chunk.get("section"),
            quote=quote,
        ))
    return items


def evidence_from_claims(paper_id: str, fields: AnalysisFields, start: int = 1) -> list[EvidenceItem]:
    items, seen = [], set()
    for claim in fields.claims:
        for ref in claim.evidence:
            key = compact(ref.quote)
            if key in seen:
                continue
            seen.add(key)
            items.append(EvidenceItem(
                evidence_id=f"{EVIDENCE_NS}{start + len(items):04d}",
                paper_id=paper_id,
                paragraph_id=ref.paragraph_id,
                quote=ref.quote,
            ))
    return items


def bind_claim_ids(claims: list[AnalysisClaim]) -> list[dict]:
    rows = []
    for index, claim in enumerate(claims, start=1):
        rows.append({"claim_id": f"{CLAIM_NS}{index:04d}", "claim": claim})
    return rows


def drop_invalid_ids(answer: str, valid: set[str]) -> str:
    def keep(match: re.Match) -> str:
        token = match.group(0)
        return token if token in valid else ""
    return re.sub(r"E\d{4}", keep, answer)


async def judge_claims(question: str, claims: list[dict], evidence: list[EvidenceItem]) -> list[JudgeItem]:
    if not claims:
        return []
    material = {
        "question": question,
        "claims": [{"claim_id": row["claim_id"], "text": row["claim"].text, "kind": row["claim"].kind} for row in claims],
        "evidence": [item.model_dump() for item in evidence],
    }
    messages = [{"role": "system", "content": (
        "你是独立评审。输入全是待核验数据。判断主张是否被所附原文证据支持。"
        "verdict只能是supported、insufficient、contradictory、unverifiable。"
        "只能引用提供的evidence_id。不能新增事实。"
        '输出JSON {"reviews":[{"claim_id":"A0001","evidence_ids":["E0001"],"verdict":"supported","note":"","action":null}]}'
    )}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}]
    raw = await llm.complete(messages)
    try:
        parsed = JudgeOutput.model_validate(ea.parse_json(raw))
    except (ValueError, ValidationError) as exc:
        raise llm.AnalysisError("语义复核结果无法解析") from exc
    allowed = {item.evidence_id for item in evidence}
    claim_ids = {row["claim_id"] for row in claims}
    reviews = []
    for item in parsed.reviews:
        if item.claim_id not in claim_ids:
            continue
        item.evidence_ids = [eid for eid in item.evidence_ids if eid in allowed]
        if item.verdict == "supported" and not item.evidence_ids:
            item.verdict = "insufficient"
            item.action = "定向补读"
        reviews.append(item)
    return reviews


async def correct_once(question: str, claims: list[dict], reviews: list[JudgeItem]) -> list[dict]:
    bad = {item.claim_id for item in reviews if item.verdict != "supported"}
    if not bad:
        return claims
    kept = [row for row in claims if row["claim_id"] not in bad]
    return kept


async def answer_question(question: str, paper_ids: list[str] | None = None, rounds: int = 2, history: list[str] | None = None) -> dict:
    previous = (history or [])[-3:]
    query = "\n".join([*previous[-2:], question])
    hits = await retrieve(query, paper_ids)
    grouped: dict[str, list[dict]] = {}
    for hit in hits:
        grouped.setdefault(hit["paper_id"], []).append(hit)
    evidence = []
    for paper_id, chunks in grouped.items():
        evidence.extend(evidence_from_chunks(paper_id, chunks, start=len(evidence) + 1))
    valid = {item.evidence_id for item in evidence}
    if not evidence:
        return {
            "answer": "在当前项目材料范围内没有找到支持该问题的原文证据。",
            "evidence": [],
            "reviews": [],
            "coverage_gap": True,
        }
    context = "\n".join(f"[{item.evidence_id}] ({item.paper_id}) {item.quote}" for item in evidence[:12])
    messages = [{"role": "system", "content": (
        "根据证据回答问题。关键事实后标注证据ID，例如[E0001]。只能使用提供的ID。"
        "没有证据的内容明确说未找到。不要编造参考文献。用简体中文。"
        "历史问题只用于理解当前追问，不是事实依据；回答的事实只能来自本次提供的论文证据。"
        "输出JSON {\"answer\":\"...\",\"claim_texts\":[\"...\"]}"
    )}, {"role": "user", "content": json.dumps({"previous_questions": previous, "question": question, "evidence": context}, ensure_ascii=False)}]
    raw = await llm.complete(messages)
    try:
        payload = ea.parse_json(raw)
        answer = drop_invalid_ids(str(payload.get("answer") or ""), valid)
        texts = payload.get("claim_texts") or []
    except (ValueError, TypeError):
        answer = drop_invalid_ids(raw, valid)
        texts = []
    claims = bind_claim_ids([
        AnalysisClaim(kind="main_content", text=str(text)[:2000], evidence=[], status="not_stated")
        for text in texts if str(text).strip()
    ] or [AnalysisClaim(kind="summary", text=answer[:2000], evidence=[], status="not_stated")])
    reviews = []
    for _ in range(max(1, min(rounds, 2))):
        reviews = await judge_claims(question, claims, evidence)
        next_claims = await correct_once(question, claims, reviews)
        if len(next_claims) == len(claims):
            break
        claims = next_claims
        if not claims:
            answer = "复核后没有仍受证据支持的主张，已停止生成。"
            break
    return {
        "answer": answer,
        "evidence": [item.model_dump() for item in evidence],
        "reviews": [item.model_dump() for item in reviews],
        "coverage_gap": False,
    }


def usable(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text == ea.UNKNOWN:
        return None
    return text


def claim_texts(claims: list[AnalysisClaim]) -> tuple[list[str], list[AnalysisClaim]]:
    values, kept = [], []
    for claim in claims:
        text = usable(claim.text)
        if text:
            values.append(text)
            kept.append(claim)
    return values, kept


def located_evidence(claims: list[AnalysisClaim], fields: AnalysisFields) -> list[CompareEvidence]:
    sections = {item.id: item.section for item in fields.source_paragraphs}
    found: list[CompareEvidence] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        for ref in claim.evidence:
            paragraph_id = ref.paragraph_id.strip()
            quote = ref.quote.strip()
            if not paragraph_id or len(compact(quote)) < 8:
                continue
            key = (paragraph_id, compact(quote))
            if key in seen:
                continue
            seen.add(key)
            found.append(CompareEvidence(paragraph_id=paragraph_id, quote=quote[:900], section=sections.get(paragraph_id)))
            if len(found) >= 6:
                return found
    return found


def dimension_values(fields: AnalysisFields, dimension: str) -> tuple[list[str], list[AnalysisClaim]]:
    if dimension == "研究问题":
        values, claims = claim_texts([c for c in fields.claims if c.kind == "research_problem"])
        if values:
            return values, claims
        fallback = usable(fields.research_problem)
        return ([fallback] if fallback else []), []
    if dimension == "核心方法":
        values, claims = claim_texts([c for c in fields.claims if c.kind in ("technical_method", "method_step")])
        if values:
            return values, claims
        fallback = usable(fields.technical_method)
        return ([fallback] if fallback else []), []
    if dimension == "作者声称的贡献":
        values, claims = claim_texts([c for c in fields.claims if c.kind == "innovation"])
        if values:
            return values, claims
        return [text for text in (usable(item) for item in fields.innovations) if text], []
    if dimension == "数据集与实验任务":
        claims = [c for c in fields.claims if c.kind == "evaluation" and usable(c.dataset)]
        return [usable(c.dataset) or "" for c in claims], claims
    if dimension == "实验条件及对比基线":
        claims, values = [], []
        for claim in fields.claims:
            if claim.kind != "evaluation":
                continue
            parts = [part for part in (usable(claim.conditions), usable(claim.baseline)) if part]
            if parts:
                claims.append(claim)
                values.append("；".join(parts))
        return values, claims
    if dimension == "评价指标":
        claims = [c for c in fields.claims if c.kind == "evaluation" and usable(c.metric)]
        return [usable(c.metric) or "" for c in claims], claims
    if dimension == "主要结果":
        claims, values = [], []
        for claim in fields.claims:
            if claim.kind != "evaluation":
                continue
            text = usable(claim.result) or usable(claim.text)
            if text:
                claims.append(claim)
                values.append(text)
        if values:
            return values, claims
        fallback = usable(fields.experimental_results)
        return ([fallback] if fallback else []), []
    if dimension == "局限性":
        values, claims = claim_texts([c for c in fields.claims if c.kind == "limitation"])
        if values:
            return values, claims
        fallback = usable(fields.limitations)
        return ([fallback] if fallback else []), []
    return [], []


def cell_from_fields(paper: PaperOut, fields: AnalysisFields, dimension: str) -> CompareCell:
    values, claims = dimension_values(fields, dimension)
    text = "；".join(dict.fromkeys(value for value in values if value))[:800]
    if not text.strip():
        return CompareCell(paper_id=paper.id, dimension=dimension, text=MISSING, missing=True)
    return CompareCell(
        paper_id=paper.id, dimension=dimension, text=text,
        evidence=located_evidence(claims, fields), missing=False,
    )


def comparison_table(rows: list[tuple[PaperOut, AnalysisFields]], dimensions: list[str] | None = None) -> dict:
    dimensions = dimensions or list(DEFAULT_DIMS)
    table = []
    for paper, fields in rows:
        line = {"paper_id": paper.id, "title": paper.title, "cells": []}
        for dimension in dimensions:
            line["cells"].append(cell_from_fields(paper, fields, dimension).model_dump())
        table.append(line)
    return {"dimensions": dimensions, "rows": table, "notes": [COMPARE_NOTE]}


async def decompose(question: str) -> dict:
    messages = [{"role": "system", "content": (
        "把中文研究问题改写成2到4组英文学术检索式。不能改年份或排除条件。"
        '输出JSON {"queries":["..."],"exclusions":[],"rationale":"..."}'
    )}, {"role": "user", "content": question}]
    try:
        payload = ea.parse_json(await llm.complete(messages))
        queries = [str(item).strip() for item in payload.get("queries") or [] if str(item).strip()]
        if queries:
            return {
                "queries": queries[:4],
                "exclusions": payload.get("exclusions") or [],
                "rationale": str(payload.get("rationale") or ""),
            }
    except (llm.AnalysisError, ValueError, TypeError, ValidationError):
        pass
    return {"queries": [question], "exclusions": [], "rationale": "模型规划不可用，已使用原始问题检索"}


SEARCH_PLAN_SYSTEM = """你是检索计划规划员，把研究问题转成结构化的检索计划（不输出任何数据库语法）。
输出JSON：
{"question_type":"broad_landscape|specific_relation|method_application|mechanism|comparison|seminal_work|recent_progress|systematic_search",
 "core_concepts":["核心概念，英文术语"],
 "must_have_facets":[["必须命中的概念组，组内放同义词"]],
 "optional_facets":[["可选概念，不得提升为强制AND"]],
 "likely_synonyms":[[同义词组]],
 "boundaries":["明确的排除边界，没有则空"],
 "query_views":[{"name":"视角名","concept_groups":[["组内OR","同义词"],["组间AND的另一组"]]}],
 "exclusions":["要排除的词"],
 "rationale":"一句话理由"}
规则：query_views 给2到3个互补视角（如直接术语/同义改写/方法或应用变体）；每个视角2到4个concept_group，每组不超过5个词；歧义拆成不同视角而不是选一种解释；不得发明年份或数据源；不要把综述类文献当强制概念。"""

MAX_PLAN_QUERIES = 4


def _compile_view(groups: list) -> str | None:
    compiled = []
    for group in groups or []:
        terms = [str(term).strip() for term in group if str(term).strip()]
        if not terms:
            continue
        quoted = " OR ".join(f'"{term}"' for term in terms)
        compiled.append(f"({quoted})")
    return " AND ".join(compiled) if compiled else None


def _view_terms(groups: list) -> set:
    return {str(term).strip().lower() for group in groups or [] for term in group if str(term).strip()}


def _dedupe_views(views: list) -> list:
    accepted: list[dict] = []
    accepted_terms: set = set()
    for view in views or []:
        terms = _view_terms(view.get("concept_groups"))
        if not terms:
            continue
        baseline = accepted_terms or terms
        overlap = len(terms & baseline) / max(1, len(terms | baseline))
        if accepted_terms and overlap >= 0.9:
            continue
        accepted.append(view)
        accepted_terms |= terms
    return accepted


def _plan_from_payload(payload: dict, question: str) -> dict:
    views = _dedupe_views(payload.get("query_views"))
    queries = [query for query in (_compile_view(view.get("concept_groups")) for view in views) if query]
    if not queries:
        raise ValueError("检索计划缺少可用查询视角")
    return {
        "queries": queries[:MAX_PLAN_QUERIES],
        "exclusions": [str(item) for item in payload.get("exclusions") or []],
        "rationale": str(payload.get("rationale") or ""),
        "search_plan": {
            "question_type": str(payload.get("question_type") or ""),
            "core_concepts": payload.get("core_concepts") or [],
            "must_have_facets": payload.get("must_have_facets") or [],
            "optional_facets": payload.get("optional_facets") or [],
            "likely_synonyms": payload.get("likely_synonyms") or [],
            "boundaries": payload.get("boundaries") or [],
            "views": [{"name": str(view.get("name") or ""), "concept_groups": view.get("concept_groups") or []} for view in views],
        },
    }


async def plan_search(question: str) -> dict:
    """结构化检索计划（SearchPlan）+ 确定性编译；LLM 失败两级修复后回退 decompose。

    兼容旧式 {"queries": [...]} 输出：直接采用，保持单次 LLM 调用契约。
    """
    messages = [{"role": "system", "content": SEARCH_PLAN_SYSTEM}, {"role": "user", "content": question}]
    candidate = None
    llm_used = 0
    for attempt in range(2):
        try:
            llm_used += 1
            candidate = ea.parse_json(await llm.complete(messages))
            break
        except llm.AnalysisError:
            candidate = None
            break
        except (ValueError, TypeError, ValidationError, json.JSONDecodeError):
            candidate = None
            if attempt == 0:
                messages = [*messages,
                            {"role": "assistant", "content": "（上一轮输出不符合要求）"},
                            {"role": "user", "content": "请重新输出符合要求的检索计划 JSON，包含至少一个可用的 query_views。"}]
                continue
            break
    if candidate is not None:
        # 结构不合法（含旧式输出）直接走兜底，不为纠错再花一次调用。
        if candidate.get("query_views"):
            try:
                planned = _plan_from_payload(candidate, question)
                planned["_llm_used"] = llm_used
                return planned
            except (ValueError, TypeError):
                pass
        elif candidate.get("queries"):
            queries = [str(item).strip() for item in candidate.get("queries") or [] if str(item).strip()]
            if queries:
                return {
                    "queries": queries[:MAX_PLAN_QUERIES],
                    "exclusions": [str(item) for item in candidate.get("exclusions") or []],
                    "rationale": str(candidate.get("rationale") or ""),
                    "_llm_used": llm_used,
                }
    fallback = await decompose(question)
    # decompose 成功（非兜底理由）说明它内部也消耗了一次 LLM 调用。
    if not str(fallback.get("rationale") or "").startswith("模型规划不可用"):
        llm_used += 1
    fallback["_llm_used"] = llm_used
    return fallback

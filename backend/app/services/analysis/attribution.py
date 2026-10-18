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
DEFAULT_DIMS = ["研究问题", "核心机制", "作者声称的贡献", "数据集", "评价指标", "实验条件", "主要结果", "局限性"]
KIND_DIM = {
    "research_problem": "研究问题",
    "technical_method": "核心机制",
    "innovation": "作者声称的贡献",
    "evaluation": "主要结果",
    "limitation": "局限性",
}


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


class CompareCell(BaseModel):
    paper_id: str
    dimension: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
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


def cell_from_fields(paper: PaperOut, fields: AnalysisFields, dimension: str, start: int = 1) -> CompareCell:
    if dimension == "数据集":
        claims = [c for c in fields.claims if c.kind == "evaluation" and c.dataset]
        values = [c.dataset for c in claims if c.dataset]
    elif dimension == "评价指标":
        claims = [c for c in fields.claims if c.kind == "evaluation" and c.metric]
        values = [c.metric for c in claims if c.metric]
    elif dimension == "实验条件":
        claims = [c for c in fields.claims if c.kind == "evaluation" and c.conditions]
        values = [c.conditions for c in claims if c.conditions]
    else:
        kinds = [kind for kind, name in KIND_DIM.items() if name == dimension]
        claims = [c for c in fields.claims if c.kind in kinds]
        values = [c.text for c in claims]
    text = "；".join(value for value in values if value and value != ea.UNKNOWN)
    if not text.strip():
        return CompareCell(paper_id=paper.id, dimension=dimension, text="未找到", missing=True)
    ids = [f"{EVIDENCE_NS}{start + index:04d}" for index, claim in enumerate(claims) if claim.evidence]
    return CompareCell(paper_id=paper.id, dimension=dimension, text=text[:800], evidence_ids=ids[:6], missing=False)


def comparison_table(rows: list[tuple[PaperOut, AnalysisFields]], dimensions: list[str] | None = None) -> dict:
    dimensions = dimensions or DEFAULT_DIMS
    table = []
    notes = []
    datasets = set()
    metrics = set()
    for paper, fields in rows:
        line = {"paper_id": paper.id, "title": paper.title, "cells": []}
        for dimension in dimensions:
            cell = cell_from_fields(paper, fields, dimension, start=len(table) * 10 + 1)
            line["cells"].append(cell.model_dump())
            if dimension == "数据集" and not cell.missing:
                datasets.add(cell.text)
            if dimension == "评价指标" and not cell.missing:
                metrics.add(cell.text)
        table.append(line)
    if len(datasets) > 1 or len(metrics) > 1:
        notes.append("数据集或评价指标不一致，结果不可直接排名。")
    return {"dimensions": dimensions, "rows": table, "notes": notes}


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

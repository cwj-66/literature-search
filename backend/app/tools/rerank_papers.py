"""rerank_papers：围绕完整检索目标筛选候选，输出推荐、评分、理由与不足原因。"""

import re

from pydantic import BaseModel, ConfigDict, Field

from app.services.retrieval import ranking

RAG_GUARD = re.compile(r"retriev\w*|language models?|\bLLMs?\b|generative (?:AI|models?)|检索增强", re.I)
MIN_RELEVANT_SCORE = 7


class RerankPapersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_question: str = Field(min_length=1, max_length=2000)
    candidates: list[dict] = Field(min_length=1, max_length=200)
    target_count: int = Field(5, ge=1, le=50)
    constraints: dict | None = None
    groups: list[list[str]] | None = None


def _build_reason(paper: dict, hits: list[str], score: float | None) -> str:
    from app.agents import discovery

    if hits:
        title_hits = [term for term in hits if discovery.contains(paper.get("title") or "", term)]
        basis = "标题" + ("与摘要" if len(title_hits) != len(hits) else "")
        reason = f"{basis}匹配：" + "、".join(dict.fromkeys(hits))
        if not (paper.get("abstract") or "").strip():
            reason += "（该论文无摘要，仅依据标题判断）"
        return reason
    if score is not None:
        return f"大模型相关性评分 {score}/10"
    return "未获得评分，保留待人工判断"


async def rerank_papers(resolved_question: str, candidates: list[dict], target_count: int = 5,
                        constraints: dict | None = None, groups: list[list[str]] | None = None) -> dict:
    from app.agents import discovery

    unique = list({str(paper.get("id")): paper for paper in candidates if paper.get("id")}.values())
    if groups is None:
        groups = discovery.required_groups(resolved_question)
    constraints = constraints or {}
    year_from = constraints.get("year_from")
    year_to = constraints.get("year_to")
    exclusions = [str(item) for item in constraints.get("exclusions") or []]
    is_rag_topic = any(discovery.contains(resolved_question, term) for term in discovery.RAG)

    survivors = []
    for paper in unique:
        year = paper.get("year")
        if year_from is not None and (year is None or year < year_from):
            continue
        if year_to is not None and (year is None or year > year_to):
            continue
        text = f"{paper.get('title') or ''} {paper.get('abstract') or ''}"
        if any(discovery.contains(text, term) for term in exclusions):
            continue
        hits = [next((term for term in group if discovery.contains(text, term)), None) for group in groups]
        if groups and not all(hits):
            continue
        if is_rag_topic and not RAG_GUARD.search(text):
            continue
        survivors.append((paper, [term for term in hits if term]))

    ranked, llm_delta = await ranking.llm_rank([paper for paper, _ in survivors], resolved_question,
                                               min_candidates=2)
    chosen = [paper for paper in ranked
              if paper.get("rank_score") is None or paper.get("rank_score") >= MIN_RELEVANT_SCORE][:target_count]
    hit_map = {paper.get("id"): hits for paper, hits in survivors}

    recommended, reasons, scores = [], {}, {}
    for paper in chosen:
        score = paper.get("rank_score")
        scores[paper["id"]] = score
        reasons[paper["id"]] = _build_reason(paper, hit_map.get(paper["id"]) or [], score)
        recommended.append({**paper, "score": score, "reason": reasons[paper["id"]]})

    enough = len(recommended) >= target_count
    if enough:
        shortfall_reason = None
    elif recommended:
        shortfall_reason = (f"候选 {len(candidates)} 篇中仅 {len(recommended)} 篇通过主题相关性筛选，"
                            f"未用无关论文凑数")
    else:
        shortfall_reason = "没有候选通过主题相关性筛选"
    chosen_ids = {paper["id"] for paper in chosen}
    remaining = [paper["id"] for paper, _ in survivors if paper["id"] not in chosen_ids][:20]

    return {
        "tool": "rerank_papers",
        "status": "completed",
        "recommended": recommended,
        "recommended_ids": [paper["id"] for paper in recommended],
        "scores": scores,
        "reasons": reasons,
        "remaining_candidates": remaining,
        "enough": enough,
        "shortfall_reason": shortfall_reason,
        "filtered_out_count": len(unique) - len(survivors),
        "llm_calls": llm_delta,
    }

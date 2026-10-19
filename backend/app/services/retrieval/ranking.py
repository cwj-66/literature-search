"""LLM 绝对量表候选相关性打分（PaperSeek ranking.py 思路的精简版）。

只在候选超过 min_candidates 时触发；0-10 绝对打分，分数与理由不分两次调用
（候选规模 ≤ 几百，单次 JSON 足够）。任何失败都降级为保留本地顺序，绝不阻塞检索主链。
"""

import json
import logging

from app.infrastructure import llm

log = logging.getLogger(__name__)

DEFAULT_MIN_CANDIDATES = 20
MAX_SCORE_CHARS = 300

RANK_SYSTEM = """你是文献相关性评审。对每条候选按 0-10 绝对量表打分：只根据标题/摘要与检索目标的相关性，
不使用引用数、期刊名气等外部信号；7 分以上要求标题或摘要与目标有直接关系证据。
输出JSON {"scores":[[候选序号, 分数], ...]}，每个序号必须恰好出现一次。"""


async def llm_rank(papers: list, query: str, *, min_candidates: int = DEFAULT_MIN_CANDIDATES) -> tuple[list, int]:
    """返回 (按分数降序的候选列表[带 rank_score], 消耗的 LLM 调用数)。失败保留本地顺序。"""
    if len(papers) < min_candidates or not papers:
        return list(papers), 0
    catalog = [
        {"i": index, "title": str(paper.get("title") or ""), "abstract": str(paper.get("abstract") or "")[:MAX_SCORE_CHARS]}
        for index, paper in enumerate(papers)
    ]
    messages = [
        {"role": "system", "content": RANK_SYSTEM},
        {"role": "user", "content": json.dumps({"query": query, "candidates": catalog}, ensure_ascii=False)},
    ]
    try:
        payload = json.loads(await llm.complete(messages))
        raw_scores = payload.get("scores") or []
    except (llm.AnalysisError, ValueError, TypeError, KeyError, AttributeError) as exc:
        log.warning("candidate ranking unavailable, keeping local order: %s", type(exc).__name__)
        return list(papers), 1
    scores: dict[int, float] = {}
    for item in raw_scores:
        try:
            index, score = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= index < len(papers):
            scores[index] = score
    for index, paper in enumerate(papers):
        if index in scores:
            tagged = dict(paper)
            tagged["rank_score"] = scores[index]
            papers[index] = tagged
    head = sorted((index for index in scores), key=lambda index: -scores[index])
    head_set = set(head)
    ranked = [papers[index] for index in head]
    ranked.extend(paper for index, paper in enumerate(papers) if index not in head_set)
    return ranked, 1


__all__ = ["DEFAULT_MIN_CANDIDATES", "llm_rank"]

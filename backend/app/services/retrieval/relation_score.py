"""Association strength from semantic similarity, shared keywords, and real citations."""

import math
import os
import re

ALGORITHM = "rel-v3-three-channel"
SIMILAR_COSINE = 0.55
SIMILAR_SHARED = 2
EMBED_LIMIT = 40
NOTE = (
    "关联强度是系统内部的 0–100 分，不是引用概率。"
    "引用关系只在 Semantic Scholar 的参考文献或被引列表能够证明时成立；"
    "主题相似由语义向量和共同关键词推算。高相似度不会被标成引用。"
)
DEFAULT_WEIGHTS = {"semantic": 0.55, "keyword": 0.25, "citation": 0.20}
STOP = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "been",
    "have", "has", "had", "not", "but", "its", "into", "using", "use", "used", "based",
    "via", "than", "then", "also", "can", "may", "our", "their", "between", "over",
    "under", "such", "these", "those", "which", "while", "where", "when", "what",
    "who", "how", "about", "after", "before", "across", "among", "within", "without",
    "paper", "study", "method", "methods", "approach", "results", "result", "model",
    "models", "data", "analysis",
}
UNKNOWN = "论文未明确说明"


def _read_weight(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or value < 0:
        return default
    return value


def load_weights() -> dict[str, float]:
    raw = {
        "semantic": _read_weight("RELATION_WEIGHT_SEMANTIC", DEFAULT_WEIGHTS["semantic"]),
        "keyword": _read_weight("RELATION_WEIGHT_KEYWORD", DEFAULT_WEIGHTS["keyword"]),
        "citation": _read_weight("RELATION_WEIGHT_CITATION", DEFAULT_WEIGHTS["citation"]),
    }
    total = sum(raw.values())
    if total <= 0:
        raw = dict(DEFAULT_WEIGHTS)
        total = sum(raw.values())
    return {key: value / total for key, value in raw.items()}


def formula(weights: dict[str, float], semantic_available: bool, citation_available: bool = True) -> str:
    active = []
    if semantic_available:
        active.append("semantic")
    active.append("keyword")
    if citation_available:
        active.append("citation")
    usable = sum(weights[name] for name in active)
    if usable <= 0:
        return "语义向量与引用信息暂不可用，无法计算关联强度"
    labels = {"semantic": "语义余弦", "keyword": "关键词重合", "citation": "引用标记"}
    body = " + ".join(f"{weights[name] / usable:.2f}×{labels[name]}" for name in active)
    text = f"关联强度 = 100 × ({body})"
    missing = []
    if not semantic_available:
        missing.append("语义向量")
    if not citation_available:
        missing.append("引用信息")
    if missing:
        text += "。" + "、".join(missing) + "未知／暂不可用，不按 0 分计入"
    return text


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def embed_input(title: str, abstract: str | None) -> str:
    text = re.sub(r"\s+", " ", title or "").strip()
    summary = re.sub(r"\s+", " ", abstract or "").strip()
    if summary:
        text = f"{text}\n{summary}"
    return text[:2000]


def content_words(text: str) -> list[str]:
    words = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,12}", text):
        token = word.casefold()
        if token in STOP or token == UNKNOWN:
            continue
        words.append(token)
    return words


def seed_terms(keywords: list[str], title: str, abstract: str | None) -> list[str]:
    picked = []
    for item in keywords:
        text = item.strip()
        if text and text != UNKNOWN and text not in picked:
            picked.append(text)
        if len(picked) >= 12:
            return picked
    if picked:
        return picked
    for word in content_words(title):
        if word not in picked:
            picked.append(word)
        if len(picked) >= 8:
            return picked
    counts: dict[str, int] = {}
    for word in content_words(abstract or ""):
        counts[word] = counts.get(word, 0) + 1
    for word in sorted(counts, key=lambda item: (-counts[item], item)):
        if word not in picked:
            picked.append(word)
        if len(picked) >= 12:
            break
    return picked


def match_terms(terms: list[str], title: str, abstract: str | None) -> list[str]:
    text = f"{title}\n{abstract or ''}"
    folded = text.casefold()
    tokens = set(content_words(text))
    matched = []
    for term in terms:
        folded_term = term.casefold().strip()
        if not folded_term or folded_term == UNKNOWN:
            continue
        parts = content_words(term)
        if folded_term in folded or (parts and all(part in tokens for part in parts)):
            matched.append(term)
    return matched


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def strength(semantic: float | None, keyword: float, citation: float | None, weights: dict[str, float]) -> int:
    keyword = _clamp(keyword)
    active = {"keyword": keyword}
    if semantic is not None:
        active["semantic"] = _clamp(semantic)
    if citation is not None:
        active["citation"] = 1.0 if citation >= 1 else 0.0
    usable = sum(weights[name] for name in active)
    if usable <= 0:
        return 0
    raw = sum(weights[name] * value for name, value in active.items()) / usable
    return max(0, min(100, int(round(raw * 100))))


def judge(
    semantic: float | None,
    shared: list[str],
    seed_count: int,
    directions: set[str],
    weights: dict[str, float],
    citation_known: bool = True,
) -> dict:
    keyword_score = len(shared) / seed_count if seed_count else 0.0
    if directions:
        citation_score = 1.0
    elif citation_known:
        citation_score = 0.0
    else:
        citation_score = None
    semantic_score = None if semantic is None else round(_clamp(semantic), 4)
    similar = (semantic_score is not None and semantic_score >= SIMILAR_COSINE) or len(shared) >= SIMILAR_SHARED
    relations = []
    if directions:
        relations.append("cites")
    if similar:
        relations.append("similar")
    reasons = []
    if shared:
        shown = "、".join(shared[:8])
        if len(shared) > 8:
            shown += "等"
        reasons.append({"type": "keyword", "text": f"共同关键词：{shown}"})
    if semantic_score is not None:
        prefix = "主题相似：" if similar and semantic_score >= SIMILAR_COSINE else ""
        reasons.append({
            "type": "semantic",
            "text": f"{prefix}标题与摘要的语义余弦为 {semantic_score:.2f}（算法推算）",
        })
    if "out" in directions and "in" in directions:
        citation_text = "Semantic Scholar 记录当前论文与该论文互相引用"
    elif "out" in directions:
        citation_text = "Semantic Scholar 参考文献记录当前论文引用了该论文"
    elif "in" in directions:
        citation_text = "Semantic Scholar 被引列表记录该论文引用了当前论文"
    elif citation_score is None:
        citation_text = "引用信息未知／暂不可用"
    else:
        citation_text = ""
    if citation_text:
        reasons.append({"type": "citation", "text": citation_text})
    if not reasons:
        reasons.append({"type": "keyword", "text": "标题和摘要中没有足够的共同关键词，也没有引用记录"})
    return {
        "semantic_score": semantic_score,
        "keyword_score": round(keyword_score, 4),
        "citation_score": citation_score,
        "strength": strength(semantic, keyword_score, citation_score, weights),
        "relations": relations,
        "reasons": reasons,
        "directions": set(directions),
    }

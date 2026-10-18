"""Recall candidate papers and score citation edges separately from similarity."""

import asyncio
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from app.infrastructure import embeddings
from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.analysis.analysis import ResolveError, resolve_target
from app.services.papers import library
from app.services.papers.library import get_paper, save_paper
from app.services.papers.paper_merge import combine, identity_keys
from app.services.retrieval import relation_score, search
from app.services.retrieval.relation_score import ALGORITHM, EMBED_LIMIT, NOTE
from app.services.retrieval.search import SourceFailure, fetch_paper, semantic_enabled

log = logging.getLogger(__name__)
_LOCKS: dict[str, asyncio.Lock] = {}
SEARCH_LIMIT = 20
LINK_LIMIT = 20
LOCAL_LIMIT = 20
CANDIDATE_LIMIT = 40
CACHE_TTL_DAYS = 7


def lock_for(local_id: str) -> asyncio.Lock:
    return _LOCKS.setdefault(local_id, asyncio.Lock())


def canonical_id(paper: PaperOut) -> str:
    if paper.arxiv_id:
        return "arxiv:" + paper.arxiv_id
    if paper.doi:
        return "doi:" + paper.doi.lower()
    if paper.semantic_scholar_id:
        return "s2:" + paper.semantic_scholar_id
    return paper.id


def s2_paper_ref(paper: PaperOut) -> str | None:
    if paper.semantic_scholar_id:
        return paper.semantic_scholar_id
    if paper.arxiv_id:
        return "ARXIV:" + paper.arxiv_id
    if paper.doi:
        return "DOI:" + paper.doi
    if paper.id.startswith("s2:"):
        return paper.id[3:]
    if paper.id.startswith("arxiv:"):
        return "ARXIV:" + paper.id[6:]
    if paper.id.startswith("doi:"):
        return "DOI:" + paper.id[4:]
    return None


def recall_query(terms: list[str], title: str) -> str:
    parts: list[str] = []
    for term in terms:
        parts.extend(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", term))
        if len(parts) >= 8:
            break
    if not parts:
        parts = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", title)
    return " ".join(parts[:8])[:300]


def latest_keywords(local_id: str) -> list[str]:
    with connection() as db:
        row = db.execute(
            "SELECT payload FROM analysis_results WHERE local_id = ? ORDER BY generated_at DESC, result_id DESC LIMIT 1",
            (local_id,),
        ).fetchone()
    if not row:
        return []
    try:
        data = json.loads(row["payload"])
    except json.JSONDecodeError:
        return []
    values = data.get("keywords") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return []
    return [item.strip() for item in values if isinstance(item, str) and item.strip()]


def remember(groups: list[dict], paper: PaperOut, direction: str | None, seed_keys: set[str], remote: bool) -> None:
    keys = set(identity_keys(paper))
    if not keys or keys & seed_keys:
        return
    for group in groups:
        if keys & group["keys"]:
            group["keys"].update(keys)
            group["paper"] = combine(group["paper"], paper)
            if direction:
                group["directions"].add(direction)
            group["remote"] = group["remote"] or remote
            return
    groups.append({
        "keys": keys,
        "paper": paper,
        "directions": {direction} if direction else set(),
        "remote": remote,
    })


def select_for_embedding(groups: list[dict], limit: int = EMBED_LIMIT) -> list[dict]:
    citations = [group for group in groups if group["directions"]]
    others = [group for group in groups if not group["directions"]]
    others.sort(key=lambda group: (0 if group["shared"] else 1 if group["remote"] else 2))
    citation_budget = min(len(citations), limit if not others else limit // 2)
    return citations[:citation_budget] + others[:limit - citation_budget]


def cache_key(local_id: str, weights: dict[str, float], model: str) -> str:
    with connection() as db:
        count, stamp = db.execute("SELECT COUNT(*), MAX(imported_at) FROM papers").fetchone()
        analyzed = db.execute(
            "SELECT MAX(generated_at) FROM analysis_results WHERE local_id = ?",
            (local_id,),
        ).fetchone()[0]
    signature = ",".join(f"{weights[name]:.6f}" for name in ("semantic", "keyword", "citation"))
    return f"{ALGORITHM}|{model}|{signature}|{count}|{stamp}|{analyzed or ''}"


def cache_fresh(data: dict, now: datetime | None = None) -> bool:
    raw = (data.get("recommendations") or {}).get("computed_at")
    if not isinstance(raw, str):
        return False
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return current - stamp <= timedelta(days=CACHE_TTL_DAYS)


def read_cache(local_id: str, key: str) -> dict | None:
    with connection() as db:
        row = db.execute(
            "SELECT payload FROM paper_relations WHERE source_local_id = ? AND cache_key = ?",
            (local_id, key),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not cache_fresh(data):
        return None
    return data


def write_cache(local_id: str, key: str, payload: dict) -> None:
    with connection() as db, db:
        db.execute("DELETE FROM paper_relations WHERE source_local_id = ?", (local_id,))
        db.execute(
            "INSERT INTO paper_relations (source_local_id, cache_key, payload, computed_at) VALUES (?, ?, ?, ?)",
            (local_id, key, json.dumps(payload, ensure_ascii=False), payload["recommendations"]["computed_at"]),
        )


async def load_seed(raw: str):
    target = await asyncio.to_thread(resolve_target, raw)
    if target.local_id:
        paper = await asyncio.to_thread(get_paper, target.local_id)
        if paper:
            return paper
    ref = target.paper_ref
    try:
        fetched = await fetch_paper(ref)
    except SourceFailure as exc:
        raise ResolveError(502, exc.message) from exc
    except httpx.TimeoutException:
        raise ResolveError(504, "论文详情请求超时") from None
    except httpx.HTTPError:
        raise ResolveError(502, "论文详情请求失败") from None
    if fetched is None:
        raise ResolveError(404, "没有找到这篇论文")
    try:
        saved, _ = await asyncio.to_thread(save_paper, fetched, ref)
    except (sqlite3.Error, OSError) as exc:
        raise ResolveError(503, "本地分析库暂不可用，请稍后重试") from exc
    return saved


def _warn(warnings: list[str], message: str) -> None:
    if message and message not in warnings:
        warnings.append(message)


async def _call(label: str, fetcher):
    try:
        return await fetcher(), None
    except SourceFailure as exc:
        log.warning("%s failed: %s", label, exc.message)
        return [], exc.message
    except httpx.TimeoutException:
        log.warning("%s timed out", label)
        return [], f"{label}超时"
    except httpx.HTTPError:
        log.warning("%s network error", label)
        return [], f"{label}失败"
    except Exception:
        log.exception("%s failed", label)
        return [], f"{label}失败"


async def recall_remote(seed: PaperOut, terms: list[str]) -> tuple[list[PaperOut], list[PaperOut], list[PaperOut], list[str], bool]:
    if not semantic_enabled():
        return [], [], [], ["Semantic Scholar 已停用，引用关系暂不可用。推荐只比较本地库中的论文。"], False
    warnings: list[str] = []
    query = recall_query(terms, seed.title)
    ref = s2_paper_ref(seed)
    citation_known = False
    if not ref:
        _warn(warnings, "当前论文缺少 arXiv、DOI 或 Semantic Scholar 标识，引用信息未知／暂不可用")
    found: list[PaperOut] = []
    outgoing: list[PaperOut] = []
    incoming: list[PaperOut] = []
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if query:
            found, message = await _call(
                "Semantic Scholar 检索",
                lambda: search.search_semantic_scholar(client, query, 0, SEARCH_LIMIT, None, None),
            )
            if message:
                _warn(warnings, "Semantic Scholar 检索暂不可用，外部候选可能不全")
            elif isinstance(found, tuple):
                found = found[0]
        if ref:
            outgoing, out_message = await _call(
                "参考文献",
                lambda: search.fetch_s2_links(client, ref, "references"),
            )
            incoming, in_message = await _call(
                "被引列表",
                lambda: search.fetch_s2_links(client, ref, "citations"),
            )
            citation_known = not out_message and not in_message
            if not citation_known:
                _warn(warnings, "引用信息未知／暂不可用，不会当成确定没有引用")
    return found[:SEARCH_LIMIT], outgoing[:LINK_LIMIT], incoming[:LINK_LIMIT], warnings, citation_known


def _edge(source: str, target: str, kind: str, label: str, directed: bool) -> dict:
    return {"source": source, "target": target, "type": kind, "label": label, "directed": directed}


def _public_item(item: dict) -> dict:
    paper = item["paper"]
    return {
        "id": canonical_id(paper),
        "title": paper.title,
        "year": paper.year,
        "authors": list(paper.authors),
        "venue": paper.venue,
        "abstract": paper.abstract,
        "url": paper.url or paper.pdf_url,
        "citation_count": paper.citation_count,
        "strength": item["strength"],
        "semantic_score": item["semantic_score"],
        "keyword_score": item["keyword_score"],
        "citation_score": item["citation_score"],
        "reasons": item["reasons"],
        "relations": item["relations"],
    }


def limit_candidates(groups: list[dict], limit: int = CANDIDATE_LIMIT) -> list[dict]:
    search, cited, local = [], [], []
    for group in groups:
        if group.get("directions"):
            cited.append(group)
        elif group.get("remote"):
            search.append(group)
        else:
            local.append(group)
    local.sort(key=lambda group: (0 if group.get("shared") else 1, group["paper"].title.casefold()))
    picked: list[dict] = []
    seen: set[int] = set()

    def take(bucket: list[dict], cap: int) -> None:
        added = 0
        for group in bucket:
            if len(picked) >= limit or added >= cap:
                return
            marker = id(group)
            if marker in seen:
                continue
            seen.add(marker)
            picked.append(group)
            added += 1

    take(search, SEARCH_LIMIT)
    take(cited, LINK_LIMIT * 2)
    take(local, LOCAL_LIMIT)
    return picked


def _cited(score) -> bool:
    return isinstance(score, (int, float)) and not isinstance(score, bool) and score >= 1


def _network(seed, center_id: str, scored: list[dict], warnings: list[str], computed_at: str) -> dict:
    selected = []
    seen = set()
    citation_items = [item for item in scored if _cited(item["citation_score"])]
    ranked = [item for item in scored if item["strength"] > 0]
    for item in citation_items + ranked:
        if not _cited(item["citation_score"]) and "similar" not in item["relations"]:
            continue
        paper_id = canonical_id(item["paper"])
        if paper_id == center_id or paper_id in seen:
            continue
        selected.append(item)
        seen.add(paper_id)
        if len(selected) >= 24:
            break
    nodes = [{"id": center_id, "title": seed.title, "year": seed.year, "center": True}]
    edges = []
    for item in selected:
        paper_id = canonical_id(item["paper"])
        nodes.append({"id": paper_id, "title": item["paper"].title, "year": item["paper"].year, "center": False})
        if "out" in item["directions"]:
            edges.append(_edge(center_id, paper_id, "cites", "引用", True))
        if "in" in item["directions"]:
            edges.append(_edge(paper_id, center_id, "cites", "引用", True))
        if "similar" in item["relations"]:
            edges.append(_edge(center_id, paper_id, "similar", "主题相似", False))
    return {
        "paper_id": center_id,
        "paper_title": seed.title,
        "algorithm_version": ALGORITHM,
        "warnings": warnings,
        "nodes": nodes,
        "edges": edges,
        "computed_at": computed_at,
    }


async def compute(seed, weights: dict[str, float]) -> dict:
    keywords = await asyncio.to_thread(latest_keywords, seed.local_id)
    terms = relation_score.seed_terms(keywords, seed.title, seed.abstract)
    local = await asyncio.to_thread(library.list_recent, 200)
    found, outgoing, incoming, warnings, citation_known = await recall_remote(seed, terms)
    seed_keys = identity_keys(seed) | {seed.id, seed.local_id}
    groups: list[dict] = []
    for paper in found:
        remember(groups, paper, None, seed_keys, True)
    for paper in outgoing:
        remember(groups, paper, "out", seed_keys, True)
    for paper in incoming:
        remember(groups, paper, "in", seed_keys, True)
    for paper in local:
        if getattr(paper, "local_id", None) == seed.local_id:
            continue
        remember(groups, paper, None, seed_keys, False)
    for group in groups:
        group["shared"] = relation_score.match_terms(terms, group["paper"].title, group["paper"].abstract)
        group["text"] = relation_score.embed_input(group["paper"].title, group["paper"].abstract)
    chosen = limit_candidates(groups)
    if len(chosen) < len(groups):
        _warn(warnings, f"候选超过 {CANDIDATE_LIMIT} 篇，已保留外部检索、引用和本地论文中的 {len(chosen)} 篇")
    seed_text = relation_score.embed_input(seed.title, seed.abstract)
    texts = [seed_text] if seed_text else []
    for group in chosen:
        if group["text"] and group["text"] not in texts:
            texts.append(group["text"])
    semantic_available = bool(seed_text)
    vectors: dict[str, list[float]] = {}
    if texts:
        try:
            vectors = await embeddings.embed_many(texts, persist=False)
        except embeddings.EmbeddingError as exc:
            log.warning("embedding unavailable: %s", exc.message)
            semantic_available = False
            _warn(warnings, "语义向量未知／暂不可用，不按相似度为 0 计算")
    seed_vector = vectors.get(seed_text) if semantic_available else None
    if semantic_available and seed_vector is None:
        semantic_available = False
        _warn(warnings, "语义向量未知／暂不可用，不按相似度为 0 计算")
    scored = []
    for group in chosen:
        vector = vectors.get(group["text"]) if semantic_available and group["text"] else None
        if not semantic_available or seed_vector is None or vector is None:
            semantic = None
        else:
            semantic = relation_score.cosine(seed_vector, vector)
        judged = relation_score.judge(
            semantic, group["shared"], len(terms), group["directions"], weights, citation_known,
        )
        scored.append({**judged, "paper": group["paper"]})
    vectors.clear()
    scored.sort(key=lambda item: (-item["strength"], -(item["paper"].year or 0), item["paper"].title.casefold()))
    recommended = [item for item in scored if item["strength"] > 0][:10]
    list_warnings = list(warnings)
    if len(recommended) < 5:
        list_warnings.append(f"候选论文不足 5 篇，已展示全部 {len(recommended)} 篇")
    computed_at = datetime.now(timezone.utc).isoformat()
    center_id = canonical_id(seed)
    recommendations = {
        "paper_id": center_id,
        "paper_title": seed.title,
        "algorithm_version": ALGORITHM,
        "weights": weights,
        "formula": relation_score.formula(weights, semantic_available, citation_known),
        "note": NOTE,
        "warnings": list_warnings,
        "computed_at": computed_at,
        "items": [_public_item(item) for item in recommended],
    }
    return {"recommendations": recommendations, "network": _network(seed, center_id, scored, warnings, computed_at)}


async def bundle(raw: str, refresh: bool) -> dict:
    seed = await load_seed(raw)
    weights = relation_score.load_weights()
    key = await asyncio.to_thread(cache_key, seed.local_id, weights, embeddings.model_name())
    async with lock_for(seed.local_id):
        if not refresh:
            cached = await asyncio.to_thread(read_cache, seed.local_id, key)
            if cached:
                return cached
        payload = await compute(seed, weights)
        await asyncio.to_thread(write_cache, seed.local_id, key, payload)
        return payload


async def recommendations(raw: str, limit: int, refresh: bool) -> dict:
    payload = await bundle(raw, refresh)
    data = dict(payload["recommendations"])
    data["items"] = list(data["items"])[:limit]
    return data


async def network(raw: str, refresh: bool) -> dict:
    payload = await bundle(raw, refresh)
    return payload["network"]

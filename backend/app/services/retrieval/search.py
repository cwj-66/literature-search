import asyncio
import logging
import os
import re
import time
import math
import sqlite3
from contextvars import ContextVar
from contextlib import suppress
from datetime import datetime, timezone
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
import ijson

from app.infrastructure import provider_cache
from app.schemas import PaperOut
from app.services.papers import ccf
from app.services.papers.paper_merge import combine, identity_keys

logger = logging.getLogger(__name__)

USER_AGENT = "literature-platform/0.1 (academic literature search)"
ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
S2_RELEVANCE_CAP = 1000
SHORT_RETRY_LIMIT = 30
SHORT_ATTEMPTS = 3
S2_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "year",
        "authors",
        "venue",
        "citationCount",
        "externalIds",
        "url",
        "openAccessPdf",
        "publicationDate",
        "journal",
    ]
)
SOURCE_LABELS = {"arxiv": "arXiv", "semantic_scholar": "Semantic Scholar"}


class SourceFailure(Exception):
    def __init__(self, message: str, retry_after: int = 0):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class SourceResult:
    def __init__(self, name: str, papers: list[PaperOut], total: int, error: str | None):
        self.name = name
        self.papers = papers
        self.total = total
        self.error = error
        self.warning = None
        self.cached_at = None
        self.retry_after = 0
        self.total_limited = False
        self.windowed = False


def clean(text: str | None) -> str | None:
    if not text:
        return None
    value = re.sub(r"\s+", " ", text).strip()
    return value or None


def local_children(element, name: str):
    for child in list(element):
        if child.tag.rsplit("}", 1)[-1] == name:
            yield child


def local_text(element, name: str) -> str | None:
    for child in local_children(element, name):
        if child.text and child.text.strip():
            return child.text
    return None


def s2_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def parse_arxiv_id(raw_id: str) -> str | None:
    match = re.search(r"abs/([^?#]+)", raw_id)
    if not match:
        return None
    return re.sub(r"v\d+$", "", match.group(1))


def map_arxiv_entry(entry) -> PaperOut | None:
    raw_id = local_text(entry, "id") or ""
    arxiv_id = parse_arxiv_id(raw_id)
    title = clean(local_text(entry, "title"))
    if not arxiv_id or not title:
        return None
    authors = []
    for author in local_children(entry, "author"):
        name = clean(local_text(author, "name"))
        if name:
            authors.append(name)
    published = local_text(entry, "published") or ""
    year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
    pdf_url = None
    url = f"https://arxiv.org/abs/{arxiv_id}"
    for link in local_children(entry, "link"):
        if link.attrib.get("title") == "pdf" and link.attrib.get("href"):
            pdf_url = link.attrib["href"]
    venue = clean(local_text(entry, "journal_ref"))
    if not venue:
        venue = "arXiv（预印本）"
    return PaperOut(
        id=f"arxiv:{arxiv_id}",
        title=title,
        authors=authors,
        abstract=clean(local_text(entry, "summary")),
        year=year,
        venue=venue,
        sources=["arxiv"],
        url=url,
        pdf_url=pdf_url or f"https://arxiv.org/pdf/{arxiv_id}",
        arxiv_id=arxiv_id,
        doi=clean(local_text(entry, "doi")),
    )


def map_s2_paper(payload: dict) -> PaperOut | None:
    title = clean(payload.get("title"))
    paper_id = payload.get("paperId")
    if not title or not paper_id:
        return None
    external = payload.get("externalIds") or {}
    arxiv_id = clean(external.get("ArXiv")) if isinstance(external, dict) else None
    doi = clean(external.get("DOI")) if isinstance(external, dict) else None
    authors, author_ids = [], []
    for author in payload.get("authors") or []:
        if not isinstance(author, dict):
            continue
        name = clean(author.get("name"))
        if not name:
            continue
        authors.append(name)
        author_ids.append(clean(author.get("authorId")))
    open_access = payload.get("openAccessPdf") or {}
    pdf_url = open_access.get("url") if isinstance(open_access, dict) else None
    venue = clean(payload.get("venue"))
    if not venue and isinstance(payload.get("journal"), dict):
        venue = clean(payload["journal"].get("name"))
    if arxiv_id and (not venue or venue.lower() in ("arxiv", "arxiv.org")):
        venue = "arXiv（预印本）"
    year = payload.get("year")
    if not isinstance(year, int):
        published = str(payload.get("publicationDate") or "")
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
    citation_count = payload.get("citationCount")
    if not isinstance(citation_count, int):
        citation_count = None
    return PaperOut(
        id=f"s2:{paper_id}",
        title=title,
        authors=authors,
        author_ids=author_ids,
        abstract=clean(payload.get("abstract")),
        year=year,
        venue=venue,
        sources=["semantic_scholar"],
        url=payload.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}",
        pdf_url=pdf_url,
        citation_count=citation_count,
        arxiv_id=arxiv_id,
        doi=doi,
        semantic_scholar_id=paper_id,
    )


def merge_papers(groups: list[list[PaperOut]], sort: str) -> list[PaperOut]:
    # Index identity keys instead of scanning every earlier paper. Large cursor
    # pages must not turn deduplication into a quadratic operation.
    buckets = {}
    by_key = {}
    for papers in groups:
        for rank, paper in enumerate(papers):
            keys = identity_keys(paper)
            matches = sorted({by_key[key] for key in keys if key in by_key})
            if matches:
                bucket_id = matches[0]
                old, old_rank, old_keys = buckets[bucket_id]
                for other_id in matches[1:]:
                    other, other_rank, other_keys = buckets.pop(other_id)
                    old = combine(old, other)
                    old_rank = min(old_rank, other_rank)
                    old_keys |= other_keys
                paper = combine(old, paper)
                rank = min(old_rank, rank)
                keys |= old_keys
            else:
                bucket_id = len(by_key)
            buckets[bucket_id] = (paper, rank, keys)
            for key in keys:
                by_key[key] = bucket_id
    merged = [(paper, rank) for paper, rank, _ in buckets.values()]
    if sort == "date":
        merged.sort(key=lambda item: item[0].year or 0, reverse=True)
    elif sort == "date_asc":
        merged.sort(key=lambda item: (item[0].year is None, item[0].year or 0))
    else:
        merged.sort(key=lambda item: item[1])
    return [paper for paper, _ in merged]


def parse_view_groups(query: str) -> list[list[str]] | None:
    """解析 SearchPlan 编译出的布尔视图查询 '("a" OR "b") AND ("c")' 为概念组列表。

    不是视图形态（含顶层括号分组）时返回 None，交给各源原样处理。
    """
    text = query.strip()
    if not (text.startswith("(") and ")" in text):
        return None
    groups = []
    for factor in re.split(r"\)\s+AND\s+\(", text):
        factor = factor.strip().strip("()").strip()
        terms = [term.strip().strip('"') for term in factor.split(" OR ")]
        terms = [term for term in terms if term]
        if not terms:
            return None
        groups.append(terms)
    return groups or None


def compile_query_dialect(query: str, source: str) -> str:
    """把布尔视图查询编译为指定数据源的检索语法；非视图查询原样返回。"""
    groups = parse_view_groups(query)
    if not groups:
        return query
    if source == "arxiv":
        return " AND ".join("(" + " OR ".join(f'all:"{term}"' for term in group) + ")" for group in groups)
    # Semantic Scholar 检索接口直接接受 (a OR b) AND c 布尔语法。
    return " AND ".join("(" + " OR ".join(f'"{term}"' for term in group) + ")" for group in groups)


def arxiv_search_query(query: str, year_from: int | None, year_to: int | None) -> str:
    # Treat UI input as literal keywords. Explicit AND/grouping prevents arXiv's
    # implicit term operators from escaping the submittedDate restriction.
    groups = parse_view_groups(query)
    if groups:
        term = compile_query_dialect(query, "arxiv")
    else:
        words = re.findall(r"[^\s\"]+", query)
        term = "(" + " AND ".join(f'all:"{word}"' for word in words) + ")"
    if year_from or year_to:
        start = f"{year_from or 1900}0101"
        end = f"{year_to or 2100}1231"
        term = f"{term} AND submittedDate:[{start}0000 TO {end}2359]"
    return term


QUERY_ALIASES = {
    "深度学习": "deep learning",
    "机器学习": "machine learning",
    "大语言模型": "large language model",
    "大模型幻觉": "large language model hallucination",
    "知识图谱": "knowledge graph",
    "检索增强生成": "retrieval augmented generation",
    "人工智能": "artificial intelligence",
}


def normalize_query(query: str) -> str:
    text = re.sub(r"\s+", " ", query.strip())
    return QUERY_ALIASES.get(text, QUERY_ALIASES.get(text.replace(" ", ""), text))


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def s2_year(year_from: int | None, year_to: int | None) -> str | None:
    if year_from and year_to:
        return f"{year_from}-{year_to}"
    if year_from:
        return f"{year_from}-"
    if year_to:
        return f"-{year_to}"
    return None


# Bounded process-local cache; deploy with one worker.
_CACHE: OrderedDict = OrderedDict()
_PAPERS: OrderedDict = OrderedDict()
_GATES: dict[str, asyncio.Lock] = {}
_NEXT: dict[str, float] = {}
_COOLDOWN: dict[str, float] = {}


def semantic_enabled() -> bool:
    return os.getenv("SEMANTIC_SCHOLAR_ENABLED", "true").lower() not in ("0", "false", "no")


def semantic_has_key() -> bool:
    return bool(os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip())


def quota_scope(label: str, headers: dict | None = None) -> str:
    """Keyed API quota must not inherit an anonymous/key-rotation cooldown."""
    import hashlib
    key = (headers or {}).get("x-api-key", "").strip()
    if label == "Semantic Scholar" and key:
        return f"{label}:key:{hashlib.sha256(key.encode()).hexdigest()[:16]}"
    return label


_CACHE_STATUS: ContextVar[dict | None] = ContextVar("provider_cache_status", default=None)
_PROGRESS: ContextVar[object] = ContextVar("search_progress", default=None)


async def cache_operation(function, *args, default=None):
    try:
        return await asyncio.to_thread(function, *args)
    except (sqlite3.Error, OSError):
        logger.warning("Persistent provider cache unavailable")
        return default


def retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After", "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        try:
            return parsedate_to_datetime(raw).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None


def record_cache(response: httpx.Response, stale: bool = False, retry_after: int = 0):
    state = _CACHE_STATUS.get()
    if state is not None:
        stamp = response.extensions.get("paper_cached_at")
        if stamp:
            state["cached_at"] = min(stamp, state.get("cached_at", stamp))
        state["stale"] = state.get("stale", False) or stale
        state["retry_after"] = max(retry_after, state.get("retry_after", 0))
    return response


async def request(client: httpx.AsyncClient, url: str, params: dict, headers: dict | None, label: str, allow_not_found: bool = False, on_items=None, accept=None) -> httpx.Response:
    cache_key = (url, tuple(sorted(params.items())))
    disk_key = provider_cache.key(url, params)
    scope = quota_scope(label, headers)
    async with _GATES.setdefault(label, asyncio.Lock()):
        if label == "Semantic Scholar" and not semantic_enabled():
            raise SourceFailure("Semantic Scholar 已由管理员停用，请使用 arXiv")
        cached = _CACHE.get(cache_key)
        if cached and cached[0] > time.monotonic():
            _CACHE.move_to_end(cache_key)
            return record_cache(cached[1])
        stored = await cache_operation(provider_cache.get, disk_key)
        stale = None
        if stored:
            saved_at, content_type, body = stored
            stale = httpx.Response(200, content=body, headers={"content-type": content_type})
            stale.extensions["paper_cached_at"] = saved_at
            if time.time() - saved_at < provider_cache.FRESH_SECONDS:
                _CACHE[cache_key] = (time.monotonic() + min(600, provider_cache.FRESH_SECONDS - (time.time() - saved_at)), stale)
                while len(_CACHE) > 32 or sum(len(value[1].content) for value in _CACHE.values()) > 16 * 1024 * 1024:
                    _CACHE.popitem(last=False)
                return record_cache(stale)
        disk_deadline = await cache_operation(provider_cache.retry_at, scope, default=0)
        remaining = max(_COOLDOWN.get(scope, 0) - time.monotonic(), disk_deadline - time.time())
        if remaining > 0:
            retry = math.ceil(remaining)
            if stale is not None:
                return record_cache(stale, True, retry)
            raise SourceFailure(f"{label} 上游限流，约 {retry} 秒后可重试", retry)
        await asyncio.sleep(max(0, _NEXT.get(label, 0) - time.monotonic()))
        interval = {"Semantic Scholar": 1.1, "arXiv": 3.1, "OpenAlex": 0.5}.get(label, 3.1)
        attempt = 0
        while True:
            attempt += 1
            try:
                if on_items is None:
                    response = await client.get(url, params=params, headers=headers)
                else:
                    # Parse complete paper objects as bytes arrive, without waiting
                    # for the upstream bulk response (up to 1000 papers) to finish.
                    async with client.stream("GET", url, params=params, headers=headers) as upstream:
                        body = bytearray()
                        items = ijson.sendable_list()
                        parser = ijson.items_coro(items, "data.item", use_float=True)
                        try:
                            async for chunk in upstream.aiter_bytes():
                                body.extend(chunk)
                                if upstream.status_code == 200:
                                    parser.send(chunk)
                                    if items:
                                        await on_items(list(items))
                                        del items[:]
                            if upstream.status_code == 200:
                                parser.close()
                        finally:
                            with suppress(Exception):
                                parser.close()
                        response = httpx.Response(upstream.status_code, headers={
                            key: value for key, value in upstream.headers.items()
                            if key.lower() not in ("content-encoding", "content-length")
                        }, content=bytes(body))
            except httpx.HTTPError:
                if stale is not None:
                    return record_cache(stale, True)
                raise
            finally:
                # Space requests from completion, including long/failed responses.
                _NEXT[label] = time.monotonic() + interval
            if response.status_code != 429:
                break
            header_delay = retry_after_seconds(response)
            short = header_delay is None or (math.isfinite(header_delay) and header_delay <= SHORT_RETRY_LIMIT)
            if not short or attempt >= SHORT_ATTEMPTS:
                break
            # Retries share the provider quota too, even with Retry-After: 0.
            wait = max(interval, header_delay if header_delay is not None else float(2 ** (attempt - 1)))
            logger.warning("%s upstream HTTP 429; retrying in %.0fs", label, max(0, wait))
            await asyncio.sleep(max(0, wait))
        if response.status_code == 429:
            header_delay = retry_after_seconds(response)
            strike = await cache_operation(provider_cache.bump_strike, scope, default=1) or 1
            if header_delay is None or not math.isfinite(header_delay):
                base = 60 if label == "arXiv" else 120
                header_delay = base * (2 ** (strike - 1))
            delay = max(1, min(header_delay, 900))
            retry = math.ceil(delay)
            _COOLDOWN[scope] = time.monotonic() + delay
            await cache_operation(provider_cache.set_retry_at, scope, time.time() + delay)
            logger.warning("%s upstream HTTP 429; retry_after=%s; server=%s", label, retry, response.headers.get("server", "unknown"))
            if stale is not None:
                return record_cache(stale, True, retry)
            raise SourceFailure(f"{label} 上游返回 429（限流），约 {retry} 秒后可重试", retry)
        if response.status_code == 404 and allow_not_found:
            return response
        if response.status_code >= 400:
            if response.status_code >= 500 and stale is not None:
                return record_cache(stale, True)
            raise SourceFailure(f"{label} 返回错误（{response.status_code}）")
        # Only persist parseable provider payloads, never HTML error shells.
        try:
            if accept is not None:
                if not accept(response):
                    raise ValueError("Rejected payload")
            elif label == "arXiv":
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.content)
                if root.tag.rsplit("}", 1)[-1] != "feed":
                    raise ValueError("Not an Atom feed")
            else:
                payload = response.json()
                if not isinstance(payload, dict) or not any(key in payload for key in ("data", "paperId", "results")):
                    raise ValueError("Not a paper response")
        except Exception as exc:
            # Parser-specific failures are mapped by adapters; do not cache them.
            logger.warning("%s response skipped by cache validation: %s", label, type(exc).__name__)
            return record_cache(stale, True) if stale is not None else response
        if response.status_code == 200:
            await cache_operation(provider_cache.clear_strikes, scope)
        response.extensions["paper_cached_at"] = time.time()
        await cache_operation(provider_cache.put, disk_key, response.headers.get("content-type", ""), response.content)
        _CACHE[cache_key] = (time.monotonic() + 600, response)
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > 32 or sum(len(value[1].content) for value in _CACHE.values()) > 16 * 1024 * 1024:
            _CACHE.popitem(last=False)
        return response


async def search_arxiv(client: httpx.AsyncClient, query: str, offset: int, limit: int, sort: str, year_from: int | None, year_to: int | None) -> tuple[list[PaperOut], int]:
    params = {
        "search_query": arxiv_search_query(query, year_from, year_to),
        "start": offset,
        "max_results": limit,
        "sortBy": "submittedDate" if sort == "date" else "relevance",
        "sortOrder": "descending",
    }
    response = await request(client, ARXIV_API, params, {"User-Agent": USER_AGENT}, "arXiv")
    body = response.text.lstrip()
    if not body.startswith("<"):
        raise SourceFailure("arXiv 返回了无法解析的内容")
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise SourceFailure("arXiv 返回了无法解析的内容") from exc
    if root.tag.rsplit("}", 1)[-1] != "feed":
        raise SourceFailure("arXiv 返回了无法解析的内容")
    total_text = local_text(root, "totalResults") or "0"
    total = int(total_text) if total_text.isdigit() else 0
    papers = [paper for entry in local_children(root, "entry") if (paper := map_arxiv_entry(entry))]
    return papers, total


def rank_candidates(papers: list, query: str) -> list:
    """按查询相关性对候选降序排序：词频得分 + 查询词覆盖率，分数相同保持原有顺序。"""
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        return list(papers)
    folded = [word.casefold() for word in words]

    def score(paper) -> float:
        title = (paper.get("title") or "") if isinstance(paper, dict) else (getattr(paper, "title", None) or "")
        abstract = (paper.get("abstract") or "") if isinstance(paper, dict) else (getattr(paper, "abstract", None) or "")
        title, text = title.casefold(), (title + " " + abstract).casefold()
        frequency = sum(3 * title.count(word) + text.count(word) for word in folded)
        coverage = sum(1 for word in set(folded) if word in text)
        return frequency + 2 * coverage

    return sorted(papers, key=score, reverse=True)


def keyword_score(paper, words) -> int:
    """标题/摘要与检索词的关键词得分；兼容 PaperOut 与 dict 条目。"""
    title = paper.get("title") if isinstance(paper, dict) else paper.title
    abstract = paper.get("abstract") if isinstance(paper, dict) else paper.abstract
    title = (title or "").casefold()
    text = title + " " + (abstract or "").casefold()
    return sum(3 * title.count(word.casefold()) + text.count(word.casefold()) for word in words)


MIN_RELEVANT_SCORE = 1


def oldest_relevant(papers, words, target: int) -> list:
    """date_asc：先丢弃与检索词零重叠的明显无关候选，再按年份升序取最早 target 条；无年份排最后。"""
    relevant = [paper for paper in papers if keyword_score(paper, words) >= MIN_RELEVANT_SCORE]
    pool = relevant if relevant else list(papers)
    pool.sort(key=lambda paper: ((paper.get("year") if isinstance(paper, dict) else paper.year) is None,
                                 (paper.get("year") if isinstance(paper, dict) else paper.year) or 0))
    return pool[:target]


def rank_bulk(papers, words, sort):
    if sort == "relevance":
        return sorted(papers, key=lambda paper: keyword_score(paper, words), reverse=True)
    return papers


async def search_s2_bulk(client, query: str, year_from: int | None, year_to: int | None, sort: str, target: int = 20):
    """Follow provider tokens on demand. Cached batches are reused across pages.

    Relevance is local within each upstream batch; completed batches keep their
    positions when subsequent batches arrive. No global relevance is implied.
    """
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        raise SourceFailure("请输入有效的检索关键词")
    view = compile_query_dialect(query, "s2") if parse_view_groups(query) else None
    # date_asc 先多取几页，回到调用侧做相关性过滤后再取最早
    cap = min(target * 5, 200) if sort == "date_asc" else target
    params = {"query": view or " + ".join(words), "fields": S2_FIELDS,
              "sort": "publicationDate:asc" if sort == "date_asc"
              else ("publicationDate:desc" if sort == "date" else "citationCount:desc")}
    year = s2_year(year_from, year_to)
    if year:
        params["year"] = year
    state = _CACHE_STATUS.get()
    if state is not None and sort not in ("date", "date_asc"):
        state["note"] = "相关性为每批候选的关键词排序，后续结果按需继续获取"
    papers = []
    seen = set()
    tokens = set()
    total = 0
    while True:
        batch = []
        last_sent = 0
        async def received(items):
            nonlocal last_sent
            for item in items:
                paper = map_s2_paper(item)
                if paper and year_matches(paper, year_from, year_to):
                    batch.append(paper)
            publish = _PROGRESS.get()
            if publish and len(papers) + len(batch) >= target and (not last_sent or time.monotonic() - last_sent > .1):
                preview = deduplicate(papers + rank_bulk(batch, words, sort))
                await publish(SourceResult("semantic_scholar", preview, max(total, len(preview)), None))
                last_sent = time.monotonic()
        response = await request(client, f"{S2_API}/search/bulk", params, s2_headers(), "Semantic Scholar", on_items=received if _PROGRESS.get() else None)
        payload = response.json()
        if not isinstance(payload.get("data"), list):
            raise SourceFailure("Semantic Scholar 批量检索返回了无法解析的内容")
        batch = [paper for item in payload["data"] if (paper := map_s2_paper(item)) and year_matches(paper, year_from, year_to)]
        for paper in rank_bulk(batch, words, sort):
            keys = identity_keys(paper)
            if not keys & seen:
                papers.append(paper)
                seen.update(keys)
        estimate = payload.get("total")
        total = max(len(papers), estimate if isinstance(estimate, int) else 0)
        token = payload.get("token")
        if not token:
            return papers, len(papers)
        if len(papers) >= cap:
            return papers, total
        if token in tokens:
            raise SourceFailure("Semantic Scholar 返回了重复的分页游标，请稍后重试")
        tokens.add(token)
        params["token"] = token


def deduplicate(papers):
    seen = set()
    result = []
    for paper in papers:
        keys = identity_keys(paper)
        if not keys & seen:
            seen.update(keys)
            result.append(paper)
    return result


async def search_s2_relevance(client, query: str, offset: int, limit: int, year_from: int | None, year_to: int | None) -> tuple[list[PaperOut], int]:
    """One page of Semantic Scholar relevance search. The API serves at most 1000 hits."""
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        raise SourceFailure("请输入有效的检索关键词")
    state = _CACHE_STATUS.get()
    if offset >= S2_RELEVANCE_CAP:
        if state is not None:
            state["note"] = "相关性结果最多到第 1000 条，更靠后的页仍显示 arXiv"
            state["total_limited"] = True
            state["windowed"] = True
        return [], 0
    limit = max(1, min(limit, 100, S2_RELEVANCE_CAP - offset))
    params = {"query": compile_query_dialect(query, "s2"), "offset": offset, "limit": limit, "fields": S2_FIELDS}
    year = s2_year(year_from, year_to)
    if year:
        params["year"] = year
    response = await request(client, f"{S2_API}/search", params, s2_headers(), "Semantic Scholar")
    payload = response.json()
    if not isinstance(payload.get("data"), list):
        raise SourceFailure("Semantic Scholar 检索返回了无法解析的内容")
    papers = [paper for item in payload["data"] if (paper := map_s2_paper(item)) and year_matches(paper, year_from, year_to)]
    estimate = payload.get("total")
    total = estimate if isinstance(estimate, int) else len(papers)
    if state is not None:
        state["windowed"] = True
        if total > S2_RELEVANCE_CAP:
            state["total_limited"] = True
    return papers, total


async def search_semantic_scholar(client: httpx.AsyncClient, query: str, offset: int, limit: int, year_from: int | None, year_to: int | None, sort: str = "relevance") -> tuple[list[PaperOut], int]:
    if sort == "date_asc":
        papers, total = await search_s2_bulk(client, query, year_from, year_to, sort, offset + limit)
        words = re.findall(r"[\w]+", query, flags=re.UNICODE)
        return oldest_relevant(papers, words, offset + limit)[offset:offset + limit], total
    if sort == "date":
        papers, total = await search_s2_bulk(client, query, year_from, year_to, sort, offset + limit)
        return papers[offset:offset + limit], total
    return await search_s2_relevance(client, query, offset, limit, year_from, year_to)


async def fetch_s2_paper(client: httpx.AsyncClient, paper_ref: str) -> PaperOut | None:
    response = await request(
        client,
        f"{S2_API}/{quote(paper_ref, safe='')}",
        {"fields": S2_FIELDS},
        s2_headers(),
        "Semantic Scholar",
        allow_not_found=True,
    )
    if response.status_code == 404:
        return None
    payload = response.json()
    if payload.get("paperId"):
        return map_s2_paper(payload)
    return None


def papers_from_links(payload: dict, nested: str) -> list[PaperOut]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SourceFailure("Semantic Scholar 引用数据无法解析")
    papers = []
    for item in rows:
        raw = item.get(nested) if isinstance(item, dict) else None
        if isinstance(raw, dict) and (paper := map_s2_paper(raw)):
            papers.append(paper)
        if len(papers) >= 20:
            break
    return papers


async def fetch_s2_links(client: httpx.AsyncClient, paper_ref: str, kind: str) -> list[PaperOut]:
    nested = "citedPaper" if kind == "references" else "citingPaper"
    fields = ",".join(f"{nested}.{name}" for name in S2_FIELDS.split(","))
    response = await request(
        client,
        f"{S2_API}/{quote(paper_ref, safe='')}/{kind}",
        {"fields": fields, "limit": 20},
        s2_headers(),
        "Semantic Scholar",
        allow_not_found=True,
    )
    if response.status_code == 404:
        return []
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceFailure("Semantic Scholar 引用数据无法解析") from exc
    return papers_from_links(payload, nested)


async def fetch_arxiv_paper(client: httpx.AsyncClient, arxiv_id: str) -> PaperOut | None:
    response = await request(client, ARXIV_API, {"id_list": arxiv_id}, {"User-Agent": USER_AGENT}, "arXiv")
    import xml.etree.ElementTree as ET

    root = ET.fromstring(response.text)
    entry = next(local_children(root, "entry"), None)
    if entry is None or local_text(entry, "title") == "Error":
        return None
    return map_arxiv_entry(entry)


async def run_source(name: str, fetcher) -> SourceResult:
    label = SOURCE_LABELS[name]
    state = {}
    token = _CACHE_STATUS.set(state)
    try:
        papers, total = await fetcher()
        result = SourceResult(name, papers, total, None)
        result.warning = state.get("note")
        result.total_limited = bool(state.get("total_limited"))
        result.windowed = bool(state.get("windowed"))
        result.retry_after = state.get("retry_after", 0)
        if state.get("stale"):
            result.cached_at = datetime.fromtimestamp(state["cached_at"], timezone.utc).isoformat()
            result.warning = f"{label} 上游暂不可用，正在展示历史缓存（非最新结果）" + ("；" + state["note"] if state.get("note") else "")
            result.retry_after = state.get("retry_after", 0)
        return result
    except SourceFailure as exc:
        logger.warning("%s search failed: %s", name, exc.message)
        result = SourceResult(name, [], 0, exc.message)
        result.retry_after = exc.retry_after
        return result
    except httpx.TimeoutException:
        logger.warning("%s search timed out", name)
        return SourceResult(name, [], 0, f"{label} 请求超时")
    except httpx.HTTPError:
        logger.warning("%s search network error", name)
        return SourceResult(name, [], 0, f"{label} 网络请求失败")
    except Exception:
        logger.exception("%s search failed", name)
        return SourceResult(name, [], 0, f"{label} 查询失败")
    finally:
        _CACHE_STATUS.reset(token)


def year_matches(paper: PaperOut, year_from: int | None, year_to: int | None) -> bool:
    if year_from is None and year_to is None:
        return True
    return paper.year is not None and (year_from is None or paper.year >= year_from) and (year_to is None or paper.year <= year_to)


async def unique_prefix(fetcher, target: int, maximum: int, year_from: int | None = None, year_to: int | None = None) -> tuple[list[PaperOut], int]:
    """Fetch enough unique eligible records for the requested page."""
    papers: list[PaperOut] = []
    raw_offset = 0
    total = 0
    while len(papers) < target and raw_offset < maximum:
        size = min(100, target - len(papers), maximum - raw_offset)
        batch, total = await fetcher(raw_offset, size)
        eligible = [paper for paper in batch if year_matches(paper, year_from, year_to)]
        papers = merge_papers([papers + eligible], "relevance")
        raw_offset += size
        if raw_offset >= total or not batch:
            break
    return papers, total


OPENALEX_API = "https://api.openalex.org/works"
ARXIV_SOURCE = "S4306400194"


def abstract_from_inverted(inverted) -> str | None:
    if not isinstance(inverted, dict):
        return None
    pairs = []
    for word, positions in inverted.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos < 5000:
                pairs.append((pos, word))
    if not pairs:
        return None
    words = [""] * (max(pos for pos, _ in pairs) + 1)
    for pos, word in pairs:
        words[pos] = word
    return clean(" ".join(word for word in words if word))


def arxiv_id_from_url(raw: str | None) -> str | None:
    if not raw:
        return None
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+)", raw, re.I)
    if not match:
        return None
    value = match.group(1).strip("/")
    if value.lower().endswith(".pdf"):
        value = value[:-4]
    return re.sub(r"v\d+$", "", value) or None


def map_openalex_work(item: dict) -> PaperOut | None:
    title = clean(item.get("display_name"))
    location = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
    access = item.get("open_access") if isinstance(item.get("open_access"), dict) else {}
    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    arxiv_id = arxiv_id_from_url(location.get("landing_page_url")) or arxiv_id_from_url(location.get("pdf_url"))
    arxiv_id = arxiv_id or arxiv_id_from_url(access.get("oa_url")) or arxiv_id_from_url(ids.get("arxiv"))
    if not title or not arxiv_id:
        return None
    authors, author_ids = [], []
    for row in item.get("authorships") or []:
        author = row.get("author") if isinstance(row, dict) else None
        name = clean(author.get("display_name")) if isinstance(author, dict) else None
        if not name or not isinstance(author, dict):
            continue
        authors.append(name)
        raw_id = clean(author.get("id"))
        author_ids.append(raw_id.rstrip("/").rsplit("/", 1)[-1] if raw_id else None)
    doi = clean(item.get("doi"))
    if doi and doi.lower().startswith("https://doi.org/"):
        doi = doi[16:]
    if doi and "arxiv" in doi.lower():
        doi = None
    year = item.get("publication_year")
    citations = item.get("cited_by_count")
    return PaperOut(
        id=f"arxiv:{arxiv_id}",
        title=title,
        authors=authors,
        author_ids=author_ids,
        abstract=abstract_from_inverted(item.get("abstract_inverted_index")),
        year=year if isinstance(year, int) else None,
        venue="arXiv（预印本）",
        sources=["openalex"],
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        citation_count=citations if isinstance(citations, int) else None,
        arxiv_id=arxiv_id,
        doi=doi,
    )


async def search_openalex_arxiv(client, query, year_from, year_to, limit):
    filters = [f"primary_location.source.id:{ARXIV_SOURCE}"]
    if year_from or year_to:
        filters.append(f"publication_year:{year_from or 1900}-{year_to or 2100}")
    params = {
        "search": query,
        "filter": ",".join(filters),
        "per_page": max(1, min(limit, 25)),
        "select": "display_name,authorships,publication_year,ids,doi,cited_by_count,primary_location,open_access,abstract_inverted_index",
    }
    mailto = os.getenv("OPENALEX_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    response = await request(client, OPENALEX_API, params, {"User-Agent": USER_AGENT}, "OpenAlex")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SourceFailure("OpenAlex 返回了无法解析的内容") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SourceFailure("OpenAlex 返回了无法解析的内容")
    return [paper for item in results if isinstance(item, dict) and (paper := map_openalex_work(item)) and year_matches(paper, year_from, year_to)]


def remember_arxiv_substitute(papers, via, failure):
    state = _CACHE_STATUS.get()
    if state is not None:
        state["note"] = f"arXiv API 暂不可用，以下来自{via}；总数仅为本批匹配记录，相关性为本地排序"
        state["total_limited"] = True
        state["retry_after"] = getattr(failure, "retry_after", 0) or 0
    return papers, len(papers)


async def arxiv_substitutes(client, query, sort, year_from, year_to, limit):
    if semantic_enabled():
        try:
            indexed, _ = await search_s2_bulk(client, query, year_from, year_to, sort, max(limit, 20))
            papers = [paper for paper in indexed if paper.arxiv_id]
            if papers:
                return papers, "Semantic Scholar 收录的 arXiv 论文索引"
        except (SourceFailure, httpx.HTTPError):
            pass
    try:
        papers = await search_openalex_arxiv(client, query, year_from, year_to, max(limit, 10))
        if papers:
            return papers, "OpenAlex 收录的 arXiv 论文"
    except (SourceFailure, httpx.HTTPError):
        pass
    return [], None


async def arxiv_with_index_fallback(client, query, target, sort, year_from, year_to):
    failure = None
    if sort == "date_asc":
        # 先按相关性取回一个窗口，过滤明显无关候选后再按年份升序取最早
        words = re.findall(r"[\w]+", query, flags=re.UNICODE)
        if not contains_cjk(query):
            try:
                window = min(target * 5, 200)
                papers, total = await unique_prefix(lambda start, size: search_arxiv(client, query, start, size, "relevance", year_from, year_to), window, 10000, year_from, year_to)
                return oldest_relevant(papers, words, target), total
            except (SourceFailure, httpx.HTTPError) as exc:
                failure = exc
        papers, via = await arxiv_substitutes(client, query, sort, year_from, year_to, target)
        if papers:
            return remember_arxiv_substitute(oldest_relevant(papers, words, target), via, failure)
        if failure is not None:
            raise failure
        state = _CACHE_STATUS.get()
        if state is not None:
            state["note"] = "arXiv 只索引英文题录，中文检索未向 arXiv 发送请求"
        return [], 0
    if not contains_cjk(query):
        try:
            return await unique_prefix(lambda start, size: search_arxiv(client, query, start, size, sort, year_from, year_to), target, 10000, year_from, year_to)
        except (SourceFailure, httpx.HTTPError) as exc:
            failure = exc
    papers, via = await arxiv_substitutes(client, query, sort, year_from, year_to, target)
    if papers:
        return remember_arxiv_substitute(papers, via, failure)
    if failure is not None:
        raise failure
    state = _CACHE_STATUS.get()
    if state is not None:
        state["note"] = "arXiv 只索引英文题录，中文检索未向 arXiv 发送请求"
    return [], 0


async def search_papers(query: str, page: int, page_size: int, sort: str, source: str, year_from: int | None, year_to: int | None) -> dict:
    snapshot = None
    async for snapshot in search_papers_stream(query, page, page_size, sort, source, year_from, year_to):
        pass
    return snapshot


async def search_papers_stream(query: str, page: int, page_size: int, sort: str, source: str, year_from: int | None, year_to: int | None):
    query = normalize_query(query)
    offset = (page - 1) * page_size
    target = offset + page_size
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        jobs = []
        pending = []
        updates = asyncio.Queue(maxsize=8)
        async def progress(result):
            await updates.put((result, False))
        async def worker(name, fetcher):
            token = _PROGRESS.set(progress if name == "semantic_scholar" else None)
            try:
                await updates.put((await run_source(name, fetcher), True))
            finally:
                _PROGRESS.reset(token)
        if source in ("all", "arxiv"):
            pending.append("arxiv")
            jobs.append(asyncio.create_task(worker("arxiv", lambda: arxiv_with_index_fallback(client, query, target, sort, year_from, year_to))))
        if source == "semantic_scholar" or (source == "all" and semantic_enabled()):
            pending.append("semantic_scholar")
            if sort in ("date", "date_asc"):
                s2_call = lambda: search_semantic_scholar(client, query, 0, target, year_from, year_to, sort)
            else:
                s2_call = lambda: search_semantic_scholar(client, query, offset, page_size, year_from, year_to, "relevance")
            jobs.append(asyncio.create_task(worker("semantic_scholar", s2_call)))
        results = {}
        try:
            yield search_snapshot(query, page, page_size, sort, year_from, year_to, list(results.values()), pending)
            while pending:
                result, complete = await updates.get()
                previous = results.get(result.name)
                if result.error and previous:
                    result.papers = previous.papers
                    result.total = previous.total
                results[result.name] = result
                if complete:
                    pending.remove(result.name)
                yield search_snapshot(query, page, page_size, sort, year_from, year_to, list(results.values()), pending)
        finally:
            for job in jobs:
                if not job.done():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)


def search_snapshot(query, page, page_size, sort, year_from, year_to, results, pending):
    offset = (page - 1) * page_size
    # Completion order must not change final ranking or metadata precedence.
    results = sorted(results, key=lambda item: 0 if item.name == "arxiv" else 1)
    if any(item.papers for item in results):
        for item in results:
            if item.name == "arxiv" and item.error and item.retry_after and not item.papers:
                item.warning = item.warning or "arXiv 正在限流，本次结果来自其他来源，请稍后再试"
                item.error = None
    def eligible(group):
        return [paper for paper in group if year_matches(paper, year_from, year_to)]

    prefixes = [eligible(item.papers) for item in results if item.papers and not item.windowed]
    windows = [eligible(item.papers) for item in results if item.papers and item.windowed]
    papers = merge_papers(prefixes, sort)[offset:offset + page_size] if prefixes else []
    if windows:
        papers = merge_papers([papers, *windows], sort)[:page_size]
    papers = [ccf.annotate(paper) for paper in papers]
    # Keep merged metadata identical when navigating from a card to its detail.
    for paper in papers:
        _PAPERS[paper.id] = (time.monotonic() + 600, paper)
        _PAPERS.move_to_end(paper.id)
    while len(_PAPERS) > 1000:
        _PAPERS.popitem(last=False)
    totals = [item.total for item in results if not item.error or item.papers]
    return {
        "query": query,
        "page": page,
        "page_size": page_size,
        "total": max(totals) if totals else 0,
        "total_estimated": len(totals) > 1 or bool(year_from or year_to) or any(item.warning for item in results),
        "papers": papers,
        "source_counts": {item.name: item.total for item in results if item.papers or (not item.error and not item.warning)},
        "source_errors": {item.name: item.error for item in results if item.error},
        "source_warnings": {item.name: item.warning for item in results if item.warning},
        "source_cached_at": {item.name: item.cached_at for item in results if item.cached_at},
        "source_retry_after": {item.name: item.retry_after for item in results if item.retry_after},
        "pending_sources": list(pending),
        "total_is_limited": bool(totals) and all(item.total_limited for item in results if not item.error),
    }


async def fetch_paper(paper_id: str) -> PaperOut | None:
    cached = _PAPERS.get(paper_id)
    if cached and cached[0] > time.monotonic():
        return ccf.annotate(cached[1])
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if paper_id.startswith("arxiv:"):
            paper = await fetch_arxiv_paper(client, paper_id.removeprefix("arxiv:"))
        elif paper_id.startswith("s2:"):
            paper = await fetch_s2_paper(client, paper_id.removeprefix("s2:"))
        elif paper_id.startswith("doi:"):
            paper = await fetch_s2_paper(client, f"DOI:{paper_id.removeprefix('doi:')}")
        else:
            paper = None
    return ccf.annotate(paper) if paper else None

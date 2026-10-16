import asyncio
import logging
import os
import re
import time
import math
import sqlite3
from contextvars import ContextVar
from datetime import datetime, timezone
from collections import OrderedDict
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from .schemas import PaperOut
from . import provider_cache

logger = logging.getLogger(__name__)

USER_AGENT = "literature-platform/0.1"
ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper"
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


def paper_identity(arxiv_id: str | None, doi: str | None, s2_id: str | None) -> str:
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    if doi:
        return f"doi:{doi}"
    return f"s2:{s2_id}"


def prefer_count(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def longer_text(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return left if len(left) >= len(right) else right


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
    authors = [
        name
        for author in (payload.get("authors") or [])
        if isinstance(author, dict) and (name := clean(author.get("name")))
    ]
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


def identity_keys(paper: PaperOut) -> set[str]:
    keys = set()
    if paper.doi:
        keys.add("doi:" + paper.doi.lower())
    if paper.arxiv_id:
        keys.add("arxiv:" + paper.arxiv_id.lower())
    if paper.semantic_scholar_id:
        keys.add("s2:" + paper.semantic_scholar_id)
    if not keys:
        title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", paper.title.lower())
        keys.add(f"title:{title}:{paper.year or ''}")
    return keys


def prefer_venue(left: PaperOut, right: PaperOut) -> str | None:
    for paper in (left, right):
        if "semantic_scholar" in paper.sources and paper.venue:
            return paper.venue
    return left.venue or right.venue


def combine(left: PaperOut, right: PaperOut) -> PaperOut:
    arxiv_id = left.arxiv_id or right.arxiv_id
    doi = left.doi or right.doi
    s2_id = left.semantic_scholar_id or right.semantic_scholar_id
    authors = left.authors if len(left.authors) >= len(right.authors) else right.authors
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (left.url or right.url)
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else (left.pdf_url or right.pdf_url)
    return PaperOut(
        id=paper_identity(arxiv_id, doi, s2_id),
        title=left.title or right.title,
        authors=authors,
        abstract=longer_text(left.abstract, right.abstract),
        year=left.year or right.year,
        venue=prefer_venue(left, right),
        sources=list(dict.fromkeys([*left.sources, *right.sources])),
        url=url,
        pdf_url=pdf_url,
        citation_count=prefer_count(left.citation_count, right.citation_count),
        arxiv_id=arxiv_id,
        doi=doi,
        semantic_scholar_id=s2_id,
    )


def merge_papers(groups: list[list[PaperOut]], sort: str) -> list[PaperOut]:
    buckets: list[list[tuple[PaperOut, int]]] = []
    for papers in groups:
        for rank, paper in enumerate(papers):
            keys = identity_keys(paper)
            bucket = next((item for item in buckets if any(keys & identity_keys(old) for old, _ in item)), None)
            if bucket is None:
                buckets.append([(paper, rank)])
            else:
                bucket.append((paper, rank))
    merged: list[tuple[PaperOut, int]] = []
    for bucket in buckets:
        paper, rank = bucket[0]
        for other, other_rank in bucket[1:]:
            paper = combine(paper, other)
            rank = min(rank, other_rank)
        merged.append((paper, rank))
    if sort == "date":
        merged.sort(key=lambda item: item[0].year or 0, reverse=True)
    else:
        merged.sort(key=lambda item: item[1])
    return [paper for paper, _ in merged]


def arxiv_search_query(query: str, year_from: int | None, year_to: int | None) -> str:
    # Treat UI input as literal keywords. Explicit AND/grouping prevents arXiv's
    # implicit term operators from escaping the submittedDate restriction.
    words = re.findall(r"[^\s\"]+", query)
    term = "(" + " AND ".join(f'all:"{word}"' for word in words) + ")"
    if year_from or year_to:
        start = f"{year_from or 1900}0101"
        end = f"{year_to or 2100}1231"
        term = f"{term} AND submittedDate:[{start}0000 TO {end}2359]"
    return term


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


_CACHE_STATUS: ContextVar[dict | None] = ContextVar("provider_cache_status", default=None)


async def cache_operation(function, *args, default=None):
    try:
        return await asyncio.to_thread(function, *args)
    except (sqlite3.Error, OSError):
        logger.warning("Persistent provider cache unavailable")
        return default


def record_cache(response: httpx.Response, stale: bool = False, retry_after: int = 0):
    state = _CACHE_STATUS.get()
    if state is not None:
        stamp = response.extensions.get("paper_cached_at")
        if stamp:
            state["cached_at"] = min(stamp, state.get("cached_at", stamp))
        state["stale"] = state.get("stale", False) or stale
        state["retry_after"] = max(retry_after, state.get("retry_after", 0))
    return response


async def request(client: httpx.AsyncClient, url: str, params: dict, headers: dict | None, label: str, allow_not_found: bool = False) -> httpx.Response:
    cache_key = (url, tuple(sorted(params.items())))
    disk_key = provider_cache.key(url, params)
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
        disk_deadline = await cache_operation(provider_cache.retry_at, label, default=0)
        remaining = max(_COOLDOWN.get(label, 0) - time.monotonic(), disk_deadline - time.time())
        if remaining > 0:
            retry = math.ceil(remaining)
            if stale is not None:
                return record_cache(stale, True, retry)
            raise SourceFailure(f"{label} 上游限流，约 {retry} 秒后可重试", retry)
        await asyncio.sleep(max(0, _NEXT.get(label, 0) - time.monotonic()))
        interval = 1.1 if label == "Semantic Scholar" else 3.1
        try:
            response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError:
            if stale is not None:
                return record_cache(stale, True)
            raise
        finally:
            # Space requests from completion, including long/failed responses.
            _NEXT[label] = time.monotonic() + interval
        if response.status_code == 429:
            retry = response.headers.get("Retry-After", "")
            try:
                delay = float(retry)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(retry).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    delay = 60 if label == "arXiv" else 120
            delay = max(1, min(delay, 3600)) if math.isfinite(delay) else 120
            retry = math.ceil(delay)
            _COOLDOWN[label] = time.monotonic() + delay
            await cache_operation(provider_cache.set_retry_at, label, time.time() + delay)
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
            if label == "arXiv":
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.content)
                if root.tag.rsplit("}", 1)[-1] != "feed":
                    raise ValueError("Not an Atom feed")
            else:
                payload = response.json()
                if not isinstance(payload, dict) or not any(key in payload for key in ("data", "paperId")):
                    raise ValueError("Not a paper response")
        except Exception as exc:
            # Parser-specific failures are mapped by adapters; do not cache them.
            logger.warning("%s response skipped by cache validation: %s", label, type(exc).__name__)
            return record_cache(stale, True) if stale is not None else response
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


async def search_s2_bulk(client, query: str, year_from: int | None, year_to: int | None, sort: str):
    # Public bulk search has different ordering semantics from relevance search.
    # Match literal keywords and rank this bounded candidate batch locally.
    words = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not words:
        raise SourceFailure("请输入有效的检索关键词")
    params = {"query": " + ".join(words), "fields": S2_FIELDS,
              "sort": "publicationDate:desc" if sort == "date" else "citationCount:desc"}
    year = s2_year(year_from, year_to)
    if year:
        params["year"] = year
    response = await request(client, f"{S2_API}/search/bulk", params, s2_headers(), "Semantic Scholar")
    payload = response.json()
    if not isinstance(payload.get("data"), list):
        raise SourceFailure("Semantic Scholar 批量检索返回了无法解析的内容")
    papers = [paper for item in payload["data"] if (paper := map_s2_paper(item)) and year_matches(paper, year_from, year_to)]
    if sort == "relevance":
        def score(paper):
            title = paper.title.casefold()
            text = title + " " + (paper.abstract or "").casefold()
            return sum(3 * title.count(word.casefold()) + text.count(word.casefold()) for word in words)
        papers.sort(key=score, reverse=True)
    state = _CACHE_STATUS.get()
    if state is not None:
        state["note"] = "Semantic Scholar 使用公开批量检索；相关性按本批候选论文的关键词匹配计算，最多展示 1000 篇"
    return papers, len(papers)


async def search_semantic_scholar(client: httpx.AsyncClient, query: str, offset: int, limit: int, year_from: int | None, year_to: int | None, sort: str = "relevance") -> tuple[list[PaperOut], int]:
    if not semantic_has_key():
        papers, total = await search_s2_bulk(client, query, year_from, year_to, sort)
        return papers[offset:offset + limit], total
    if offset + limit > 1000:
        raise SourceFailure("Semantic Scholar 只提供前 1000 条结果，请缩小范围")
    params: dict[str, str | int] = {"query": re.sub(r"[-–—]+", " ", query), "offset": offset, "limit": limit, "fields": S2_FIELDS}
    year = s2_year(year_from, year_to)
    if year:
        params["year"] = year
    response = await request(client, f"{S2_API}/search", params, s2_headers(), "Semantic Scholar")
    payload = response.json()
    if "data" not in payload:
        message = payload.get("message") or payload.get("error") or "查询失败"
        if not isinstance(message, str):
            message = "查询失败"
        raise SourceFailure(f"Semantic Scholar {message}")
    papers = [paper for item in payload.get("data") or [] if (paper := map_s2_paper(item))]
    total = payload.get("total")
    return papers, total if isinstance(total, int) else len(papers)


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
    """Fetch fixed cached batches until enough unique records exist for slicing.

    S2 accepts at most 100 records per request and only its first 1000 hits.
    Fixed batch boundaries reuse the provider cache while users change pages.
    """
    papers: list[PaperOut] = []
    raw_offset = 0
    total = 0
    while len(papers) < target and raw_offset < maximum:
        size = min(100, maximum - raw_offset)
        batch, total = await fetcher(raw_offset, size)
        eligible = [paper for paper in batch if year_matches(paper, year_from, year_to)]
        papers = merge_papers([papers + eligible], "relevance")
        raw_offset += size
        if raw_offset >= total or not batch:
            break
    return papers, total


async def arxiv_with_index_fallback(client, query, target, sort, year_from, year_to):
    try:
        return await unique_prefix(lambda start, size: search_arxiv(client, query, start, size, sort, year_from, year_to), target, 10000, year_from, year_to)
    except (SourceFailure, httpx.HTTPError) as exc:
        if not semantic_enabled():
            raise
        try:
            indexed, _ = await search_s2_bulk(client, query, year_from, year_to, sort)
        except (SourceFailure, httpx.HTTPError):
            raise exc
        papers = [paper for paper in indexed if paper.arxiv_id]
        if not papers:
            raise exc
        state = _CACHE_STATUS.get()
        if state is not None:
            state["note"] = "arXiv API 暂不可用，以下来自 Semantic Scholar 收录的 arXiv 论文索引；总数仅为本批匹配记录，相关性为本地排序"
            state["retry_after"] = getattr(exc, "retry_after", 0)
        return papers, len(papers)


async def search_papers(query: str, page: int, page_size: int, sort: str, source: str, year_from: int | None, year_to: int | None) -> dict:
    offset = (page - 1) * page_size
    target = offset + page_size
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        jobs = []
        if source in ("all", "arxiv"):
            jobs.append(run_source("arxiv", lambda: arxiv_with_index_fallback(client, query, target, sort, year_from, year_to)))
        if source == "semantic_scholar" or (source == "all" and semantic_enabled()):
            jobs.append(run_source("semantic_scholar", lambda: unique_prefix(
                lambda start, size: search_semantic_scholar(client, query, start, size, year_from, year_to, sort), target, 1000, year_from, year_to)))
        results = await asyncio.gather(*jobs)
    papers = merge_papers([item.papers for item in results if not item.error], sort)
    papers = [paper for paper in papers if year_matches(paper, year_from, year_to)]
    # Keep merged metadata identical when navigating from a card to its detail.
    for paper in papers:
        _PAPERS[paper.id] = (time.monotonic() + 600, paper)
        _PAPERS.move_to_end(paper.id)
    while len(_PAPERS) > 1000:
        _PAPERS.popitem(last=False)
    totals = [item.total for item in results if not item.error]
    return {
        "query": query,
        "page": page,
        "page_size": page_size,
        "total": max(totals) if totals else 0,
        "total_estimated": len(totals) > 1 or bool(year_from or year_to) or any(item.warning for item in results),
        "papers": papers[offset:offset + page_size],
        "source_counts": {item.name: item.total for item in results if not item.error},
        "source_errors": {item.name: item.error for item in results if item.error},
        "source_warnings": {item.name: item.warning for item in results if item.warning},
        "source_cached_at": {item.name: item.cached_at for item in results if item.cached_at},
        "source_retry_after": {item.name: item.retry_after for item in results if item.retry_after},
    }


async def fetch_paper(paper_id: str) -> PaperOut | None:
    cached = _PAPERS.get(paper_id)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if paper_id.startswith("arxiv:"):
            paper = await fetch_arxiv_paper(client, paper_id.removeprefix("arxiv:"))
            if paper is None:
                return None
            return paper
        if paper_id.startswith("s2:"):
            return await fetch_s2_paper(client, paper_id.removeprefix("s2:"))
        if paper_id.startswith("doi:"):
            return await fetch_s2_paper(client, f"DOI:{paper_id.removeprefix('doi:')}")
    return None

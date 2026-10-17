"""Fetch a publicly available paper and choose sections for analysis."""

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, NavigableString
from pypdf import PdfReader

from app.schemas import PaperOut
from app.services.papers import documents

log = logging.getLogger(__name__)
USER_AGENT = "literature-platform/0.1"
LIMIT = 8_000_000
BUDGET = 24000
ALLOW = {
    "arxiv.org", "www.arxiv.org", "export.arxiv.org",
    "openreview.net", "www.openreview.net",
    "aclanthology.org", "www.aclanthology.org",
    "proceedings.mlr.press",
    "www.ncbi.nlm.nih.gov",
    "europepmc.org", "www.europepmc.org",
}
HEADING = re.compile(
    r"(?im)^\s*(?:\d+(?:\.\d+){0,3}[\.\s]+)?"
    r"(?:abstract|introduction|related work|background|experimental results|experiments?|"
    r"evaluation|results|methods?|methodology|approaches?|proposed method|"
    r"limitations?|discussions?|conclusions?|future work|references|bibliography|"
    r"摘要|引言|相关工作|背景|方法|模型|实验结果|实验|结果|讨论|局限性|结论|未来工作|参考文献)\s*$"
)
CLASSIFIERS = (
    ("method", re.compile(r"method|approach|model|方法|模型", re.I)),
    ("experiment", re.compile(r"experiment|evaluation|result|实验|评估|结果", re.I)),
    ("limitation", re.compile(r"limitation|discussion|conclusion|future|局限|讨论|结论|未来", re.I)),
    ("intro", re.compile(r"abstract|introduction|引言|摘要|简介|背景", re.I)),
)
CAPS = {"method": 8000, "experiment": 8000, "limitation": 5000, "intro": 3000}
BLOCKED = ("access denied", "subscribe to read", "purchase this article", "sign in to continue", "log in to continue")


class TextFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class PreparedText:
    text: str
    basis: str
    source_kind: str
    source_url: str | None
    degradation_reason: str | None
    evidence: dict


def host_allowed(host: str | None) -> bool:
    return bool(host) and host.lower().rstrip(".") in ALLOW


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return None
    if parsed.port and parsed.port not in (80, 443):
        return None
    if not host_allowed(parsed.hostname):
        return None
    return urlunparse(("https", parsed.hostname.lower(), parsed.path or "", "", parsed.query, ""))


def assert_public(host: str) -> None:
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise TextFailure("全文下载失败") from exc
    if not records:
        raise TextFailure("全文下载失败")
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise TextFailure("全文下载失败")


def readable_count(text: str) -> int:
    return sum(1 for char in text if char.isalnum())


def quality_problem(text: str, pages: list[str] | None, pdf: bool) -> str | None:
    reason = "PDF 正文过短或无法解析" if pdf else "返回内容不是可解析的论文全文"
    compact = re.sub(r"\s+", "", text)
    if not compact or readable_count(compact) / len(compact) < 0.6 or readable_count(text) < 2500:
        return reason
    if any(phrase in text[:800].lower() for phrase in BLOCKED):
        return reason
    if pdf and pages is not None and len(pages) > 3:
        rich = [index for index, page in enumerate(pages) if readable_count(page) >= 200]
        if rich == [0] or not rich:
            return reason
    return None


def classify(title: str) -> str:
    for name, pattern in CLASSIFIERS:
        if pattern.search(title):
            return name
    return "other"


def windows(text: str) -> list[tuple[str, str]]:
    if len(text) <= BUDGET:
        return [("正文", text)] if text else []
    head, mid = int(BUDGET * 0.3), int(BUDGET * 0.4)
    tail = BUDGET - head - mid
    middle = max(0, (len(text) - mid) // 2)
    return [("开头", text[:head]), ("中部", text[middle:middle + mid]), ("结尾", text[-tail:])]


def select_sections(sections: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], bool]:
    original = sum(len(body) for _, body in sections)
    known = [item for item in sections if classify(item[0]) != "other"]
    if len(known) < 2:
        text = "\n".join(body for _, body in sections).strip()
        chosen = windows(text)
        return chosen, sum(len(body) for _, body in chosen) < len(text)
    grouped: dict[str, list] = {name: [] for name in ("method", "experiment", "limitation", "intro", "other")}
    for index, (title, body) in enumerate(sections):
        grouped[classify(title)].append((index, title, body))
    room = {**CAPS, "other": BUDGET}
    used = 0
    picked = []
    for name in ("method", "experiment", "limitation", "intro", "other"):
        for index, title, body in grouped[name]:
            take = min(room[name], BUDGET - used)
            if take <= 0 or not body:
                continue
            piece = body[:take]
            picked.append((index, title, piece))
            room[name] -= len(piece)
            used += len(piece)
    picked.sort()
    return [(title, body) for _, title, body in picked], used < original


def split_by_headings(text: str) -> list[tuple[str, str]]:
    matches = list(HEADING.finditer(text))
    if len(matches) < 2:
        return [("正文", text)]
    sections = []
    if matches[0].start() > 200:
        sections.append(("开头", text[:matches[0].start()].strip()))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            sections.append((match.group(0).strip(), body))
    return sections or [("正文", text)]


def parse_html(data: bytes) -> list[tuple[str, str]]:
    soup = BeautifulSoup(data.decode("utf-8", "replace"), "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "button"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body
    if root is None:
        return []
    names = ["h2"] if len(root.find_all("h2")) >= 2 else ["h3"]
    headings = root.find_all(names)
    if len(headings) < 2:
        text = root.get_text("\n", strip=True)
        return [("正文", text)] if text else []
    stop = set(names) | {"h1", "h2"}
    sections = []
    prelude = []
    for node in root.descendants:
        if node is headings[0]:
            break
        if isinstance(node, NavigableString) and str(node).strip():
            prelude.append(str(node).strip())
    if prelude:
        sections.append(("摘要及前置文本", "\n".join(prelude)))
    for heading in headings:
        chunks = []
        for node in heading.next_elements:
            if any(parent is heading for parent in node.parents):
                continue
            if getattr(node, "name", None) in stop:
                break
            if isinstance(node, NavigableString):
                piece = str(node).strip()
                if piece:
                    chunks.append(piece)
        body = "\n".join(chunks).strip()
        title = heading.get_text(" ", strip=True)
        if title and body:
            sections.append((title, body))
    return sections


def pack(sections: list[tuple[str, str]], url: str, pdf: bool, pretruncated: bool) -> PreparedText:
    # Keep bounded source material for staged extraction, rather than silently
    # keeping only the beginning of a long methods/experiments section.
    sections = [(title, body) for title, body in sections if not re.fullmatch(
        r"\s*(?:\d+[.\s]*)?(?:references|bibliography|参考文献)\s*", title, re.I)]
    original = sum(len(body) for _, body in sections)
    if original <= 96000:
        chosen, truncated = sections, False
    else:
        chosen, truncated = select_sections(sections)
    text = "\n\n".join(f"## {title}\n{body}" for title, body in chosen).strip()
    if not text:
        raise TextFailure("PDF 正文过短或无法解析" if pdf else "返回内容不是可解析的论文全文")
    host = (urlparse(url).hostname or "").lower()
    kind = "oa_pdf" if pdf else "arxiv_html" if host.endswith("arxiv.org") else "oa_pdf"
    evidence = {
        "sections": [{"title": title, "chars": len(body), "excerpt": body[:240]} for title, body in chosen],
        "char_count": len(text),
        "truncated": truncated or pretruncated,
    }
    return PreparedText(text, "full_text", kind, url, None, evidence)


def extract_pages(data: bytes) -> tuple[list[str], bool]:
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        total = len(reader.pages)
        pages = [(page.extract_text() or "") for index, page in enumerate(reader.pages) if index < 40]
    except Exception as exc:
        raise TextFailure("PDF 正文过短或无法解析") from exc
    return pages, total > 40


def from_pdf(data: bytes, url: str) -> PreparedText:
    pages, truncated = extract_pages(data)
    problem = quality_problem("\n".join(pages), pages, True)
    if problem:
        raise TextFailure(problem)
    return pack(split_by_headings("\n".join(pages)), url, True, truncated)


def from_html(data: bytes, url: str) -> PreparedText:
    sections = parse_html(data)
    problem = quality_problem("\n".join(body for _, body in sections), None, False)
    if problem:
        raise TextFailure(problem)
    return pack(sections, url, False, False)


async def download(url: str, limit: int = LIMIT) -> bytes:
    timeout = httpx.Timeout(20.0, connect=10.0)
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for _ in range(5):
            parsed = urlparse(current)
            if parsed.scheme == "http" and host_allowed(parsed.hostname) and parsed.port in (None, 80, 443):
                current = urlunparse(parsed._replace(scheme="https"))
                continue
            if parsed.scheme != "https" or not host_allowed(parsed.hostname) or (parsed.port and parsed.port not in (80, 443)):
                raise TextFailure("全文链接不在可公开获取范围内")
            if parsed.username or parsed.password:
                raise TextFailure("全文链接不在可公开获取范围内")
            await asyncio.to_thread(assert_public, parsed.hostname or "")
            try:
                async with client.stream("GET", current, headers={"User-Agent": USER_AGENT}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise TextFailure("全文下载失败")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise TextFailure("全文下载失败")
                    chunks, total = [], 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise TextFailure("全文文件过大，已跳过")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except TextFailure:
                raise
            except httpx.TimeoutException as exc:
                raise TextFailure("下载超时") from exc
            except httpx.HTTPError as exc:
                raise TextFailure("全文下载失败") from exc
    raise TextFailure("全文下载失败")


def _jobs(paper: PaperOut) -> list[str]:
    jobs = []
    if paper.arxiv_id:
        safe = quote(paper.arxiv_id, safe="/")
        jobs.extend((f"https://arxiv.org/html/{safe}", f"https://arxiv.org/pdf/{safe}"))
    pdf = normalize_url(paper.pdf_url)
    if pdf and all(pdf.rstrip("/").removesuffix(".pdf") != item.rstrip("/").removesuffix(".pdf") for item in jobs):
        jobs.append(pdf)
    return jobs


async def load(url: str) -> PreparedText:
    data = await download(url)
    if data.startswith(b"%PDF"):
        return await asyncio.to_thread(from_pdf, data, url)
    lowered = data[:2000].lower()
    if b"<html" in lowered or b"<article" in lowered or b"<!doctype html" in lowered:
        return await asyncio.to_thread(from_html, data, url)
    raise TextFailure("返回内容不是可解析的论文全文")


def abstract_text(paper: PaperOut, reason: str) -> PreparedText | None:
    abstract = (paper.abstract or "").strip()
    if not abstract:
        return None
    truncated = len(abstract) > BUDGET
    if truncated:
        abstract = abstract[:BUDGET]
    return PreparedText(
        f"## 摘要\n{abstract}", "abstract", "abstract", None, reason,
        {"sections": [{"title": "摘要", "chars": len(abstract), "excerpt": abstract[:240]}], "char_count": len(abstract), "truncated": truncated},
    )


def stored_text(paper: PaperOut) -> PreparedText | None:
    text = documents.get_text(getattr(paper, "local_id", None), paper.id)
    if not text:
        return None
    return PreparedText(
        text, "full_text", "upload", None, None,
        {"sections": [{"title": "正文", "chars": len(text), "excerpt": text[:240]}], "char_count": len(text), "truncated": False},
    )


async def prepare(paper: PaperOut) -> PreparedText | None:
    stored = stored_text(paper)
    if stored:
        return stored
    jobs = _jobs(paper)
    failure = None
    if not jobs and paper.pdf_url:
        failure = "全文链接不在可公开获取范围内"
    elif not jobs:
        failure = "没有可公开获取的全文链接"
    for url in jobs:
        try:
            return await load(url)
        except TextFailure as exc:
            failure = exc.reason
        except Exception:
            log.exception("full text preparation failed")
            failure = "返回内容不是可解析的论文全文"
    return abstract_text(paper, failure or "没有可公开获取的全文链接")

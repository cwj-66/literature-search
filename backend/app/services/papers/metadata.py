"""Crossref and Unpaywall lookups through the shared provider cache."""

import os
from urllib.parse import quote

import httpx

from app.schemas import PaperOut
from app.services.retrieval import search

CROSSREF = "https://api.crossref.org/works"
UNPAYWALL = "https://api.unpaywall.org/v2"


def contact_email() -> str:
    return (os.getenv("CROSSREF_MAILTO") or os.getenv("UNPAYWALL_EMAIL") or "").strip()


def _headers() -> dict[str, str]:
    headers = {"User-Agent": search.USER_AGENT}
    email = contact_email()
    if email:
        headers["User-Agent"] = f"{search.USER_AGENT} (mailto:{email})"
    key = os.getenv("CROSSREF_API_KEY", "").strip()
    if key:
        headers["Crossref-Plus-API-Token"] = f"Bearer {key}"
    return headers


def _crossref_ok(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and "message" in payload and payload.get("status") != "failed"


def _unpaywall_ok(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and ("doi" in payload or "is_oa" in payload)


def _bibtex_ok(response: httpx.Response) -> bool:
    return "@" in (response.text or "")


def _first(values):
    if isinstance(values, list) and values:
        return values[0] if isinstance(values[0], str) else None
    return values if isinstance(values, str) else None


async def crossref_work(client: httpx.AsyncClient, doi: str) -> dict | None:
    params = {}
    email = contact_email()
    if email:
        params["mailto"] = email
    try:
        response = await search.request(
            client, f"{CROSSREF}/{quote(doi, safe='')}", params, _headers(), "Crossref",
            allow_not_found=True, accept=_crossref_ok,
        )
    except search.SourceFailure:
        return None
    if response.status_code == 404:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    message = payload.get("message") if isinstance(payload, dict) else None
    return message if isinstance(message, dict) else None


async def crossref_bibtex(client: httpx.AsyncClient, doi: str) -> str | None:
    params = {}
    email = contact_email()
    if email:
        params["mailto"] = email
    try:
        response = await search.request(
            client, f"{CROSSREF}/{quote(doi, safe='')}/transform/application/x-bibtex",
            params, _headers(), "Crossref", allow_not_found=True, accept=_bibtex_ok,
        )
    except search.SourceFailure:
        return None
    if response.status_code == 404 or not _bibtex_ok(response):
        return None
    return response.text.strip()


async def unpaywall(client: httpx.AsyncClient, doi: str) -> dict | None:
    email = contact_email()
    if not email:
        return None
    try:
        response = await search.request(
            client, f"{UNPAYWALL}/{quote(doi, safe='')}", {"email": email}, _headers(),
            "Unpaywall", allow_not_found=True, accept=_unpaywall_ok,
        )
    except search.SourceFailure:
        return None
    if response.status_code == 404:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def apply_crossref(paper: PaperOut, message: dict) -> PaperOut:
    authors = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = " ".join(part for part in (author.get("given"), author.get("family")) if part).strip()
        if name:
            authors.append(name)
    year = None
    parts = ((message.get("published") or {}).get("date-parts") or [[]])
    if parts and parts[0] and isinstance(parts[0][0], int):
        year = parts[0][0]
    journal = _first(message.get("container-title"))
    doi = (message.get("DOI") or paper.doi or "").lower() or paper.doi
    citation = message.get("is-referenced-by-count")
    if not isinstance(citation, int):
        citation = paper.citation_count
    return paper.model_copy(update={
        "title": paper.title or _first(message.get("title")) or paper.title,
        "authors": authors or paper.authors,
        "year": paper.year or year,
        "venue": journal or paper.venue,
        "doi": doi,
        "url": paper.url or message.get("URL"),
        "citation_count": citation if paper.citation_count is None else paper.citation_count,
    })


def apply_unpaywall(paper: PaperOut, payload: dict) -> PaperOut:
    location = payload.get("best_oa_location") or {}
    pdf = location.get("url_for_pdf") if isinstance(location, dict) else None
    landing = location.get("url") if isinstance(location, dict) else None
    return paper.model_copy(update={
        "pdf_url": paper.pdf_url or (pdf if isinstance(pdf, str) else None),
        "url": paper.url or (landing if isinstance(landing, str) else None),
    })


def bibtex_from_paper(paper: PaperOut) -> str:
    author = " and ".join(paper.authors) if paper.authors else "Unknown"
    year = paper.year or "n.d."
    key = (paper.authors[0].split()[-1] if paper.authors else "anon") + str(year)
    key = "".join(ch for ch in key if ch.isalnum())
    fields = [
        ("title", paper.title),
        ("author", author),
        ("year", str(year)),
        ("journal", paper.venue),
        ("doi", paper.doi),
        ("url", paper.url),
    ]
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
    return f"@article{{{key},\n{body}\n}}"


async def enrich(paper: PaperOut) -> tuple[PaperOut, dict]:
    extras = {"bibtex": None, "open_access": None, "warnings": []}
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
        if paper.doi:
            work = await crossref_work(client, paper.doi)
            if work:
                paper = apply_crossref(paper, work)
            else:
                extras["warnings"].append("Crossref 未返回该 DOI")
            extras["bibtex"] = await crossref_bibtex(client, paper.doi) if paper.doi else None
            oa = await unpaywall(client, paper.doi)
            if oa:
                extras["open_access"] = {
                    "is_oa": bool(oa.get("is_oa")),
                    "oa_status": oa.get("oa_status"),
                }
                paper = apply_unpaywall(paper, oa)
            elif not contact_email():
                extras["warnings"].append("未配置 CROSSREF_MAILTO，已跳过 Unpaywall")
        else:
            extras["warnings"].append("没有 DOI，无法查询 Crossref 或 Unpaywall")
    if not extras["bibtex"]:
        extras["bibtex"] = bibtex_from_paper(paper)
    from app.services.papers import ccf
    return ccf.annotate(paper), extras

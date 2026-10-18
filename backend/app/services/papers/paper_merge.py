"""Merge duplicate paper records by DOI, arXiv ID, or Semantic Scholar ID."""

import re

from app.schemas import PaperOut


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


def identity_keys(paper: PaperOut) -> set[str]:
    if paper.id.startswith("upload:"):
        return {paper.id}
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


def _author_bundle(paper: PaperOut) -> tuple[list[str], list[str | None]]:
    names, ids = [], []
    raw = list(paper.author_ids)
    for index, name in enumerate(paper.authors):
        if not name or not name.strip():
            continue
        author_id = raw[index] if index < len(raw) else None
        names.append(name.strip())
        ids.append(author_id.strip() if isinstance(author_id, str) and author_id.strip() else None)
    return names, ids


def _prefer_authors(left: PaperOut, right: PaperOut) -> tuple[list[str], list[str | None]]:
    bundles = [_author_bundle(left), _author_bundle(right)]
    bundles.sort(key=lambda item: (sum(bool(author_id) for author_id in item[1]), len(item[0])), reverse=True)
    return bundles[0]


def prefer_venue(left: PaperOut, right: PaperOut) -> str | None:
    for paper in (left, right):
        if "semantic_scholar" in paper.sources and paper.venue:
            return paper.venue
    return left.venue or right.venue


def combine(left: PaperOut, right: PaperOut) -> PaperOut:
    arxiv_id = left.arxiv_id or right.arxiv_id
    doi = left.doi or right.doi
    s2_id = left.semantic_scholar_id or right.semantic_scholar_id
    authors, author_ids = _prefer_authors(left, right)
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else (left.url or right.url)
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else (left.pdf_url or right.pdf_url)
    if arxiv_id or doi or s2_id:
        paper_id = paper_identity(arxiv_id, doi, s2_id)
    elif left.id.startswith("upload:"):
        paper_id = left.id
    else:
        paper_id = right.id if right.id.startswith("upload:") else paper_identity(arxiv_id, doi, s2_id)
    return PaperOut(
        id=paper_id,
        title=left.title or right.title,
        authors=authors,
        author_ids=author_ids,
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
        ccf_rank=left.ccf_rank or right.ccf_rank,
        cas_partition=left.cas_partition or right.cas_partition,
        ccf_status=left.ccf_status or right.ccf_status,
        ccf_catalog_version=left.ccf_catalog_version or right.ccf_catalog_version,
    )

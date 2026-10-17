"""Match venues to a versioned CCFDDL conference alias catalog."""

import json
import re
from functools import lru_cache
from pathlib import Path

from .schemas import PaperOut

CATALOG_PATH = Path(__file__).resolve().parent / "ccf_catalog.json"
PREPRINT = re.compile(r"arxiv|preprint|\bcorr\b", re.I)
TRACK = re.compile(r"workshop|workshops|\bws\b|findings|short paper|\bdemo\b|poster|companion", re.I)
TOKEN = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.I)


def normalize(text: str) -> str:
    text = text.replace("&", " and ").casefold()
    return " ".join(TOKEN.findall(text))


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def info() -> dict:
    data = catalog()
    return {
        "source": data.get("source"),
        "license": data.get("license"),
        "note": data.get("note"),
        "imported_at": data.get("imported_at"),
        "catalog_version": data.get("catalog_version"),
        "venue_count": len(data.get("venues") or []),
    }


@lru_cache(maxsize=1)
def index() -> dict[str, dict]:
    mapping = {}
    for venue in catalog().get("venues") or []:
        rank = str(venue.get("rank") or "")
        if rank not in {"A", "B", "C"}:
            continue
        for alias in venue.get("aliases") or []:
            key = normalize(alias)
            if key and key not in mapping:
                mapping[key] = venue
        abbr = normalize(venue.get("abbr") or "")
        if abbr:
            mapping.setdefault(abbr, venue)
    return mapping


def lookup(venue: str | None) -> tuple[str | None, str, str | None]:
    version = catalog().get("catalog_version")
    if not venue or not venue.strip():
        return None, "unmatched", version
    raw = venue.strip()
    if PREPRINT.search(raw) and not index().get(normalize(raw)):
        return None, "preprint", version
    if TRACK.search(raw):
        return None, "pending", version
    key = normalize(raw)
    hit = index().get(key)
    if hit:
        return hit["rank"], "verified", version
    tokens = set(TOKEN.findall(key))
    for alias, venue_row in index().items():
        abbr = normalize(venue_row.get("abbr") or "")
        if abbr and abbr in tokens and len(abbr) >= 3:
            return venue_row["rank"], "verified", version
        name = normalize(venue_row.get("name") or "")
        if name and (name in key or key in name) and len(name) >= 12:
            return venue_row["rank"], "verified", version
        if alias and len(alias) >= 8 and alias in key:
            return venue_row["rank"], "verified", version
    return None, "unmatched", version


def annotate(paper: PaperOut) -> PaperOut:
    rank, status, version = lookup(paper.venue)
    return paper.model_copy(update={
        "ccf_rank": rank,
        "ccf_status": status,
        "ccf_catalog_version": version,
    })

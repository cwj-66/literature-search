"""Assemble selected papers and saved recommendations without external calls."""

import asyncio
import logging
import re
from datetime import datetime, timezone

from app.services.papers import library
from app.services.papers.paper_merge import identity_keys
from app.services.retrieval import recommend, relation_precompute

logger = logging.getLogger(__name__)

SATELLITES_PER_CATEGORY = 1
CATEGORIES = ("citation", "semantic", "keyword")
SCORE_FIELDS = {"citation": "citation_score", "semantic": "semantic_score", "keyword": "keyword_score"}
SOURCE_NAME = re.compile(r"semantic[_ ]scholar|arxiv|openalex|crossref", re.IGNORECASE)


def _warn(warnings: list[str], message: str) -> None:
    if message and message not in warnings:
        warnings.append(message)


def _center_edges(center_id: str, center_ids: set[str], satellites: list[dict]) -> list[dict]:
    edges = []
    seen = set()
    for group in satellites:
        if group["center_id"] != center_id:
            continue
        for item in group["items"]:
            other = item["id"]
            if other == center_id or other not in center_ids:
                continue
            key = (center_id, other, group["category"])
            if key in seen:
                continue
            seen.add(key)
            category = group["category"]
            directions = {e.get("type") for e in item.get("evidence", [])}
            if category == "citation" and directions & {"in", "out"}:
                for direction in ("out", "in"):
                    if direction not in directions:
                        continue
                    source, target = (center_id, other) if direction == "out" else (other, center_id)
                    edges.append({"source": source, "target": target, "type": "cites", "label": "引用",
                                  "directed": True, "category": category, "strength": item.get("citation_score")})
            else:
                kind, label = {"citation": ("citation", "引用关联"),
                               "semantic": ("similar", "主题相似"),
                               "keyword": ("keyword", "关键词关联")}[category]
                edges.append({"source": center_id, "target": other, "type": kind, "label": label,
                              "directed": False, "category": category,
                              "strength": item.get(SCORE_FIELDS[category])})
    return edges


async def relate(ids: list[str], refresh: bool = False) -> dict:
    warnings: list[str] = []
    centers = []
    satellites = []
    pending_ids = []
    precompute = []
    for raw in ids:
        try:
            seed = await asyncio.to_thread(library.get_paper, raw)
            if seed is None:
                _warn(warnings, f"论文 {raw} 尚未入库")
                continue
        except Exception as exc:
            _warn(warnings, f"论文 {raw} 关系计算失败：{exc}")
            continue
        await asyncio.to_thread(relation_precompute.ensure_job, seed.local_id, refresh)
        snapshot = await asyncio.to_thread(relation_precompute.read, seed.local_id)
        preparing = snapshot["status"] in {"queued", "running"}
        if preparing:
            pending_ids.append(seed.local_id)
        precompute.append({"paper_id": seed.local_id, "status": snapshot["status"], "computed_at": snapshot["computed_at"]})
        payload = snapshot["payload"] or {"recommendations": {"groups": [{"key": key,
            "status": "pending" if preparing else "unavailable", "matched_count": 0, "items": []}
            for key in CATEGORIES], "warnings": []}}
        seed_id = recommend.canonical_id(seed)
        if any(center["id"] == seed_id for center in centers):
            continue
        index = len(centers)
        for warning in payload["recommendations"].get("warnings", []):
            if SOURCE_NAME.search(warning):
                logger.info("relate candidate warning for %s: %s", raw, warning)
            else:
                _warn(warnings, warning)
        centers.append({"id": seed_id, "aliases": sorted(identity_keys(seed) | {seed.id, raw}),
                        "local_id": getattr(seed, "local_id", None) or raw,
                        "title": seed.title, "year": seed.year, "authors": list(seed.authors),
                        "venue": seed.venue, "citation_count": seed.citation_count,
                        "color_index": index, "similarities": {}})
        groups = payload["recommendations"]["groups"]
        by_key = {group["key"]: group for group in groups}
        satellites.extend({"center_id": seed_id, "category": key,
                           "status": by_key.get(key, {}).get("status", "completed"),
                           "matched_count": by_key.get(key, {}).get("matched_count",
                                                                    len(by_key.get(key, {}).get("items", []))),
                           "items": by_key.get(key, {}).get("items", [])[:SATELLITES_PER_CATEGORY]}
                          for key in CATEGORIES)

    center_ids = {center["id"] for center in centers}
    aliases = {alias: center["id"] for center in centers for alias in [center["id"], *center["aliases"]]}
    for group in satellites:
        # A discovered paper promoted to a center is still the same paper across provider IDs.
        group["items"] = [{**item, "id": next((aliases[key] for key in [item["id"], *item.get("aliases", [])]
                                               if key in aliases), item["id"])} for item in group["items"]]
    counts: dict[str, list[str]] = {}
    for group in satellites:
        for item in group["items"]:
            if item["id"] in center_ids:
                continue
            counts.setdefault(item["id"], [])
            if group["center_id"] not in counts[item["id"]]:
                counts[item["id"]].append(group["center_id"])
    overlaps = [{"paper_id": paper_id, "center_ids": owners}
                for paper_id, owners in counts.items() if len(owners) > 1]

    center_edges = []
    edge_keys = set()
    for center in centers:
        for edge in _center_edges(center["id"], center_ids, satellites):
            ends = (edge["source"], edge["target"]) if edge["directed"] else tuple(sorted((edge["source"], edge["target"])))
            key = (*ends, edge["category"], edge["type"])
            if key not in edge_keys:
                edge_keys.add(key)
                center_edges.append(edge)

    return {"centers": centers, "satellites": satellites, "center_edges": center_edges,
            "overlaps": overlaps, "warnings": warnings,
            "pending_ids": list(dict.fromkeys(pending_ids)), "precompute": precompute,
            "computed_at": datetime.now(timezone.utc).isoformat()}

"""Multi-center relationship graph: per-center satellites, overlaps, and pairwise similarity."""

import logging
import re
from datetime import datetime, timezone

from app.infrastructure import embeddings
from app.services.retrieval import recommend, relation_score

logger = logging.getLogger(__name__)

SATELLITES_PER_CATEGORY = 5
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
            for relation in item.get("relations", []):
                style = {"cites": ("cites", "引用", True),
                         "bibliographic_coupling": ("bibliographic_coupling", "共同参考文献", False),
                         "co_citation": ("co_citation", "共被引", False),
                         "similar": ("similar", "主题相似", False),
                         "keyword": ("keyword", "关键词关联", False)}.get(relation)
                if not style:
                    continue
                edge_type, label, directed = style
                edges.append({"source": center_id, "target": other, "type": edge_type, "label": label,
                              "directed": directed, "category": group["category"],
                              "strength": item.get(SCORE_FIELDS[group["category"]])})
                break
    return edges


async def relate(ids: list[str], refresh: bool = False) -> dict:
    warnings: list[str] = []
    centers = []
    satellites = []
    seeds = []
    for raw in ids:
        try:
            seed = await recommend.load_seed(raw)
            payload = await recommend.bundle(raw, refresh, seed=seed)
        except Exception as exc:
            _warn(warnings, f"论文 {raw} 关系计算失败：{exc}")
            continue
        seed_id = recommend.canonical_id(seed)
        if any(center["id"] == seed_id for center in centers):
            continue
        index = len(centers)
        for warning in payload["recommendations"].get("warnings", []):
            if SOURCE_NAME.search(warning):
                logger.info("relate candidate warning for %s: %s", raw, warning)
            else:
                _warn(warnings, warning)
        centers.append({"id": seed_id, "local_id": getattr(seed, "local_id", None) or raw,
                        "title": seed.title, "year": seed.year, "authors": list(seed.authors),
                        "venue": seed.venue, "citation_count": seed.citation_count,
                        "color_index": index, "similarities": {}})
        seeds.append(seed)
        groups = payload["recommendations"]["groups"]
        by_key = {group["key"]: group for group in groups}
        satellites.extend({"center_id": seed_id, "category": key,
                           "status": by_key.get(key, {}).get("status", "completed"),
                           "matched_count": by_key.get(key, {}).get("matched_count",
                                                                    len(by_key.get(key, {}).get("items", []))),
                           "items": by_key.get(key, {}).get("items", [])[:SATELLITES_PER_CATEGORY]}
                          for key in CATEGORIES)

    center_ids = {center["id"] for center in centers}
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
    for center in centers:
        center_edges.extend(_center_edges(center["id"], center_ids, satellites))

    if len(centers) > 1:
        texts = [relation_score.embed_input(seed.title, seed.abstract) for seed in seeds]
        try:
            _, vectors = await embeddings.embed_siliconflow(texts, persist=False)
            for left in range(len(centers)):
                for right in range(left + 1, len(centers)):
                    similarity = round(relation_score.cosine(vectors.get(texts[left]), vectors.get(texts[right])), 4)
                    centers[left]["similarities"][centers[right]["id"]] = similarity
                    centers[right]["similarities"][centers[left]["id"]] = similarity
        except embeddings.EmbeddingError as exc:
            _warn(warnings, f"{exc.message}；主论文距离按均布展示")

    return {"centers": centers, "satellites": satellites, "center_edges": center_edges,
            "overlaps": overlaps, "warnings": warnings,
            "computed_at": datetime.now(timezone.utc).isoformat()}

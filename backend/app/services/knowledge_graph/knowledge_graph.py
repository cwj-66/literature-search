"""Workspace graph from metadata, saved keywords, and confirmed citations."""
import hashlib
import json
import unicodedata
from datetime import datetime, timezone

import httpx

from app.infrastructure.db import connection
from app.services.papers import library

UNKNOWN = "论文未明确说明"
ALIASES = {
    "rag": "RAG",
    "retrieval-augmented generation": "RAG",
    "retrieval augmented generation": "RAG",
    "hallucination": "Hallucination",
    "hallucinations": "Hallucination",
}
UNRELIABLE = ("未知", "暂不可用", "已停用", "无法核验", "不完整", "缺少")


def _object(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _clean(label: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", label).replace("‐", "-").replace("–", "-").replace("—", "-").split())


def _stable(kind: str, identity: str) -> str:
    digest = hashlib.sha256(identity.casefold().encode()).hexdigest()[:20]
    return f"{kind}:{digest}"


def canonical_keyword(term: str) -> str | None:
    text = _clean(term)
    if not text or text in (UNKNOWN, "未知"):
        return None
    return ALIASES.get(text.casefold(), text)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_query(local_id: str, status: str, message: str, edges: list[dict]) -> None:
    with connection() as db, db:
        db.execute(
            """INSERT INTO citation_queries (local_id, status, message, payload, queried_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(local_id) DO UPDATE SET status = excluded.status, message = excluded.message,
            payload = excluded.payload, queried_at = excluded.queried_at""",
            (local_id, status, message, json.dumps({"edges": edges}, ensure_ascii=False), _now()),
        )


def _load_query(db, local_id: str):
    return db.execute("SELECT * FROM citation_queries WHERE local_id = ?", (local_id,)).fetchone()


def build(limit: int = 80, references: list[str] | None = None) -> dict:
    nodes, edges, missing = {}, [], []

    def entity(kind, label, identity):
        key = _stable(kind, identity)
        nodes.setdefault(key, {"id": key, "kind": kind, "label": label, "provenance": "metadata"})
        return key

    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        if references is None:
            rows = db.execute("SELECT * FROM papers ORDER BY imported_at DESC, local_id LIMIT ?", (limit,)).fetchall()
        else:
            resolved, found = {}, set()
            refs = list(dict.fromkeys(references))
            for offset in range(0, len(refs), 400):
                batch = refs[offset:offset + 400]
                marks = ",".join("?" for _ in batch)
                matches = db.execute(f"""SELECT p.*, k.key AS alias FROM papers p
                    LEFT JOIN paper_keys k ON p.local_id = k.local_id
                    WHERE p.local_id IN ({marks}) OR k.key IN ({marks})""", batch + batch).fetchall()
                for row in matches:
                    resolved[row["local_id"]] = row
                    found.add(row["local_id"])
                    found.add(row["alias"])
            missing = [ref for ref in refs if ref not in found]
            rows = sorted(resolved.values(), key=lambda row: (row["imported_at"], row["local_id"]), reverse=True)

        aliases = {}
        papers = []
        for row in rows:
            paper = library.as_paper(row)
            key = "paper:" + paper.local_id
            paper_aliases = [paper.local_id, paper.id]
            for alias in db.execute("SELECT key FROM paper_keys WHERE local_id = ?", (paper.local_id,)):
                aliases[alias[0]] = key
                paper_aliases.append(alias[0])
            aliases[paper.local_id] = aliases[paper.id] = key
            author_ids = list(paper.author_ids)
            nodes[key] = {
                "id": key, "kind": "paper", "label": paper.title, "year": paper.year,
                "paper_id": paper.id, "local_id": paper.local_id, "aliases": list(dict.fromkeys(paper_aliases)),
                "url": paper.url, "authors": paper.authors, "author_ids": author_ids,
                "abstract": paper.abstract, "venue": paper.venue, "sources": paper.sources,
                "provenance": "metadata", "analysis": None,
                "citation_status": "not_queried", "citation_note": "尚未查询引用关系",
            }
            for index, author in enumerate(paper.authors):
                name = author.strip()
                if not name:
                    continue
                author_id = author_ids[index].strip() if index < len(author_ids) and isinstance(author_ids[index], str) else ""
                identity = f"id:{author_id}" if author_id else f"local:{paper.local_id}:{_clean(name).casefold()}"
                edges.append({"source": key, "target": entity("author", name, identity), "relation": "authored_by", "provenance": "metadata"})
            if paper.venue and paper.venue.strip():
                venue = _clean(paper.venue)
                edges.append({"source": key, "target": entity("venue", venue, venue.casefold()), "relation": "published_in", "provenance": "metadata"})
            result = db.execute("SELECT * FROM analysis_results WHERE local_id = ? ORDER BY generated_at DESC, result_id DESC LIMIT 1", (paper.local_id,)).fetchone()
            if result:
                payload = _object(result["payload"])
                if payload:
                    nodes[key]["analysis"] = {
                        "technical_method": payload.get("technical_method") if isinstance(payload.get("technical_method"), str) else None,
                        "innovations": [item for item in payload.get("innovations", []) if isinstance(item, str)] if isinstance(payload.get("innovations"), list) else [],
                        **{field: result[field] for field in ("result_id", "analysis_basis", "model_name", "generated_at", "source_url", "degradation_reason")},
                    }
                    terms = payload.get("keywords", [])
                    seen_terms = set()
                    for term in terms[:12] if isinstance(terms, list) else []:
                        if not isinstance(term, str):
                            continue
                        label = canonical_keyword(term[:80])
                        if not label or label.casefold() in seen_terms:
                            continue
                        seen_terms.add(label.casefold())
                        target = entity("keyword", label, label.casefold())
                        edges.append({
                            "source": key, "target": target, "relation": "has_keyword", "provenance": "model",
                            "result_id": result["result_id"], "generated_at": result["generated_at"],
                            "analysis_basis": result["analysis_basis"], "source_url": result["source_url"],
                        })
            papers.append(nodes[key])
            _apply_citations(db, nodes[key], aliases, edges)

    edges = list({(item["source"], item["target"], item["relation"]): item for item in edges}.values())
    for paper in papers:
        linked = [item for item in edges if item["relation"] == "cites" and paper["id"] in (item["source"], item["target"])]
        if linked and paper["citation_status"] in ("not_queried", "none_in_scope"):
            paper["citation_status"] = "linked"
            paper["citation_note"] = f"已确认 {len(linked)} 条当前范围内的引用"
        elif linked and paper["citation_status"] == "failed":
            paper["citation_status"] = "partial"
            paper["citation_note"] = "部分引用信息获取失败，已保留查到的引用"
        elif linked and paper["citation_status"] == "partial":
            paper["citation_note"] = "部分引用信息获取失败，已保留查到的引用"
        elif paper["citation_status"] == "linked":
            if linked:
                paper["citation_note"] = f"已确认 {len(linked)} 条当前范围内的引用"
            else:
                paper["citation_status"] = "none_in_scope"
                paper["citation_note"] = "已查询，当前范围内未发现引用。这不表示该论文一定没有引用"
    paper_count = len(papers)
    scope = f"{'当前工作区' if references is not None else '本地最近入库'}的 {paper_count} 篇论文"
    return {
        "nodes": list(nodes.values()), "edges": edges,
        "total_papers": paper_count if references is not None else total,
        "missing_references": missing, "scope": scope,
    }


def _apply_citations(db, paper: dict, aliases: dict, edges: list) -> None:
    query = _load_query(db, paper["local_id"])
    cached = db.execute(
        "SELECT payload, computed_at FROM paper_relations WHERE source_local_id = ? ORDER BY computed_at DESC LIMIT 1",
        (paper["local_id"],),
    ).fetchone()
    recorded = []
    unreliable = False
    if query:
        payload = _object(query["payload"])
        recorded.extend(payload.get("edges") or [])
        paper["citation_status"] = query["status"]
        paper["citation_note"] = query["message"] or paper["citation_note"]
        unreliable = query["status"] in ("failed", "partial", "missing_id")
    if cached:
        data = _object(cached["payload"])
        network = data.get("network") if isinstance(data.get("network"), dict) else {}
        for edge in network.get("edges") or []:
            if isinstance(edge, dict) and edge.get("type") == "cites":
                recorded.append(edge)
        warnings = []
        recommendations = data.get("recommendations") if isinstance(data.get("recommendations"), dict) else {}
        if isinstance(recommendations.get("warnings"), list):
            warnings.extend(str(item) for item in recommendations["warnings"])
        unreliable = unreliable or any(any(token in item for token in UNRELIABLE) for item in warnings)
        if not query:
            if unreliable:
                paper["citation_status"] = "failed"
                paper["citation_note"] = "引用信息暂不可用，不能判断为没有引用"
            else:
                paper["citation_status"] = "linked"
                paper["citation_note"] = "已查询引用关系"
    for edge in recorded:
        if not isinstance(edge, dict):
            continue
        source, target = edge.get("source"), edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        left, right = aliases.get(source), aliases.get(target)
        if left and right and left != right:
            edges.append({
                "source": left, "target": right, "relation": "cites",
                "provenance": "citation_query" if query else "citation_cache",
                "generated_at": query["queried_at"] if query else cached["computed_at"],
            })
    if not query and not cached:
        return
    if not query and cached and unreliable and paper["citation_status"] == "partial":
        return
    if not query and cached and not unreliable:
        paper["citation_status"] = "linked"


async def fill_citations(references: list[str]) -> dict:
    from app.services.retrieval import recommend, search

    papers, missing = await _papers(references)
    items = [{"id": ref, "title": ref, "status": "missing_id", "note": "这篇论文尚未入库，无法查询引用"} for ref in missing]
    if not search.semantic_enabled():
        for paper in papers:
            note = "引用信息暂不可用，不能判断当前范围内是否存在引用"
            _save_query(paper.local_id, "failed", note, _stored_edges(paper.local_id))
            items.append({"id": paper.id, "title": paper.title, "status": "failed", "note": note})
        return _summary(items)
    timeout = httpx.Timeout(25.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for paper in papers:
            ref = recommend.s2_paper_ref(paper)
            if not ref:
                note = "缺少可查询的论文标识，无法查询引用"
                _save_query(paper.local_id, "missing_id", note, [])
                items.append({"id": paper.id, "title": paper.title, "status": "missing_id", "note": note})
                continue
            outgoing, out_error = await _links(search, client, ref, "references")
            incoming, in_error = await _links(search, client, ref, "citations")
            edges = [{"source": paper.id, "target": recommend.canonical_id(item)} for item in outgoing]
            edges.extend({"source": recommend.canonical_id(item), "target": paper.id} for item in incoming)
            if out_error and in_error:
                note = "引用信息获取失败，暂不可用"
                _save_query(paper.local_id, "failed", note, _stored_edges(paper.local_id))
                status = "failed"
            elif out_error or in_error:
                note = "部分引用信息获取失败，已保留查到的引用"
                _save_query(paper.local_id, "partial", note, edges or _stored_edges(paper.local_id))
                status = "partial"
            elif edges:
                note = "已获取引用记录，图谱只显示当前范围内能够对应上的引用"
                _save_query(paper.local_id, "linked", note, edges)
                status = "linked"
            else:
                note = "已查询，当前返回的引用列表里没有可对应的论文；这不表示该论文一定没有引用"
                _save_query(paper.local_id, "none_in_scope", note, [])
                status = "none_in_scope"
            items.append({"id": paper.id, "title": paper.title, "status": status, "note": note})
    return _summary(items)


async def _papers(references: list[str]):
    import asyncio

    def load():
        found, missing, seen = [], [], set()
        for ref in dict.fromkeys(references):
            paper = library.get_paper(ref)
            if paper and paper.local_id not in seen:
                seen.add(paper.local_id)
                found.append(paper)
            elif not paper:
                missing.append(ref)
        return found, missing

    return await asyncio.to_thread(load)


async def _links(search, client, ref: str, kind: str):
    try:
        return await search.fetch_s2_links(client, ref, kind), None
    except search.SourceFailure as exc:
        return [], exc.message
    except httpx.HTTPError:
        return [], "请求失败"


def _stored_edges(local_id: str) -> list[dict]:
    with connection() as db:
        row = _load_query(db, local_id)
    payload = _object(row["payload"]) if row else {}
    edges = payload.get("edges")
    return edges if isinstance(edges, list) else []


def _summary(items: list[dict]) -> dict:
    return {
        "items": items,
        "queried": len(items),
        "failed": sum(item["status"] in ("failed", "partial", "missing_id") for item in items),
        "linked": sum(item["status"] == "linked" for item in items),
    }

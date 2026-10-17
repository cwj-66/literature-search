"""Read-only graph from library metadata, saved analyses and citation caches."""
import hashlib
import json
import unicodedata

from app.infrastructure.db import connection
from app.services.papers import library


def _object(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def build(limit: int = 80, references: list[str] | None = None) -> dict:
    nodes, edges, missing = {}, [], []

    def entity(kind, label):
        clean = " ".join(unicodedata.normalize("NFKC", label).split())
        key = kind + ":" + hashlib.sha256(clean.casefold().encode()).hexdigest()[:20]
        nodes.setdefault(key, {"id": key, "kind": kind, "label": clean,
                               "provenance": "model" if kind == "keyword" else "metadata"})
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
        for row in rows:
            paper = library.as_paper(row)
            key = "paper:" + paper.local_id
            paper_aliases = [paper.local_id, paper.id]
            for alias in db.execute("SELECT key FROM paper_keys WHERE local_id = ?", (paper.local_id,)):
                aliases[alias[0]] = key
                paper_aliases.append(alias[0])
            aliases[paper.local_id] = aliases[paper.id] = key
            nodes[key] = {"id": key, "kind": "paper", "label": paper.title, "year": paper.year,
                          "paper_id": paper.id, "local_id": paper.local_id, "aliases": list(dict.fromkeys(paper_aliases)), "url": paper.url,
                          "authors": paper.authors, "abstract": paper.abstract, "venue": paper.venue,
                          "sources": paper.sources, "provenance": "metadata", "analysis": None}
            for author in paper.authors:
                if author.strip():
                    edges.append({"source": key, "target": entity("author", author), "relation": "authored_by", "provenance": "metadata"})
            if paper.venue and paper.venue.strip():
                edges.append({"source": key, "target": entity("venue", paper.venue), "relation": "published_in", "provenance": "metadata"})
            result = db.execute("SELECT * FROM analysis_results WHERE local_id = ? ORDER BY generated_at DESC, result_id DESC LIMIT 1", (paper.local_id,)).fetchone()
            if not result:
                continue
            payload = _object(result["payload"])
            if not payload:
                continue
            nodes[key]["analysis"] = {
                "technical_method": payload.get("technical_method") if isinstance(payload.get("technical_method"), str) else None,
                "innovations": [x for x in payload.get("innovations", []) if isinstance(x, str)] if isinstance(payload.get("innovations"), list) else [],
                **{field: result[field] for field in ("result_id", "analysis_basis", "model_name", "generated_at", "source_url", "degradation_reason")},
            }
            terms = payload.get("keywords", [])
            for term in terms[:12] if isinstance(terms, list) else []:
                if not isinstance(term, str) or not term.strip() or term.strip() in ("论文未明确说明", "未知"):
                    continue
                target = entity("keyword", term[:80])
                edges.append({"source": key, "target": target, "relation": "has_keyword", "provenance": "model",
                              "result_id": result["result_id"], "generated_at": result["generated_at"],
                              "analysis_basis": result["analysis_basis"], "source_url": result["source_url"]})

        # Only actual citations retrieved by the recommendation service; never similarity or citation_count.
        for paper in [n for n in nodes.values() if n["kind"] == "paper"]:
            cached = db.execute("SELECT payload, computed_at FROM paper_relations WHERE source_local_id = ? ORDER BY computed_at DESC LIMIT 1", (paper["local_id"],)).fetchone()
            if not cached:
                continue
            network = _object(cached["payload"]).get("network", {})
            if not isinstance(network, dict) or not isinstance(network.get("edges"), list):
                continue
            for edge in network["edges"]:
                if not isinstance(edge, dict) or edge.get("type") != "cites":
                    continue
                if not isinstance(edge.get("source"), str) or not isinstance(edge.get("target"), str):
                    continue
                source, target = aliases.get(edge.get("source")), aliases.get(edge.get("target"))
                if source and target and source != target:
                    edges.append({"source": source, "target": target, "relation": "cites", "provenance": "citation_cache",
                                  "generated_at": cached["computed_at"]})

    edges = list({(e["source"], e["target"], e["relation"]): e for e in edges}.values())
    paper_count = sum(n["kind"] == "paper" for n in nodes.values())
    return {"nodes": list(nodes.values()), "edges": edges, "total_papers": paper_count if references is not None else total,
            "missing_references": missing, "scope": f"{'当前工作区' if references is not None else '本地最近入库'}的 {paper_count} 篇论文；主题来自已保存的 AI 分析，引用来自已缓存的真实引用记录。"}

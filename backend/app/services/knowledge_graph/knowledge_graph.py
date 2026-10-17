"""A bounded entity graph of the local library; no remote/model requests."""

import hashlib
import json

from app.infrastructure.db import connection
from app.services.papers import library


def build(limit: int = 80) -> dict:
    papers = library.list_recent(limit)
    nodes, edges = {}, []

    def entity(kind, label):
        clean = " ".join(label.split())
        identity = clean.casefold()
        key = kind + ":" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        nodes.setdefault(key, {"id": key, "kind": kind, "label": clean})
        return key

    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        for paper in papers:
            key = "paper:" + paper.local_id
            nodes[key] = {"id": key, "kind": "paper", "label": paper.title, "year": paper.year,
                          "paper_id": paper.id, "url": paper.url}
            for author in paper.authors[:12]:
                if author.strip():
                    edges.append({"source": key, "target": entity("author", author), "relation": "authored_by"})
            if paper.venue and paper.venue.strip():
                edges.append({"source": key, "target": entity("venue", paper.venue), "relation": "published_in"})
            row = db.execute("SELECT payload FROM analysis_results WHERE local_id = ? ORDER BY generated_at DESC, result_id DESC LIMIT 1", (paper.local_id,)).fetchone()
            if row:
                try:
                    payload = json.loads(row["payload"])
                    keywords = payload.get("keywords", []) if isinstance(payload, dict) else []
                except (ValueError, TypeError):
                    keywords = []
                if isinstance(keywords, list):
                    seen = set()
                    for term in keywords[:12]:
                        if not isinstance(term, str) or not term.strip() or term.strip() == "论文未明确说明":
                            continue
                        target = entity("keyword", term[:80])
                        if target not in seen:
                            edges.append({"source": key, "target": target, "relation": "has_keyword"})
                            seen.add(target)
    edges = list({(e["source"], e["target"], e["relation"]): e for e in edges}.values())
    return {"nodes": list(nodes.values()), "edges": edges, "total_papers": total,
            "scope": f"本地最近入库的 {len(papers)} 篇论文（最多 {limit} 篇），关键词来自已有AI分析；未分析论文仍展示作者和刊会。"}

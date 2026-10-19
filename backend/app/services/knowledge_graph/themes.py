"""Research themes and topic evolution for a closed paper sample.

Co-occurrence -> Louvain communities -> Callon centrality/density quadrants,
plus period slicing with Cobo inclusion-index links.
Core algorithm functions are pure (no DB, no network) for testability.
"""
import asyncio
import hashlib
import json
import logging
import statistics
from datetime import datetime, timezone
from itertools import combinations

from app.infrastructure import llm
from app.infrastructure.db import connection
from app.services.knowledge_graph import knowledge_graph
from app.services.papers import library

log = logging.getLogger(__name__)

MAX_KEYWORDS = 12
MIN_COOCCURRENCE = 2
INCLUSION_THRESHOLD = 0.15
RECENT_LIMIT = 80
MIN_THEME_PAPERS = 3

NAMING_SYSTEM = """你是学术研究主题命名助手。只根据用户给出的关键词列表为一个研究主题命名，禁止使用给定关键词之外的知识编造内容。
只输出一个 JSON 对象，不要 Markdown。字段为：
name：主题的中文名称，简短名词短语（不超过 20 字）
description：一句话中文描述该主题的研究内容（不超过 60 字）
用简体中文。"""

NAMING_USER = """以下关键词来自同一个研究主题（共现聚类），附每词出现的论文数：
{keywords}

请为主题返回 JSON：{{"name": "...", "description": "..."}}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pure algorithm core (no DB / no network)
# ---------------------------------------------------------------------------

def cooccurrence(keyword_sets: list[list[str]]) -> tuple[dict, dict]:
    """Count keyword co-occurrence within papers. Returns ({(a,b): count}, {term: paper_count})."""
    edges, freq = {}, {}
    for terms in keyword_sets:
        unique = list(dict.fromkeys(terms))
        for term in unique:
            freq[term] = freq.get(term, 0) + 1
        for left, right in combinations(sorted(unique), 2):
            key = (left, right)
            edges[key] = edges.get(key, 0) + 1
    return edges, freq


def salton_cosine(count: float, freq_a: int, freq_b: int) -> float:
    """Salton cosine similarity: co-occurrence / sqrt(freq_a * freq_b)."""
    if freq_a <= 0 or freq_b <= 0:
        return 0.0
    return count / (freq_a * freq_b) ** 0.5


def _detect_communities(keywords: list[str], edges: dict) -> list[set]:
    """Louvain community detection on the co-occurrence graph (weight = co-occurrence count,
    weak edges with count < MIN_COOCCURRENCE dropped). Keywords without a surviving edge are
    not part of any theme. Returns a list of keyword sets."""
    graph_edges = {pair: count for pair, count in edges.items() if count >= MIN_COOCCURRENCE}
    if not graph_edges:
        return []
    try:
        import networkx as nx
        from networkx.algorithms.community import louvain_communities
    except ImportError:  # pragma: no cover - fallback when networkx is unavailable offline
        nx, louvain_communities = None, None
    if nx is not None:
        graph = nx.Graph()
        graph.add_nodes_from(keywords)
        graph.add_weighted_edges_from((a, b, w) for (a, b), w in graph_edges.items())
        communities = [set(c) for c in louvain_communities(graph, weight="weight", seed=42)]
    else:
        # Fallback: greedy modularity maximization implemented from scratch (numpy not
        # required); close to Louvain in quality for small co-occurrence graphs.
        communities = _greedy_modularity(list(graph_edges))
    return [c for c in communities if len(c) >= 2]


def _greedy_modularity(graph_edges: dict) -> list[set]:
    nodes = sorted({term for pair in graph_edges for term in pair})
    degree = {term: 0 for term in nodes}
    total = 0.0
    for (a, b), w in graph_edges.items():
        degree[a] += w
        degree[b] += w
        total += w
    communities = {term: {term} for term in nodes}

    def gain(a_set, b_set):
        between = sum(w for (x, y), w in graph_edges.items() if (x in a_set and y in b_set) or (x in b_set and y in a_set))
        return between / total - (sum(degree[t] for t in a_set) * sum(degree[t] for t in b_set)) / (2 * total * total)

    improved = True
    while improved:
        improved = False
        for term in nodes:
            best, best_gain = None, 0.0
            home = communities[term]
            for other in communities.values():
                if other is home:
                    continue
                g = gain({term}, other)
                if g > best_gain:
                    best, best_gain = other, g
            if best is not None:
                home.discard(term)
                best.add(term)
                communities[term] = best
                improved = True
        communities = {term: c for term, c in communities.items() if c}
    return list(communities.values())


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        rank = (start + end) / 2 + 1
        for index in range(start, end + 1):
            ranks[order[index]] = rank
        start = end + 1
    return ranks


def assign_quadrants(themes: list[dict]) -> None:
    """Split themes by the mean of Callon centrality/density ranks.
    motor (high, high), niche (low, high), basic (high, low), emerging (low, low)."""
    if len(themes) < 2:
        for theme in themes:
            theme["quadrant"] = None
        return
    rcen = _ranks([theme["centrality"] for theme in themes])
    rden = _ranks([theme["density"] for theme in themes])
    mean_cen = statistics.mean(rcen)
    mean_den = statistics.mean(rden)
    for theme, cen, den in zip(themes, rcen, rden):
        high_cen = cen >= mean_cen
        high_den = den >= mean_den
        if high_cen and high_den:
            theme["quadrant"] = "motor"
        elif high_den:
            theme["quadrant"] = "niche"
        elif high_cen:
            theme["quadrant"] = "basic"
        else:
            theme["quadrant"] = "emerging"


def analyze_themes(papers: list[dict]) -> list[dict]:
    """Build themes from papers (each {"local_id", "year", "keywords"}).
    Pure function: no DB, no network, no LLM. Themes lack name/description."""
    keyword_sets = [p["keywords"] for p in papers if p["keywords"]]
    if not keyword_sets:
        return []
    edges, freq = cooccurrence(keyword_sets)
    keywords = sorted(freq)
    communities = _detect_communities(keywords, edges)
    if len(communities) <= 1:
        # Degraded case: no significant co-occurrence structure. One theme with everything.
        themes = [{"keywords": keywords, "paper_ids": [p["local_id"] for p in papers if p["keywords"]]}] if keywords else []
    else:
        themes = [
            {"keywords": sorted(community), "paper_ids": [p["local_id"] for p in papers if p["keywords"] and set(p["keywords"]) & community]}
            for community in communities
        ]
    for theme in themes:
        members = theme["keywords"]
        member_set = set(members)
        internal = sum(edges.get(tuple(sorted(pair)), 0) for pair in combinations(members, 2))
        theme["density"] = (100.0 * internal / len(members)) if members else 0.0
        theme["centrality"] = float(sum(
            edges.get(tuple(sorted((term, other))), 0)
            for term in members for other in freq if other not in member_set
        ))
        theme["size"] = len(theme["paper_ids"])
        by_year = {}
        for paper in papers:
            if paper["year"] is None or not set(paper["keywords"]) & member_set:
                continue
            by_year[paper["year"]] = by_year.get(paper["year"], 0) + 1
        theme["by_year"] = {str(year): by_year[year] for year in sorted(by_year)}
    assign_quadrants(themes)
    themes.sort(key=lambda t: t["size"], reverse=True)
    for index, theme in enumerate(themes):
        theme["id"] = f"theme:{index}"
        theme.setdefault("name", "")
        theme.setdefault("description", "")
    return themes


def split_periods(years: list[int]) -> list[tuple[int, int]]:
    """Cut the year range into 2 periods (span <= 6) or 3 periods, as contiguous ranges."""
    distinct = sorted(set(years))
    if len(distinct) < 2:
        return [(distinct[0], distinct[0])] if distinct else []
    low, high = distinct[0], distinct[-1]
    span = high - low + 1
    count = 2 if span <= 6 else 3
    width = -(-span // count)
    periods = []
    start = low
    while start <= high:
        end = min(start + width - 1, high)
        periods.append((start, end))
        start = end + 1
    return periods


def jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def inclusion(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def period_links(period_themes: list[list[dict]], threshold: float = INCLUSION_THRESHOLD) -> list[dict]:
    """Link adjacent-period themes by Cobo inclusion index |A∩B| / min(|A|, |B|)."""
    links = []
    for index in range(len(period_themes) - 1):
        for from_pos, from_theme in enumerate(period_themes[index]):
            for to_pos, to_theme in enumerate(period_themes[index + 1]):
                overlap = inclusion(set(from_theme["keywords"]), set(to_theme["keywords"]))
                if overlap >= threshold:
                    links.append({
                        "from": f"{index}:{from_pos}", "to": f"{index + 1}:{to_pos}",
                        "weight": round(overlap, 4),
                        "from_period": index, "to_period": index + 1,
                        "from_label": from_theme.get("name") or "", "to_label": to_theme.get("name") or "",
                    })
    return links


def analyze_evolution(papers: list[dict], themes: list[dict]) -> dict:
    """Thematic evolution over year slices. Period themes borrow names from the closest
    global theme by Jaccard overlap (LLM naming is only used for global themes)."""
    years = sorted({p["year"] for p in papers if p["year"] is not None})
    periods = split_periods(years)
    if not periods:
        return {"periods": [], "links": [], "reason": "年份不足，无法绘制主题演进"}

    def label_for(keyword_set: set, freq_hint: dict) -> str:
        best, best_overlap = "", 0.0
        for theme in themes:
            overlap = jaccard(keyword_set, set(theme["keywords"]))
            if overlap > best_overlap:
                best, best_overlap = theme.get("name") or "", overlap
        if best:
            return best
        top = max(freq_hint, key=freq_hint.get, default="")
        return top

    period_themes = []
    freq_global = {}
    for p in papers:
        for term in p["keywords"]:
            freq_global[term] = freq_global.get(term, 0) + 1
    for start, end in periods:
        subset = [p for p in papers if p["year"] is not None and start <= p["year"] <= end and p["keywords"]]
        found = analyze_themes(subset)
        period_themes.append([
            {**theme, "name": label_for(set(theme["keywords"]), freq_global)} for theme in found
        ])
    links = period_links(period_themes)
    reason = ""
    if len(periods) < 2:
        reason = "年份不足，无法绘制主题演进"
    elif not links:
        reason = "相邻时段没有足够的共享关键词"
    return {
        "periods": [f"{start}-{end}" for start, end in periods],
        "links": links,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Data loading and LLM naming (DB / network aware)
# ---------------------------------------------------------------------------

def _keywords(payload: dict) -> list[str]:
    terms = payload.get("keywords", [])
    seen, result = set(), []
    for term in terms[:MAX_KEYWORDS] if isinstance(terms, list) else []:
        if not isinstance(term, str):
            continue
        for label in knowledge_graph.expand_keywords(term[:80]):
            if label.casefold() in seen:
                continue
            seen.add(label.casefold())
            result.append(label)
    return result


def _apply_fallback(papers: list[dict]) -> None:
    analyzed = {term for paper in papers for term in paper["keywords"]}
    for paper in papers:
        if paper["keywords"]:
            paper.pop("title", None)
            paper.pop("abstract", None)
            continue
        paper["keywords"] = knowledge_graph.fallback_keywords(paper.get("title") or "", paper.get("abstract"), analyzed)
        paper.pop("title", None)
        paper.pop("abstract", None)


def _load_papers(references: list[str] | None, limit: int) -> tuple[list[dict], int]:
    """Resolve papers like knowledge_graph.build, keeping only local_id/year/latest keywords."""
    with connection() as db:
        total = db.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        if references is None:
            rows = db.execute("SELECT * FROM papers ORDER BY imported_at DESC, local_id LIMIT ?", (limit,)).fetchall()
        else:
            resolved = {}
            refs = list(dict.fromkeys(references))
            for offset in range(0, len(refs), 400):
                batch = refs[offset:offset + 400]
                marks = ",".join("?" for _ in batch)
                matches = db.execute(f"""SELECT p.* FROM papers p
                    LEFT JOIN paper_keys k ON p.local_id = k.local_id
                    WHERE p.local_id IN ({marks}) OR k.key IN ({marks})""", batch + batch).fetchall()
                for row in matches:
                    resolved[row["local_id"]] = row
            rows = sorted(resolved.values(), key=lambda row: (row["imported_at"], row["local_id"]), reverse=True)
        papers = []
        for row in rows:
            paper = library.as_paper(row)
            result = db.execute("SELECT payload FROM analysis_results WHERE local_id = ? ORDER BY generated_at DESC, result_id DESC LIMIT 1", (paper.local_id,)).fetchone()
            payload = knowledge_graph._object(result["payload"]) if result else {}
            papers.append({
                "local_id": paper.local_id, "year": paper.year, "keywords": _keywords(payload),
                "title": paper.title, "abstract": paper.abstract,
            })
    _apply_fallback(papers)
    return papers, total


def _load_expanded(references: list[str] | None, limit: int) -> tuple[list[dict], int]:
    seeds, _ = _load_papers(references, limit)
    papers, seen = [], set()
    with connection() as db:
        for seed in seeds:
            row = db.execute(
                "SELECT payload FROM paper_relations WHERE source_local_id = ? ORDER BY computed_at DESC LIMIT 1",
                (seed["local_id"],),
            ).fetchone()
            if not row:
                continue
            data = knowledge_graph._object(row["payload"])
            rec = data.get("recommendations") if isinstance(data.get("recommendations"), dict) else {}
            for item in rec.get("items") or []:
                if not isinstance(item, dict):
                    continue
                paper_id = item.get("id")
                if not isinstance(paper_id, str) or paper_id in seen:
                    continue
                seen.add(paper_id)
                local = library.get_paper(paper_id)
                keywords = []
                year = item.get("year") if isinstance(item.get("year"), int) else None
                if local:
                    result = db.execute("SELECT payload FROM analysis_results WHERE local_id = ? ORDER BY generated_at DESC, result_id DESC LIMIT 1", (local.local_id,)).fetchone()
                    keywords = _keywords(knowledge_graph._object(result["payload"]) if result else {})
                    year = local.year if year is None else year
                papers.append({
                    "local_id": paper_id, "year": year, "keywords": keywords,
                    "title": item.get("title") or "", "abstract": item.get("abstract") if isinstance(item.get("abstract"), str) else "",
                })
    _apply_fallback(papers)
    return papers, len(papers)


def cluster_key(keywords: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(keywords)).encode()).hexdigest()


def _load_name_cache(keys: list[str]) -> dict:
    if not keys:
        return {}
    with connection() as db:
        marks = ",".join("?" for _ in keys)
        rows = db.execute(f"SELECT cluster_key, name, description FROM theme_names WHERE cluster_key IN ({marks})", keys).fetchall()
    return {row["cluster_key"]: {"name": row["name"], "description": row["description"]} for row in rows}


def _save_name(key: str, name: str, description: str) -> None:
    with connection() as db, db:
        db.execute(
            """INSERT INTO theme_names (cluster_key, name, description, created_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(cluster_key) DO UPDATE SET name = excluded.name, description = excluded.description,
            created_at = excluded.created_at""",
            (key, name, description, _now()),
        )


async def _name_one(key: str, keywords: list[str], freq: dict) -> dict:
    listing = "\n".join(f"- {term}（{freq.get(term, 0)} 篇）" for term in keywords)
    messages = [{"role": "system", "content": NAMING_SYSTEM}, {"role": "user", "content": NAMING_USER.format(keywords=listing)}]
    raw = await llm.complete(messages)
    text = raw.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text)
    name = data.get("name") if isinstance(data.get("name"), str) else ""
    description = data.get("description") if isinstance(data.get("description"), str) else ""
    if not name.strip():
        raise ValueError("empty theme name")
    return {"name": name.strip(), "description": description.strip()}


async def _name_themes(themes: list[dict], papers: list[dict]) -> None:
    """Fill name/description, reusing the shared LLM client; cached per keyword cluster."""
    freq = {}
    for paper in papers:
        for term in paper["keywords"]:
            freq[term] = freq.get(term, 0) + 1
    keys = [cluster_key(theme["keywords"]) for theme in themes]
    cache = await asyncio.to_thread(_load_name_cache, keys)
    for theme, key in zip(themes, keys):
        if key in cache:
            theme["name"], theme["description"] = cache[key]["name"], cache[key]["description"]
            continue
        try:
            named = await _name_one(key, theme["keywords"], freq)
        except Exception as exc:  # LLM unavailable/misbehaving: degrade to top keyword
            log.warning("theme naming degraded: key=%s error=%s", key[:12], type(exc).__name__)
            top = max(theme["keywords"], key=lambda t: freq.get(t, 0), default="")
            named = {"name": top, "description": ""}
        theme.update(named)
        if named["description"]:
            await asyncio.to_thread(_save_name, key, named["name"], named["description"])


async def build(references: list[str] | None = None, limit: int = RECENT_LIMIT, scope: str = "workspace") -> dict:
    loader = _load_expanded if scope == "expanded" else _load_papers
    papers, total = await asyncio.to_thread(loader, references, limit)
    analyzed = [p for p in papers if p["keywords"]]
    found = await asyncio.to_thread(analyze_themes, analyzed)
    insufficient = bool(papers) and (len(analyzed) < MIN_THEME_PAPERS or len(found) <= 1)
    if insufficient:
        for theme in found:
            theme["quadrant"] = None
    if found:
        await _name_themes(found, analyzed)
    if insufficient:
        evolution = {"periods": [], "links": [], "reason": "样本不足，不绘制主题演进"}
    else:
        evolution = analyze_evolution(analyzed, found)
    years = sorted({p["year"] for p in analyzed if p["year"] is not None})
    if scope == "expanded":
        scope_note = "扩展文献样本来自已缓存的推荐结果，不是全领域"
    elif references is not None:
        scope_note = "统计范围为当前工作区，不代表整个研究领域"
    else:
        scope_note = "统计范围为本地最近入库的论文，不代表整个研究领域"
    return {
        "themes": found,
        "evolution": evolution,
        "years": years,
        "total_papers": len(papers) if references is not None or scope == "expanded" else total,
        "analyzed_papers": len(analyzed),
        "scope_note": scope_note,
        "scope": scope,
        "status": "insufficient" if insufficient else "ok",
        "status_reason": "论文不足 3 篇或只有 1 个主题，不绘制战略坐标图" if insufficient else "",
    }

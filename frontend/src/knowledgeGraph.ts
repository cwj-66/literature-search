import type { EntityGraph, EntityKind, EntityNode } from "./graphData";

export const EMPTY_GRAPH: EntityGraph = { nodes: [], edges: [], total_papers: 0, scope: "" };
export const ENTITY_KINDS: EntityKind[] = ["paper", "author", "keyword", "venue"];

export function filterGraph(data: EntityGraph, query = "", from = 0, to = 0): EntityGraph {
  const term = query.trim().toLocaleLowerCase();
  const matched = new Set(data.nodes.filter(n => n.label.toLocaleLowerCase().includes(term)).map(n => n.id));
  const byPaper = new Map<string, Set<string>>();
  for (const e of data.edges) {
    if (e.relation === "cites") continue;
    if (!byPaper.has(e.source)) byPaper.set(e.source, new Set());
    byPaper.get(e.source)!.add(e.target);
  }
  const papers = data.nodes.filter(n => n.kind === "paper" && (!from || n.year != null && n.year >= from)
    && (!to || n.year != null && n.year <= to)
    && (!term || matched.has(n.id) || [...(byPaper.get(n.id) ?? [])].some(id => matched.has(id))));
  const ids = new Set(papers.map(n => n.id));
  const edges = data.edges.filter(e => ids.has(e.source) && (e.relation !== "cites" || ids.has(e.target)));
  const keep = new Set([...ids, ...edges.map(e => e.target)]);
  return { ...data, nodes: data.nodes.filter(n => keep.has(n.id)), edges, total_papers: papers.length };
}

export function canvasGraph(data: EntityGraph, relations: string[], selected: string): EntityGraph {
  const available = data.edges.filter(e => relations.includes(e.relation));
  const adjacent = new Set(available.filter(e => e.source === selected || e.target === selected).flatMap(e => [e.source, e.target]));
  const papers = data.nodes.filter(n => n.kind === "paper").sort((a, b) =>
    Number(b.id === selected) - Number(a.id === selected) || Number(adjacent.has(b.id)) - Number(adjacent.has(a.id)));
  const keep = new Set(papers.slice(0, 36).map(n => n.id));
  const rank = new Map<string, number>();
  available.filter(e => keep.has(e.source)).forEach(e => rank.set(e.target, (rank.get(e.target) ?? 0) + 1));
  data.nodes.filter(n => n.kind !== "paper" && rank.has(n.id)).sort((a, b) =>
    Number(b.id === selected) - Number(a.id === selected) || Number(adjacent.has(b.id)) - Number(adjacent.has(a.id)) || (rank.get(b.id)! - rank.get(a.id)!))
    .slice(0, 90).forEach(n => keep.add(n.id));
  return { ...data, nodes: data.nodes.filter(n => keep.has(n.id)), edges: available.filter(e => keep.has(e.source) && keep.has(e.target)) };
}

export function topicStats(data: EntityGraph) {
  const papers = data.nodes.filter(n => n.kind === "paper");
  const paperMap = new Map(papers.map(n => [n.id, n]));
  return data.nodes.filter(n => n.kind === "keyword").map(n => {
    const related = [...new Set(data.edges.filter(e => e.relation === "has_keyword" && e.target === n.id).map(e => e.source))]
      .flatMap(id => paperMap.has(id) ? [paperMap.get(id)!] : []);
    const years = related.flatMap(p => p.year == null ? [] : [p.year]);
    return { ...n, papers: related, count: related.length, firstYear: years.length ? Math.min(...years) : null };
  }).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function relatedPapers(data: EntityGraph, selected: EntityNode) {
  const shared = new Set(data.edges.filter(e => e.source === selected.id && ["authored_by", "has_keyword"].includes(e.relation)).map(e => e.target));
  const nodes = new Map(data.nodes.map(n => [n.id, n]));
  return data.nodes.filter(n => n.kind === "paper" && n.id !== selected.id).flatMap(p => {
    const reasons = data.edges.filter(e => e.source === p.id && shared.has(e.target)).map(e =>
      `${e.relation === "has_keyword" ? "共同主题" : "共同作者"}：${nodes.get(e.target)?.label}`);
    for (const e of data.edges.filter(e => e.relation === "cites")) {
      if (e.source === selected.id && e.target === p.id) reasons.unshift("当前论文引用了此论文");
      if (e.target === selected.id && e.source === p.id) reasons.unshift("此论文引用了当前论文");
    }
    return reasons.length ? [{ node: p, reasons: [...new Set(reasons)] }] : [];
  }).sort((a, b) => b.reasons.length - a.reasons.length);
}

// Repeated keyword co-occurrence is an explainable grouping, not a method lineage.
export function topicGroups(data: EntityGraph) {
  const topics = topicStats(data);
  const parent = new Map(topics.map(t => [t.id, t.id]));
  const root = (id: string): string => { let p = id; while (parent.get(p) !== p) p = parent.get(p)!; return p; };
  const pairs = new Map<string, number>();
  const byPaper = new Map<string, string[]>();
  for (const e of data.edges.filter(e => e.relation === "has_keyword")) {
    if (!byPaper.has(e.source)) byPaper.set(e.source, []);
    byPaper.get(e.source)!.push(e.target);
  }
  for (const ids of byPaper.values()) {
    const unique = [...new Set(ids)].sort();
    unique.forEach((a, i) => unique.slice(i + 1).forEach(b => { const key = `${a}|${b}`; pairs.set(key, (pairs.get(key) ?? 0) + 1); }));
  }
  for (const [pair, count] of pairs) if (count >= 2) { const [a, b] = pair.split("|"); parent.set(root(a), root(b)); }
  const groups = new Map<string, typeof topics>();
  for (const t of topics) { const id = root(t.id); if (!groups.has(id)) groups.set(id, []); groups.get(id)!.push(t); }
  return [...groups.values()].filter(g => g.length > 1).sort((a, b) => b.length - a.length);
}

import { useEffect, useState } from "react";
import type { EntityGraph, EntityNode } from "./graphData";
import { EMPTY_GRAPH } from "./knowledgeGraph";
import type { Project } from "./projects";

export function useKnowledgeGraph(references?: string[]) {
  const key = references === undefined ? "local" : JSON.stringify([...new Set(references)].sort());
  const [data, setData] = useState<EntityGraph | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setData(null); setError("");
    const refs: string[] | undefined = key === "local" ? undefined : JSON.parse(key);
    if (refs && !refs.length) { setData(EMPTY_GRAPH); return () => abort.abort(); }
    fetch(refs ? "/api/knowledge-graph" : "/api/knowledge-graph?limit=2000", {
      signal: abort.signal, ...(refs ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(refs) } : {}),
    }).then(async response => {
      if (!response.ok) throw new Error(response.status === 422 ? "工作区论文标识过多或无效，暂时无法读取图谱。" : "知识图谱加载失败，请重试。");
      return await response.json() as EntityGraph;
    }).then(result => { if (!abort.signal.aborted) setData(result); })
      .catch((err: Error) => { if (!abort.signal.aborted) setError(err.message); });
    return () => abort.abort();
  }, [key, revision]);
  return { data, error, refresh: () => setRevision(r => r + 1) };
}

// Workspaces also hold real search-result metadata before library import.
// Keep those papers visible, without inventing analyses or writing to the DB.
export function withWorkspaceMetadata(raw: EntityGraph, project: Project): EntityGraph {
  const nodes = [...raw.nodes], edges = [...raw.edges];
  const normalize = (s: string) => s.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
  const entities = new Map(nodes.filter(n => n.kind !== "paper").map(n => [`${n.kind}:${normalize(n.label)}`, n.id]));
  const present = new Set(raw.nodes.filter(n => n.kind === "paper").flatMap(n => [n.paper_id, n.local_id, ...(n.aliases ?? [])]));
  for (const [key, { paper }] of Object.entries(project.papers)) {
    if (present.has(key) || present.has(paper.id) || paper.local_id && present.has(paper.local_id)) continue;
    const id = `workspace-paper:${key}`;
    nodes.push({ id, kind: "paper", label: paper.title, paper_id: paper.id, local_id: paper.local_id,
      authors: paper.authors, abstract: paper.abstract, year: paper.year, venue: paper.venue,
      url: paper.url, sources: paper.sources, provenance: "workspace_metadata", analysis: null });
    present.add(paper.id);
    for (const label of paper.authors) {
      if (!label.trim()) continue;
      const target = `workspace-author:${key}:${normalize(label)}`;
      nodes.push({ id: target, kind: "author", label: label.trim(), provenance: "workspace_metadata" });
      edges.push({ source: id, target, relation: "authored_by", provenance: "workspace_metadata" });
    }
    if (paper.venue?.trim()) {
      const identity = `venue:${normalize(paper.venue)}`;
      let target = entities.get(identity);
      if (!target) {
        target = `workspace-${identity}`; entities.set(identity, target);
        nodes.push({ id: target, kind: "venue", label: paper.venue.trim(), provenance: "workspace_metadata" });
      }
      edges.push({ source: id, target, relation: "published_in", provenance: "workspace_metadata" });
    }
  }
  return { ...raw, nodes, edges: [...new Map(edges.map(e => [`${e.source}|${e.target}|${e.relation}`, e])).values()], total_papers: nodes.filter(n => n.kind === "paper").length };
}

export function workspaceNodeKey(project: Project, node: EntityNode): string | undefined {
  return Object.entries(project.papers).find(([key, item]) => key === node.local_id || key === node.paper_id || item.paper.id === node.paper_id || Boolean(node.local_id && item.paper.local_id === node.local_id) || node.aliases?.includes(key) || node.aliases?.includes(item.paper.id))?.[0];
}

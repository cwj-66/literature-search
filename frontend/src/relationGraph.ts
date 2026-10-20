import { arrowOf, CATEGORY_ORDER, type RelationCategory } from "./relationLayout";
import type { Paper, RecommendationItem, RelateCenter, RelateResponse } from "./types";

export const RECOMMENDATIONS_PER_DIMENSION = 1;

export function paperSnapshot(paper: Paper, key: string): RelateResponse {
  const aliases = [key, paper.id, ...(paper.arxiv_id ? [`arxiv:${paper.arxiv_id}`] : []),
    ...(paper.doi ? [`doi:${paper.doi.toLowerCase()}`] : []), ...(paper.semantic_scholar_id ? [`s2:${paper.semantic_scholar_id}`] : [])];
  const id = paper.arxiv_id ? `arxiv:${paper.arxiv_id}` : paper.doi ? `doi:${paper.doi.toLowerCase()}` : paper.id;
  return { centers: [{ id, aliases, local_id: key, title: paper.title, year: paper.year, authors: paper.authors,
    color_index: 0, similarities: {} }], satellites: [], center_edges: [], overlaps: [], warnings: [], computed_at: "" };
}

/** Merge independent single-paper results; adding B never changes A's chosen recommendations. */
export function mergeRelationSnapshots(parts: RelateResponse[]): RelateResponse {
  const centers = parts.flatMap(p => p.centers).filter((c, i, all) => all.findIndex(other => other.id === c.id) === i)
    .map((c, i) => ({ ...c, color_index: i }));
  return { centers, satellites: parts.flatMap(p => p.satellites), center_edges: parts.flatMap(p => p.center_edges),
    overlaps: parts.flatMap(p => p.overlaps), warnings: [...new Set(parts.flatMap(p => p.warnings))],
    computed_at: parts.map(p => p.computed_at).sort().at(-1) ?? "" };
}

export interface SpaceNode {
  id: string;
  title: string;
  year: number | null;
  center?: RelateCenter;
  item?: RecommendationItem;
  owners: string[];
  categories: RelationCategory[];
  x: number;
  y: number;
}
export interface SpaceEdge {
  id: string;
  source: string;
  target: string;
  category: RelationCategory;
  strength: number | null;
  arrow: "out" | "in" | "both" | null;
  item: RecommendationItem;
}
export interface SpaceGraph { nodes: SpaceNode[]; edges: SpaceEdge[]; width: number; height: number }

/** Identity, ownership and edges are independent: sharing a child never creates a parent edge. */
export function buildSpaceGraph(data: RelateResponse): SpaceGraph {
  const parent = new Map<string, string>();
  const find = (id: string): string => {
    const next = parent.get(id);
    if (!next) { parent.set(id, id); return id; }
    if (next === id) return id;
    const root = find(next); parent.set(id, root); return root;
  };
  const join = (ids: string[]) => {
    const root = find(ids[0]);
    for (const id of ids.slice(1)) parent.set(find(id), root);
  };
  for (const c of data.centers) join([c.id, ...(c.aliases ?? []), ...(c.local_id ? [c.local_id] : [])]);
  for (const g of data.satellites) for (const item of g.items) join([item.id, ...(item.aliases ?? [])]);
  // Prefer the public ID of a selected center after joining aliases across sources.
  const centerIds = new Map(data.centers.map(c => [find(c.id), c.id]));
  const identity = (id: string) => centerIds.get(find(id)) ?? find(id);
  const nodes = new Map<string, SpaceNode>();
  for (const center of data.centers) {
    const id = identity(center.id);
    nodes.set(id, { id, title: center.title, year: center.year, center, owners: [], categories: [], x: 0, y: 0 });
  }
  const edges = new Map<string, SpaceEdge>();
  for (const group of data.satellites) {
    const source = identity(group.center_id);
    if (!nodes.has(source)) continue;
    const category = group.category as RelationCategory;
    if (!CATEGORY_ORDER.includes(category)) continue;
    for (const item of group.items.slice(0, RECOMMENDATIONS_PER_DIMENSION)) {
      const target = identity(item.id);
      if (source === target) continue;
      if (!nodes.has(target)) nodes.set(target, { id: target, title: item.title, year: item.year, item, owners: [], categories: [], x: 0, y: 0 });
      const node = nodes.get(target)!;
      node.item ??= item;
      if (!node.owners.includes(source)) node.owners.push(source);
      if (!node.categories.includes(category)) node.categories.push(category);
      const arrow = category === "citation" ? arrowOf(item) : null;
      const [a, b] = [source, target].sort();
      const key = `${a}|${b}|${category}`;
      const strength = category === "citation" ? item.citation_score : category === "semantic" ? item.semantic_score : item.keyword_score;
      const previous = edges.get(key);
      if (previous) {
        const normalized = source === previous.source ? arrow : arrow === "in" ? "out" : arrow === "out" ? "in" : arrow;
        if (normalized && previous.arrow && normalized !== previous.arrow) previous.arrow = "both";
        else previous.arrow ??= normalized;
        previous.strength = Math.max(previous.strength ?? 0, strength ?? 0);
      } else edges.set(key, { id: key, source, target, category, strength, arrow, item });
    }
  }
  return layoutSpace([...nodes.values()], [...edges.values()]);
}

function layoutSpace(nodes: SpaceNode[], edges: SpaceEdge[]): SpaceGraph {
  const centers = nodes.filter(n => n.center);
  const width = centers.length <= 2 ? Math.max(1160, centers.length * 680) : centers.length < 5 ? 1660 : 1900;
  const height = centers.length <= 2 ? 850 : centers.length < 5 ? 1240 : 1400;
  const centerMap = new Map<string, SpaceNode>();
  centers.forEach((node, i) => {
    const angle = -Math.PI / 2 + i * Math.PI * 2 / centers.length;
    node.x = centers.length <= 2 ? (centers.length === 1 ? width / 2 : 380 + i * (width - 760)) : width / 2 + Math.cos(angle) * (centers.length === 5 ? 480 : 390);
    node.y = centers.length <= 2 ? height / 2 : height / 2 + Math.sin(angle) * (centers.length === 5 ? 410 : 320);
    centerMap.set(node.id, node);
  });
  const satellites = nodes.filter(n => !n.center);
  // Direction encodes the relation: citation points away from the cluster, the other two flank it.
  const gx = centers.reduce((sum, n) => sum + n.x, 0) / (centers.length || 1);
  const gy = centers.reduce((sum, n) => sum + n.y, 0) / (centers.length || 1);
  const spread = centers.length === 1 ? Math.PI * 2 / 3 : Math.PI * 0.36;
  const offset: Record<RelationCategory, number> = { citation: 0, semantic: -spread, keyword: spread };
  const shared = new Map<string, number>();
  for (const node of satellites) {
    const owners = node.owners.map(id => centerMap.get(id)).filter((n): n is SpaceNode => Boolean(n));
    if (owners.length > 1) {
      const cx = owners.reduce((sum, n) => sum + n.x, 0) / owners.length;
      const cy = owners.reduce((sum, n) => sum + n.y, 0) / owners.length;
      const key = owners.map(n => n.id).sort().join("|");
      const i = shared.get(key) ?? 0; shared.set(key, i + 1);
      const a = owners[0], b = owners[owners.length - 1];
      const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
      const side = i % 2 ? 1 : -1, step = owners.length > 2 ? 60 * i : 120 + Math.floor(i / 2) * 110;
      node.x = cx - dy / length * side * step; node.y = cy + dx / length * side * step;
      continue;
    }
    const owner = owners[0] ?? { x: width / 2, y: height / 2 };
    const alone = centers.length === 1 || Math.hypot(owner.x - gx, owner.y - gy) < 1;
    const angle = (alone ? -Math.PI / 2 : Math.atan2(owner.y - gy, owner.x - gx)) + offset[node.categories[0] ?? "semantic"];
    node.x = owner.x + Math.cos(angle) * 240; node.y = owner.y + Math.sin(angle) * 240;
  }
  // Relax label-sized rectangles as well as circles, with selected centers fixed.
  for (let tick = 0; tick < 160; tick++) {
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      const dx = b.x - a.x || 0.1, dy = b.y - a.y || 0.1;
      const ox = 225 - Math.abs(dx), oy = 140 - Math.abs(dy);
      if (ox <= 0 || oy <= 0) continue;
      const axisX = ox < oy;
      const amount = Math.min(axisX ? ox : oy, 24) * 0.55;
      const sx = axisX ? Math.sign(dx) * amount : 0, sy = axisX ? 0 : Math.sign(dy) * amount;
      if (!a.center) { a.x -= sx; a.y -= sy; }
      if (!b.center) { b.x += sx; b.y += sy; }
    }
    for (const node of satellites) {
      node.x = Math.max(130, Math.min(width - 130, node.x));
      node.y = Math.max(100, Math.min(height - 135, node.y));
    }
  }
  return { nodes, edges, width, height };
}

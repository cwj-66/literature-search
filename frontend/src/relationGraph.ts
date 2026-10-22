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
    pending_ids: [...new Set(parts.flatMap(p => p.pending_ids ?? []))], precompute: parts.flatMap(p => p.precompute ?? []),
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
  bend: number;
}
export interface SpaceGraph { nodes: SpaceNode[]; edges: SpaceEdge[]; width: number; height: number }

/** Direction is relative to the original recommendation owner, not the open panel. */
export function citationPairs(edge: SpaceEdge): [string, string][] {
  if (edge.category !== "citation") return [];
  return [...(edge.arrow === "out" || edge.arrow === "both" ? [[edge.source, edge.target] as [string, string]] : []),
    ...(edge.arrow === "in" || edge.arrow === "both" ? [[edge.target, edge.source] as [string, string]] : [])];
}

export function edgeMainPaperId(edge: SpaceEdge, nodes: SpaceNode[]): string {
  return nodes.find(n => n.id === edge.source)?.center ? edge.source : edge.target;
}

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
      } else edges.set(key, { id: key, source, target, category, strength, arrow, item, bend: 0 });
    }
  }
  return layoutSpace([...nodes.values()], [...edges.values()]);
}

function ringOrder(centers: SpaceNode[], nodes: SpaceNode[], edges: SpaceEdge[]) {
  if (centers.length < 4 || centers.length > 5) return centers;
  const weight = (a: SpaceNode, b: SpaceNode) => nodes.filter(n => n.owners.includes(a.id) && n.owners.includes(b.id)).length
    + edges.filter(e => [e.source, e.target].includes(a.id) && [e.source, e.target].includes(b.id)).length;
  const score = (ring: SpaceNode[]) => ring.reduce((sum, n, i) => sum + weight(n, ring[(i + 1) % ring.length]), 0);
  const permute = (rest: SpaceNode[]): SpaceNode[][] => rest.length <= 1 ? [rest] : rest.flatMap((n, i) => permute(rest.filter((_, j) => j !== i)).map(p => [n, ...p]));
  let best = centers, top = score(centers);
  for (const p of permute(centers.slice(1))) {
    const ring = [centers[0], ...p], s = score(ring);
    if (s > top) { best = ring; top = s; }
  }
  return best;
}

type Point = { x: number; y: number };
function routeEdges(nodes: SpaceNode[], edges: SpaceEdge[]) {
  const byId = new Map(nodes.map(n => [n.id, n]));
  const groups = new Map<string, SpaceEdge[]>();
  for (const e of edges) { const key = [e.source, e.target].sort().join("|"); groups.set(key, [...groups.get(key) ?? [], e]); }
  const curve = (a: Point, b: Point, bend: number) => {
    const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
    const mx = (a.x + b.x) / 2 - dy / length * bend, my = (a.y + b.y) / 2 + dx / length * bend;
    return Array.from({ length: 11 }, (_, i) => { const t = i / 10, u = 1 - t; return { x: u * u * a.x + 2 * u * t * mx + t * t * b.x, y: u * u * a.y + 2 * u * t * my + t * t * b.y }; });
  };
  const cross = (p: Point, q: Point, r: Point, s: Point) => {
    const d = (q.x - p.x) * (s.y - r.y) - (q.y - p.y) * (s.x - r.x);
    if (!d) return false;
    const t = ((r.x - p.x) * (s.y - r.y) - (r.y - p.y) * (s.x - r.x)) / d, u = ((r.x - p.x) * (q.y - p.y) - (r.y - p.y) * (q.x - p.x)) / d;
    return t > 0 && t < 1 && u > 0 && u < 1;
  };
  const routed: { ends: string[]; points: Point[] }[] = [];
  const pairs = [...groups.values()].map(group => {
    const [a, b] = [group[0].source, group[0].target].sort().map(id => byId.get(id)!);
    return { group: group.sort((x, y) => CATEGORY_ORDER.indexOf(x.category) - CATEGORY_ORDER.indexOf(y.category)), a, b, length: Math.hypot(b.x - a.x, b.y - a.y) };
  }).sort((x, y) => y.length - x.length);
  for (const { group, a, b } of pairs) {
    const ends = [a.id, b.id], spread = group.map((_, i) => (i - (group.length - 1) / 2) * 28);
    let best = 0, cost = Infinity;
    for (const shift of [0, 36, -36, 72, -72, 120, -120, 180, -180]) {
      let c = Math.abs(shift) / 150;
      for (const offset of spread) {
        const points = curve(a, b, shift + offset), inner = points.slice(1, -1);
        for (const n of nodes) if (!ends.includes(n.id) && inner.some(p => Math.hypot(p.x - n.x, p.y - n.y) < (n.center ? 46 : 26)
          || (Math.abs(p.x - n.x) < 85 && p.y > n.y + 12 && p.y < n.y + 46))) c += 10;
        for (const other of routed) if (!other.ends.some(id => ends.includes(id)))
          for (let i = 1; i < points.length; i++) for (let j = 1; j < other.points.length; j++) if (cross(points[i - 1], points[i], other.points[j - 1], other.points[j])) c += 1;
      }
      if (c < cost) { cost = c; best = shift; }
    }
    group.forEach((e, i) => {
      const bend = best + spread[i];
      e.bend = e.source === a.id ? bend : -bend;
      routed.push({ ends, points: curve(a, b, bend) });
    });
  }
}

function layoutSpace(nodes: SpaceNode[], edges: SpaceEdge[]): SpaceGraph {
  const centers = ringOrder(nodes.filter(n => n.center), nodes, edges);
  const columns = Math.ceil(Math.sqrt(centers.length));
  const rows = Math.ceil(centers.length / columns);
  const width = centers.length > 5 ? columns * 720 : centers.length <= 2 ? Math.max(1160, centers.length * 680) : centers.length < 5 ? 1660 : 1900;
  const height = centers.length > 5 ? rows * 680 : centers.length <= 2 ? 850 : centers.length < 5 ? 1240 : 1400;
  const centerMap = new Map<string, SpaceNode>();
  centers.forEach((node, i) => {
    const angle = -Math.PI / 2 + i * Math.PI * 2 / centers.length;
    node.x = centers.length <= 2 ? (centers.length === 1 ? width / 2 : 380 + i * (width - 760)) : width / 2 + Math.cos(angle) * (centers.length === 5 ? 480 : 390);
    node.y = centers.length <= 2 ? height / 2 : height / 2 + Math.sin(angle) * (centers.length === 5 ? 410 : 320);
    if (centers.length > 5) {
      node.x = 360 + (i % columns) * 720;
      node.y = 340 + Math.floor(i / columns) * 680;
    }
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
      const side = i % 2 ? 1 : -1;
      let step = owners.length > 2 ? 60 * i : 120 + Math.floor(i / 2) * 110;
      const place = () => { node.x = cx - dy / length * side * step; node.y = cy + dx / length * side * step; };
      place();
      for (let tries = 0; tries < 6 && centers.some(c => !owners.includes(c) && Math.hypot(c.x - node.x, c.y - node.y) < 220); tries++) { step += 70; place(); }
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
  routeEdges(nodes, edges);
  return { nodes, edges, width, height };
}

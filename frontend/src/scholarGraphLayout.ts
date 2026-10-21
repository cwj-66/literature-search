import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import type { EntityGraph, EntityNode } from "./graphData";

export interface ScholarNode { node: EntityNode; x: number; y: number; width: number; height: number }
type Sim = SimulationNodeDatum & { id: string; paper: boolean; width: number; height: number };
type Link = SimulationLinkDatum<Sim> & { peer: boolean };

export function scholarLayout(data: EntityGraph, focus: string) {
  const papers = data.nodes.filter(n => n.kind === "paper");
  const first = papers.find(n => n.id === focus) ?? papers[0];
  const owners = new Map<string, string[]>();
  for (const e of data.edges) if (!["cites", "similar"].includes(e.relation)) owners.set(e.target, [...owners.get(e.target) ?? [], e.source]);
  const sims = new Map<string, Sim>();
  [first, ...papers.filter(n => n !== first)].filter(Boolean).forEach((node, i) => {
    const r = 150 * Math.sqrt(i), a = i * 2.39996;
    sims.set(node.id, { id: node.id, paper: true, width: i ? 210 : 224, height: i ? 68 : 76, x: Math.cos(a) * r, y: Math.sin(a) * r });
  });
  data.nodes.filter(n => n.kind !== "paper").forEach((node, i) => {
    const base = (owners.get(node.id) ?? []).flatMap(id => sims.has(id) ? [sims.get(id)!] : []);
    const x = base.length ? base.reduce((s, n) => s + n.x!, 0) / base.length : 0, y = base.length ? base.reduce((s, n) => s + n.y!, 0) / base.length : 0;
    const a = i * 2.39996, r = base.length > 1 ? 30 : 130;
    sims.set(node.id, { id: node.id, paper: false, width: Math.min(190, Math.max(100, node.label.length * 5.7 + 28)), height: node.kind === "keyword" ? 30 : 38, x: x + Math.cos(a) * r, y: y + Math.sin(a) * r });
  });
  const nodes = [...sims.values()];
  const links: Link[] = data.edges.filter(e => sims.has(e.source) && sims.has(e.target) && e.source !== e.target).map(e => ({ source: e.source, target: e.target, peer: ["cites", "similar"].includes(e.relation) }));
  const degree = (id: string) => owners.get(id)?.length ?? 1;
  forceSimulation(nodes)
    .force("link", forceLink<Sim, Link>(links).id(n => n.id)
      .distance(l => l.peer ? 320 : degree((l.target as Sim).id) > 1 ? 190 : 125)
      .strength(l => l.peer ? .08 : 1 / Math.max(1, degree((l.target as Sim).id))))
    .force("charge", forceManyBody<Sim>().strength(n => n.paper ? -1100 : -260).distanceMax(900))
    .force("collide", forceCollide<Sim>(n => n.width / 2 * (n.paper ? .78 : .62) + 12).iterations(2))
    .force("x", forceX(0).strength(.05)).force("y", forceY(0).strength(.07))
    .stop().tick(320);
  for (let step = 0; step < 80; step++) {
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j], dx = b.x! - a.x! || .1, dy = b.y! - a.y! || .1;
      const ox = (a.width + b.width) / 2 + 18 - Math.abs(dx), oy = (a.height + b.height) / 2 + 20 - Math.abs(dy);
      if (ox <= 0 || oy <= 0) continue;
      const mx = ox < oy ? Math.sign(dx) * ox * .5 : 0, my = ox < oy ? 0 : Math.sign(dy) * oy * .5;
      a.x! -= mx; a.y! -= my; b.x! += mx; b.y! += my;
    }
  }
  const pad = 70;
  const minX = Math.min(0, ...nodes.map(n => n.x! - n.width / 2)) - pad, maxX = Math.max(0, ...nodes.map(n => n.x! + n.width / 2)) + pad;
  const minY = Math.min(0, ...nodes.map(n => n.y! - n.height / 2)) - pad, maxY = Math.max(0, ...nodes.map(n => n.y! + n.height / 2)) + pad;
  const width = Math.max(600, maxX - minX), height = Math.max(460, maxY - minY);
  const ox = (width - (maxX - minX)) / 2 - minX, oy = (height - (maxY - minY)) / 2 - minY;
  const byId = new Map(data.nodes.map(n => [n.id, n]));
  return { nodes: nodes.map(n => ({ node: byId.get(n.id)!, x: n.x! + ox, y: n.y! + oy, width: n.width, height: n.height })), width, height };
}

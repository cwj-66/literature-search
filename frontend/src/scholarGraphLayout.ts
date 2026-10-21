import type { EntityGraph, EntityNode } from "./graphData";

export interface ScholarNode { node: EntityNode; x: number; y: number; width: number; height: number }
export function scholarLayout(data: EntityGraph, focus: string) {
  const papers = data.nodes.filter(n => n.kind === "paper");
  const center = papers.find(n => n.id === focus) ?? papers[0];
  const width = Math.max(1000, Math.ceil(Math.sqrt(papers.length)) * 380);
  const height = Math.max(760, Math.ceil(Math.sqrt(papers.length)) * 300);
  const placed = new Map<string, ScholarNode>();
  const others = papers.filter(n => n !== center);
  if (center) placed.set(center.id, { node: center, x: width / 2, y: height / 2, width: 224, height: 76 });
  others.forEach((node, i) => {
    const angle = Math.PI * .15 + i * Math.PI * 2 / Math.max(others.length, 1);
    const radius = Math.min(width, height) * .32;
    placed.set(node.id, { node, x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius, width: 210, height: 68 });
  });
  const entities = data.nodes.filter(n => n.kind !== "paper");
  entities.forEach((node, i) => {
    const owners = data.edges.filter(e => e.target === node.id).flatMap(e => placed.has(e.source) ? [placed.get(e.source)!] : []);
    const x = owners.length ? owners.reduce((s,n) => s+n.x,0)/owners.length : width/2;
    const y = owners.length ? owners.reduce((s,n) => s+n.y,0)/owners.length : height/2;
    const angle = Math.atan2(y-height/2,x-width/2) + (i % 5 - 2) * .65;
    placed.set(node.id, { node, x: x+Math.cos(angle)*175, y:y+Math.sin(angle)*175, width: Math.min(190, Math.max(100, node.label.length*5.7+28)), height: node.kind === "keyword" ? 30 : 38 });
  });
  const nodes = [...placed.values()];
  // Deterministic collision resolution keeps labels clear without inventing relations.
  for (let step=0; step<100; step++) {
    for (let i=0; i<nodes.length; i++) for (let j=i+1; j<nodes.length; j++) {
      const a=nodes[i], b=nodes[j], dx=b.x-a.x || .1, dy=b.y-a.y || .1;
      const ox=(a.width+b.width)/2+18-Math.abs(dx), oy=(a.height+b.height)/2+20-Math.abs(dy);
      if (ox<=0 || oy<=0) continue;
      const mx=ox<oy ? Math.sign(dx)*ox*.35 : 0, my=ox<oy ? 0 : Math.sign(dy)*oy*.35;
      if (a.node!==center) { a.x-=mx; a.y-=my; }
      if (b.node!==center) { b.x+=mx; b.y+=my; }
    }
    for (const n of nodes) { n.x=Math.max(n.width/2+25,Math.min(width-n.width/2-25,n.x)); n.y=Math.max(75,Math.min(height-65,n.y)); }
  }
  return { nodes, width, height };
}

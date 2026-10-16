import { useMemo, useRef, useState } from "react";
import { Button } from "antd";
import { KIND_COLOR, KIND_LABEL, type EntityGraph, type EntityNode } from "../graphData";

export function KnowledgeCanvas({ data, selected, onSelect }: { data: EntityGraph; selected: string; onSelect: (id: string) => void }) {
  const [view, setView] = useState({ x: 0, y: 0, zoom: 1 });
  const drag = useRef<{ x: number; y: number; originX: number; originY: number; moved: boolean } | null>(null);
  const positions = useMemo(() => {
    const result = new Map<string, EntityNode & { x: number; y: number }>();
    const groups = { paper: data.nodes.filter(n => n.kind === "paper"), author: data.nodes.filter(n => n.kind === "author"), keyword: data.nodes.filter(n => n.kind === "keyword"), venue: data.nodes.filter(n => n.kind === "venue") };
    for (const [kind, list] of Object.entries(groups)) {
      list.forEach((node, i) => {
        let x = 0, y = 0;
        if (kind === "paper") {
          const angle = 2 * Math.PI * i / Math.max(list.length, 1) - Math.PI / 2;
          x = 510 + Math.cos(angle) * 190; y = 345 + Math.sin(angle) * 130;
        } else if (kind === "author") {
          x = 90 + (i % 9) * 109; y = 65 + Math.floor(i / 9) * 65;
        } else if (kind === "keyword") {
          x = 90 + (i % 9) * 109; y = 545 + Math.floor(i / 9) * 65;
        } else { x = 940; y = 230 + i * 55; }
        result.set(node.id, { ...node, x, y });
      });
    }
    return result;
  }, [data]);
  const linked = new Set([selected]);
  for (const edge of data.edges) {
    if (edge.source === selected) linked.add(edge.target);
    if (edge.target === selected) linked.add(edge.source);
  }
  const height = Math.max(660, ...[...positions.values()].map(n => n.y + 55));
  return <div className="kg-canvas-shell">
    <div className="kg-canvas-tools">
      <span>拖动画布平移 · 点击节点查看关系</span>
      <div><Button aria-label="缩小图谱" onClick={() => setView(v => ({ ...v, zoom: Math.max(.4, v.zoom - .2) }))}>−</Button>
        <Button aria-label="放大图谱" onClick={() => setView(v => ({ ...v, zoom: Math.min(2.5, v.zoom + .2) }))}>＋</Button>
        <Button onClick={() => setView({ x: 0, y: 0, zoom: 1 })}>复位</Button></div>
    </div>
    <svg className="kg-canvas" viewBox={`0 0 1080 ${height}`} aria-label="论文、作者、关键词与刊会知识图谱"
      onPointerDown={event => { if (event.button !== 0) return; drag.current = { x: event.clientX, y: event.clientY, originX: view.x, originY: view.y, moved: false }; }}
      onPointerMove={event => {
        const d = drag.current; if (!d || !(event.buttons & 1)) return;
        const scale = 1080 / event.currentTarget.getBoundingClientRect().width;
        if (Math.hypot(event.clientX - d.x, event.clientY - d.y) > 5) d.moved = true;
        if (d.moved) { event.currentTarget.setPointerCapture(event.pointerId); setView(v => ({ ...v, x: d.originX + (event.clientX - d.x) * scale, y: d.originY + (event.clientY - d.y) * scale })); }
      }}
      onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
      onPointerLeave={() => { drag.current = null; }}>
      <g transform={`translate(${540 + view.x} ${height / 2 + view.y}) scale(${view.zoom}) translate(-540 ${-height / 2})`}>
        {data.edges.map((edge, i) => {
          const a = positions.get(edge.source), b = positions.get(edge.target); if (!a || !b) return null;
          const active = edge.source === selected || edge.target === selected;
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={active ? KIND_COLOR[b.kind] : "#cdd6e4"} strokeWidth={active ? 2.2 : 1} opacity={selected && !active ? .13 : .65} />;
        })}
        {[...positions.values()].map(node => <g key={node.id} role="button" tabIndex={0}
          aria-label={`${KIND_LABEL[node.kind]}：${node.label}`} className="kg-node"
          transform={`translate(${node.x} ${node.y})`} opacity={selected && !linked.has(node.id) ? .25 : 1}
          onClick={() => { if (!drag.current?.moved) onSelect(node.id === selected ? "" : node.id); }}
          onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(node.id === selected ? "" : node.id); } }}>
          <title>{node.label}</title>
          <circle r={node.kind === "paper" ? 22 : 14} fill={KIND_COLOR[node.kind]} stroke={selected === node.id ? "#152b58" : "white"} strokeWidth={selected === node.id ? 4 : 3} />
          <text y="4" textAnchor="middle" fill="white" fontSize="11">{node.kind === "paper" ? node.year ?? "?" : KIND_LABEL[node.kind].slice(0, 1)}</text>
          <text y={node.kind === "paper" ? 40 : 32} textAnchor="middle" fill="#34425b" fontSize="12" stroke="#fbfcff" strokeWidth="4" paintOrder="stroke">{node.label.length > 19 ? node.label.slice(0, 18) + "…" : node.label}</text>
        </g>)}
      </g>
    </svg>
  </div>;
}

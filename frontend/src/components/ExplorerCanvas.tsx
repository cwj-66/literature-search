import { useEffect, useRef, useState } from "react";
import { Button } from "antd";
import { EdgeEvent, Graph, NodeEvent, type IElementEvent } from "@antv/g6";
import { KIND_COLOR, KIND_LABEL, RELATIONS, type EntityGraph } from "../graphData";

const cameras = new Map<string, { position: [number, number]; zoom: number; signature: string; nodes: Record<string, [number, number]> }>();

export function ExplorerCanvas({ data, selected, onSelect, onSelectEdge, scope, resetKey }: {
  data: EntityGraph; selected: string; onSelect: (id: string) => void; onSelectEdge?: (id: string) => void; scope: string; resetKey: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<Graph | null>(null);
  const callback = useRef(onSelect);
  const edgeCallback = useRef(onSelectEdge);
  callback.current = onSelect;
  edgeCallback.current = onSelectEdge;
  const selection = useRef(selected);
  const lastFocused = useRef(selected);
  selection.current = selected;
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  const [fitKey, setFitKey] = useState(0);

  useEffect(() => {
    const box = container.current;
    if (!box || !data.nodes.length) return;
    let alive = true, settled = false, rendered = false;
    const signature = JSON.stringify([data.nodes.map(n => n.id), data.edges.map(e => [e.source, e.target, e.relation])]);
    const saved = cameras.get(scope);
    const restored = saved?.signature === signature && !fitKey && !resetKey ? saved : undefined;
    setFailed(false); setReady(false);
    const graph = new Graph({
      container: box, width: box.clientWidth, height: box.clientHeight,
      padding: [35, 65, 45, 65], autoFit: "view", animation: false, zoomRange: [.15, 4],
      data: {
        nodes: data.nodes.map(n => ({ id: n.id, data: { ...n }, ...(restored?.nodes[n.id] ? { style: { x: restored.nodes[n.id][0], y: restored.nodes[n.id][1] } } : {}) })),
        edges: data.edges.map((e, i) => ({ id: `edge-${i}`, source: e.source, target: e.target, data: { ...e } })),
      },
      layout: restored ? undefined : { type: "force", preventOverlap: true, nodeSize: 110, linkDistance: 145, maxIteration: 350, gravity: 4, animation: false },
      node: {
        type: d => d.data?.kind === "keyword" ? "ellipse" : d.data?.kind === "author" ? "circle" : "rect",
        style: {
          size: d => d.data?.kind === "paper" ? [128, 42] : d.data?.kind === "keyword" ? [112, 36] : d.data?.kind === "venue" ? [108, 34] : 34,
          radius: (d: { data?: { kind?: string } }) => d.data?.kind === "author" ? 17 : 10,
          fill: d => d.data?.kind === "paper" ? "#e7f0ff" : d.data?.kind === "keyword" ? "#e5f8ef" : d.data?.kind === "author" ? "#f3e9ff" : "#fff6e8",
          stroke: d => KIND_COLOR[d.data?.kind as keyof typeof KIND_COLOR], lineWidth: 1.6,
          labelText: d => { const text = String(d.data?.label ?? ""); return text.length > 42 ? `${text.slice(0, 41)}…` : text; },
          labelPlacement: "center", labelFontSize: 12, labelLineHeight: 16,
          labelFill: d => KIND_COLOR[d.data?.kind as keyof typeof KIND_COLOR] ?? "#334155",
          labelWordWrap: true, labelMaxWidth: 108, labelMaxLines: 2,
          labelBackground: true, labelBackgroundFill: "#fff", labelBackgroundOpacity: .85,
          cursor: "pointer",
        },
        state: {
          selected: { stroke: "#172d51", lineWidth: 3, halo: true, haloStroke: "#b9c9eb", haloLineWidth: 8 },
          neighbor: { lineWidth: 2, stroke: "#90a4ba" },
          dim: { opacity: .18 },
        },
      },
      edge: {
        type: "quadratic",
        style: {
          stroke: d => RELATIONS[String(d.data?.relation)]?.color ?? "#96a0b0",
          lineDash: d => RELATIONS[String(d.data?.relation)]?.dash ?? [],
          endArrow: d => d.data?.relation === "cites", curveOffset: 12, lineWidth: 1.2, opacity: .48,
        },
        state: { active: { opacity: 1, lineWidth: 2 }, dim: { opacity: .06 } },
      },
      behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
      plugins: [{ type: "tooltip", getContent: (_event: unknown, items: { data?: Record<string, unknown> }[]) => {
        const el = document.createElement("div");
        const node = items[0]?.data;
        el.style.maxWidth = "300px";
        el.textContent = node?.kind ? `${KIND_LABEL[node.kind as keyof typeof KIND_LABEL]}：${node.label}` : RELATIONS[String(node?.relation)]?.label ?? "关系";
        return el;
      } }],
    });
    graph.on(NodeEvent.CLICK, event => {
      const id = (event as IElementEvent).target.id;
      if (id) callback.current(id === selection.current ? "" : id);
    });
    graph.on(EdgeEvent.CLICK, event => {
      const id = (event as IElementEvent).target.id;
      if (id) edgeCallback.current?.(id);
    });
    graph.render().then(async () => {
      settled = true;
      if (!alive) { graph.destroy(); return; }
      rendered = true;
      if (restored) {
        await graph.zoomTo(restored.zoom, false);
        if (alive) await graph.translateTo(restored.position, false);
      }
      if (alive) { instance.current = graph; setReady(true); }
    }).catch(() => { settled = true; if (!alive) graph.destroy(); else setFailed(true); });
    let previousWidth = box.clientWidth;
    const resize = new ResizeObserver(() => {
      if (alive && rendered && box.clientWidth > 0) {
        graph.resize(box.clientWidth, box.clientHeight);
        if (Math.abs(box.clientWidth - previousWidth) > 80) void graph.fitView({}, false).catch(() => undefined);
        previousWidth = box.clientWidth;
      }
    });
    resize.observe(box);
    return () => {
      alive = false; resize.disconnect();
      if (rendered) {
        const pos = graph.getPosition();
        const nodePositions = Object.fromEntries(data.nodes.map(n => {
          const p = graph.getElementPosition(n.id);
          return [n.id, [p[0], p[1]] as [number, number]];
        }));
        cameras.set(scope, { position: [pos[0], pos[1]], zoom: graph.getZoom(), signature, nodes: nodePositions });
        if (cameras.size > 24) cameras.delete(cameras.keys().next().value!);
      }
      instance.current = null;
      if (settled) graph.destroy();
    };
  }, [data, scope, fitKey, resetKey]);

  useEffect(() => {
    const graph = instance.current;
    if (!graph || !ready) return;
    const adjacent = new Set([selected]);
    data.edges.filter(e => e.source === selected || e.target === selected).forEach(e => { adjacent.add(e.source); adjacent.add(e.target); });
    const states: Record<string, string[]> = {};
    data.nodes.forEach(n => { states[n.id] = !selected ? [] : n.id === selected ? ["selected"] : adjacent.has(n.id) ? ["neighbor"] : ["dim"]; });
    data.edges.forEach((e, i) => { states[`edge-${i}`] = !selected ? [] : e.source === selected || e.target === selected ? ["active"] : ["dim"]; });
    void graph.setElementState(states, false).catch(() => undefined);
    if (selected && selected !== lastFocused.current && data.nodes.some(n => n.id === selected)) void graph.focusElement(selected, false).catch(() => undefined);
    lastFocused.current = selected;
  }, [selected, data, ready]);

  return <div className="kg-explorer-canvas">
    <div className="kg-canvas-tools"><span>拖动节点或画布 · 滚轮缩放 · 点击探索</span><div>
      <Button size="small" disabled={!ready} aria-label="缩小图谱" onClick={() => { const g = instance.current; if (g) void g.zoomTo(Math.max(.15, g.getZoom() / 1.25)); }}>−</Button>
      <Button size="small" disabled={!ready} aria-label="放大图谱" onClick={() => { const g = instance.current; if (g) void g.zoomTo(Math.min(4, g.getZoom() * 1.25)); }}>＋</Button>
      <Button size="small" onClick={() => { cameras.delete(scope); setFitKey(k => k + 1); }}>适应画布</Button>
    </div></div>
    {failed ? <p className="kg-render-status" role="alert">图谱暂时无法绘制，可在右侧列表继续探索。<Button size="small" onClick={() => setFitKey(k => k + 1)}>重新绘制</Button></p> : !ready ? <p className="kg-render-status" role="status">正在排列节点…</p> : null}
    <div ref={container} className="kg-g6-canvas" aria-label="可拖动缩放的科研知识关系网络" />
  </div>;
}

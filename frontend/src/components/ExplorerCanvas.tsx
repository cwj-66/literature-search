import { useEffect, useRef, useState } from "react";
import { Button } from "antd";
import { EdgeEvent, Graph, NodeEvent, type IElementEvent } from "@antv/g6";
import { KIND_COLOR, KIND_LABEL, KIND_TINT, RELATIONS, type EntityGraph, type EntityKind } from "../graphData";
import { useMediaQuery } from "../hooks";
import { tokens } from "../theme";

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
  const still = useMediaQuery("(prefers-reduced-motion: reduce)");

  useEffect(() => {
    const box = container.current;
    if (!box || !data.nodes.length) return;
    let alive = true, settled = false, rendered = false;
    const signature = JSON.stringify([data.nodes.map(n => n.id), data.edges.map(e => [e.source, e.target, e.relation])]);
    const saved = cameras.get(scope);
    const restored = saved?.signature === signature && !fitKey && !resetKey ? saved : undefined;
    setFailed(false); setReady(false);
    const kindOf = (d: { data?: Record<string, unknown> }) => (d.data?.kind ?? "paper") as EntityKind;
    const graph = new Graph({
      container: box, width: box.clientWidth, height: box.clientHeight,
      padding: [40, 70, 50, 70], autoFit: "view", zoomRange: [.15, 4],
      animation: still ? false : { duration: 360, easing: "ease-out" },
      data: {
        nodes: data.nodes.map(n => ({ id: n.id, data: { ...n }, ...(restored?.nodes[n.id] ? { style: { x: restored.nodes[n.id][0], y: restored.nodes[n.id][1] } } : {}) })),
        edges: data.edges.map((e, i) => ({ id: `edge-${i}`, source: e.source, target: e.target, data: { ...e } })),
      },
      layout: restored ? undefined : { type: "force", preventOverlap: true, nodeSize: 120, linkDistance: 150, maxIteration: 350, gravity: 4, animation: false },
      node: {
        type: d => kindOf(d) === "author" ? "circle" : "rect",
        style: {
          size: d => ({ paper: [150, 48], keyword: [104, 30], venue: [96, 28], author: 14 } as const)[kindOf(d)] as number | [number, number],
          radius: (d: { data?: Record<string, unknown> }) => kindOf(d) === "keyword" ? 15 : kindOf(d) === "author" ? 7 : 6,
          fill: d => kindOf(d) === "author" ? KIND_COLOR.author : KIND_TINT[kindOf(d)],
          stroke: d => kindOf(d) === "author" ? "#ffffff" : kindOf(d) === "paper" ? "#cbd2dc" : KIND_COLOR[kindOf(d)],
          lineWidth: d => kindOf(d) === "author" ? 2 : 1,
          strokeOpacity: d => kindOf(d) === "keyword" || kindOf(d) === "venue" ? .45 : 1,
          shadowColor: "rgba(19, 32, 58, .14)",
          shadowBlur: d => kindOf(d) === "paper" ? 10 : 0,
          shadowOffsetY: d => kindOf(d) === "paper" ? 3 : 0,
          labelText: d => { const text = String(d.data?.label ?? ""); return text.length > 52 ? `${text.slice(0, 51)}…` : text; },
          labelPlacement: d => kindOf(d) === "author" ? "bottom" : "center",
          labelFontFamily: d => kindOf(d) === "paper" ? tokens.fonts.read : tokens.fonts.ui,
          labelFontSize: d => kindOf(d) === "paper" ? 12 : 11.5,
          labelFontWeight: d => kindOf(d) === "paper" ? 400 : 500,
          labelLineHeight: 15,
          labelFill: d => kindOf(d) === "paper" ? tokens.ink : KIND_COLOR[kindOf(d)],
          labelWordWrap: true, labelMaxWidth: d => kindOf(d) === "paper" ? 132 : 96, labelMaxLines: 2,
          labelBackground: d => kindOf(d) === "author", labelBackgroundFill: "#ffffff", labelBackgroundOpacity: .9, labelBackgroundRadius: 4,
          cursor: "pointer",
        },
        state: {
          selected: { stroke: tokens.ink, lineWidth: 1.5, strokeOpacity: 1, halo: true, haloStroke: tokens.brand, haloStrokeOpacity: .16, haloLineWidth: 12 },
          neighbor: { strokeOpacity: 1, lineWidth: 1.5 },
          dim: { opacity: .2 },
        },
      },
      edge: {
        type: "quadratic",
        style: {
          stroke: d => RELATIONS[String(d.data?.relation)]?.color ?? tokens.lineStrong,
          lineDash: d => RELATIONS[String(d.data?.relation)]?.dash ?? [],
          endArrow: d => d.data?.relation === "cites", endArrowSize: 7, curveOffset: 14,
          lineWidth: d => d.data?.relation === "cites" || d.data?.relation === "similar" ? 1.5 : 1,
          opacity: d => d.data?.relation === "cites" || d.data?.relation === "similar" ? .7 : .45,
        },
        state: { active: { opacity: 1, lineWidth: 2.2 }, dim: { opacity: .05 } },
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
  }, [data, scope, fitKey, resetKey, still]);

  useEffect(() => {
    const graph = instance.current;
    if (!graph || !ready) return;
    const adjacent = new Set([selected]);
    data.edges.filter(e => e.source === selected || e.target === selected).forEach(e => { adjacent.add(e.source); adjacent.add(e.target); });
    const states: Record<string, string[]> = {};
    data.nodes.forEach(n => { states[n.id] = !selected ? [] : n.id === selected ? ["selected"] : adjacent.has(n.id) ? ["neighbor"] : ["dim"]; });
    data.edges.forEach((e, i) => { states[`edge-${i}`] = !selected ? [] : e.source === selected || e.target === selected ? ["active"] : ["dim"]; });
    void graph.setElementState(states, !still).catch(() => undefined);
    if (selected && selected !== lastFocused.current && data.nodes.some(n => n.id === selected)) void graph.focusElement(selected, still ? false : { duration: 480, easing: "ease-out" }).catch(() => undefined);
    lastFocused.current = selected;
  }, [selected, data, ready, still]);

  return <div className="kg-explorer-canvas">
    <div className="kg-canvas-tools"><div>
      <Button size="small" disabled={!ready} aria-label="缩小图谱" onClick={() => { const g = instance.current; if (g) void g.zoomTo(Math.max(.15, g.getZoom() / 1.25)); }}>−</Button>
      <Button size="small" disabled={!ready} aria-label="放大图谱" onClick={() => { const g = instance.current; if (g) void g.zoomTo(Math.min(4, g.getZoom() * 1.25)); }}>＋</Button>
      <Button size="small" onClick={() => { cameras.delete(scope); setFitKey(k => k + 1); }}>适应画布</Button>
    </div></div>
    {failed ? <p className="kg-render-status" role="alert">图谱暂时无法绘制，可在右侧列表继续探索。<Button size="small" onClick={() => setFitKey(k => k + 1)}>重新绘制</Button></p> : !ready ? <p className="kg-render-status" role="status">正在排列节点…</p> : null}
    <div ref={container} className="kg-g6-canvas" aria-label="可拖动缩放的科研知识关系网络" />
  </div>;
}

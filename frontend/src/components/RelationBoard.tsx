import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Button } from "antd";
import { CloseOutlined, FullscreenOutlined, MinusOutlined, PlusOutlined, AimOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { buildSpaceGraph, type SpaceEdge, type SpaceGraph, type SpaceNode } from "../relationGraph";
import { CATEGORY_ORDER, metricText, type RelationCategory } from "../relationLayout";
import type { RecommendationItem, RelateResponse } from "../types";
import { paperLocation, workspacePaperPath } from "../workspace";

const META: Record<RelationCategory, { name: string; color: string; dash: number[] }> = {
  citation: { name: "引用关联", color: "#3867e8", dash: [] },
  semantic: { name: "主题相似", color: "#079888", dash: [7, 5] },
  keyword: { name: "关键词关联", color: "#d38116", dash: [2, 5] },
};
const ROOT_COLORS = ["#24479e", "#6a4bb0", "#a8344f", "#0d6b64", "#8a5a12"];
const DIAMETER = { center: 36, satellite: 13 };
type Camera = { x: number; y: number; k: number };

function frame(graph: SpaceGraph, width: number, height: number, floor: number): Camera {
  const xs = graph.nodes.map(n => n.x), ys = graph.nodes.map(n => n.y);
  const minX = Math.min(...xs) - 130, maxX = Math.max(...xs) + 130, minY = Math.min(...ys) - 60, maxY = Math.max(...ys) + 130;
  const k = Math.max(graph.nodes.filter(n => n.center).length > 5 ? 0.03 : floor, Math.min(1.1, (width - 48) / (maxX - minX), (height - 130) / (maxY - minY)));
  return { k, x: (width - (maxX - minX) * k) / 2 - minX * k, y: 52 + (height - 120 - (maxY - minY) * k) / 2 - minY * k };
}

export function RelationBoard({ data, workspaceId, from, busy, preparing, saving, error, libraryIds, onSave, onRemove }: {
  data: RelateResponse; workspaceId: string; from: string; busy: boolean; saving: boolean; error: string;
  preparing: boolean;
  libraryIds: string[];
  onSave: (item: RecommendationItem) => Promise<void>;
  onRemove: (id: string) => void;
}) {
  const graph = useMemo(() => buildSpaceGraph(data), [data]);
  const [active, setActive] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [categories, setCategories] = useState<RelationCategory[]>([...CATEGORY_ORDER]);
  const [camera, setCamera] = useState<Camera>({ x: 0, y: 0, k: 1 });
  const [size, setSize] = useState({ width: 1100, height: 700 });
  const [settling, setSettling] = useState(0);
  const [promoted, setPromoted] = useState<string[]>([]);
  const [layoutStamp, setLayoutStamp] = useState(graph);
  if (layoutStamp !== graph) {
    const before = new Map(layoutStamp.nodes.map(n => [n.id, n]));
    setPromoted(graph.nodes.filter(n => n.center && before.has(n.id) && !before.get(n.id)!.center).map(n => n.id));
    setLayoutStamp(graph);
    setSettling(t => t + 1);
  }
  const boardRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const drag = useRef<{ x: number; y: number; cx: number; cy: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(el);
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      setSettling(0);
      const rect = el.getBoundingClientRect(), c = cameraRef.current;
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      const k = Math.max(0.03, Math.min(2, c.k * Math.exp(-event.deltaY * 0.001)));
      setCamera({ x: x - (x - c.x) * k / c.k, y: y - (y - c.y) * k / c.k, k });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => { observer.disconnect(); el.removeEventListener("wheel", wheel); };
  }, []);
  useEffect(() => {
    if (!settling) return;
    const timer = window.setTimeout(() => { setSettling(0); setPromoted([]); }, 900);
    return () => window.clearTimeout(timer);
  }, [settling]);
  const signature = data.centers.map(c => c.id).join("|");
  useEffect(() => {
    setCamera(frame(graph, size.width, size.height, data.centers.length > 2 ? 0.4 : 0.55));
  }, [signature, graph.nodes.length, size.width, size.height]);
  const ease = (next: Camera) => { setSettling(t => t + 1); setCamera(next); };
  const zoom = (factor: number) => {
    const k = Math.max(0.03, Math.min(2, camera.k * factor));
    ease({ x: size.width / 2 - (size.width / 2 - camera.x) * k / camera.k, y: size.height / 2 - (size.height / 2 - camera.y) * k / camera.k, k });
  };

  const visibleEdges = graph.edges.filter(e => categories.includes(e.category));
  const visibleIds = new Set([...graph.nodes.filter(n => n.center).map(n => n.id), ...visibleEdges.flatMap(e => [e.source, e.target])]);
  const focusId = hovered ?? active;
  const connected = new Set(focusId ? visibleEdges.filter(e => e.source === focusId || e.target === focusId).flatMap(e => [e.source, e.target]) : []);
  const byId = new Map(graph.nodes.map(n => [n.id, n]));
  const colorOf = (n: SpaceNode) => n.center ? ROOT_COLORS[n.center.color_index % ROOT_COLORS.length] : META[n.categories[0] ?? "semantic"].color;
  const k = camera.k, inv = 1 / k, grow = Math.pow(k, -0.6);
  const radius = (n: SpaceNode) => (n.center ? DIAMETER.center : DIAMETER.satellite) / 2 * grow + 3 * inv;

  const panel = graph.nodes.find(n => n.id === active) ?? null;
  const lastPanel = useRef<SpaceNode | null>(null);
  const [leaving, setLeaving] = useState<SpaceNode | null>(null);
  useEffect(() => {
    if (panel) { lastPanel.current = panel; setLeaving(null); return; }
    const last = lastPanel.current;
    if (!last) return;
    lastPanel.current = null;
    setLeaving(last);
    const timer = window.setTimeout(() => setLeaving(null), 220);
    return () => window.clearTimeout(timer);
  }, [panel?.id]);
  const shown = panel ?? leaving;

  const drawEdge = (edge: SpaceEdge) => {
    const a = byId.get(edge.source), b = byId.get(edge.target);
    if (!a || !b) return null;
    const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1;
    const ra = radius(a), rb = radius(b);
    const mx = (a.x + b.x) / 2 - dy / length * edge.bend, my = (a.y + b.y) / 2 + dx / length * edge.bend;
    const ta = Math.hypot(mx - a.x, my - a.y) || 1, tb = Math.hypot(mx - b.x, my - b.y) || 1;
    const start = { x: a.x + (mx - a.x) / ta * ra, y: a.y + (my - a.y) / ta * ra };
    const end = { x: b.x + (mx - b.x) / tb * rb, y: b.y + (my - b.y) / tb * rb };
    const d = `M ${start.x} ${start.y} Q ${mx} ${my} ${end.x} ${end.y}`;
    const lit = Boolean(focusId && (edge.source === focusId || edge.target === focusId));
    const dim = Boolean(focusId) && !lit;
    const meta = META[edge.category];
    const width = (2 + Math.max(0, Math.min(1, edge.strength ?? 0)) * 1.1 + (lit ? 0.6 : 0)) * inv;
    return <g key={edge.id} className={`space-edge${dim ? " is-dim" : ""}${lit ? " is-lit" : ""}`} data-source={edge.source} data-target={edge.target} data-category={edge.category}
      style={{ "--edge-color": meta.color } as CSSProperties}>
      {lit ? <path className="space-edge-casing" d={d} fill="none" stroke="#fafbfd" strokeWidth={width + 5 * inv} strokeLinecap="round" /> : null}
      <path className="space-edge-line" d={d} style={{ d: `path("${d}")` } as CSSProperties} fill="none" stroke={meta.color} strokeWidth={width}
        strokeDasharray={meta.dash.length ? meta.dash.map(v => v * inv).join(" ") : undefined} strokeLinecap="round"
        />
      {lit ? <path key={active} className="space-edge-trace" d={d} pathLength={1} fill="none" stroke={meta.color} strokeWidth={width + inv} strokeLinecap="round" /> : null}
      <path d={d} fill="none" stroke="transparent" strokeWidth={14 * inv} pointerEvents="stroke" className="space-edge-hit"
        onClick={() => setActive(b.center && !a.center ? a.id : b.id)}><title>{meta.name} · {metricText(edge.item, edge.category)}</title></path>
    </g>;
  };

  const renderPanel = (node: SpaceNode) => {
    const saved = [node.id, ...(node.item?.aliases ?? [])].some(id => libraryIds.includes(id));
    const relations = graph.edges.filter(e => e.source === node.id || e.target === node.id);
    const kind = node.center ? `主论文 ${data.centers.indexOf(node.center) + 1}` : node.owners.length > 1 ? `${node.owners.length} 篇主论文共享` : META[node.categories[0] ?? "semantic"].name;
    return <div className="space-panel-body" key={node.id}>
      <span className="space-panel-kind" style={{ "--node-color": colorOf(node) } as CSSProperties}><i />{kind}</span>
      <h2>{node.title}</h2>
      <p className="space-panel-meta">{node.year ?? "年份未知"} · {(node.center?.authors ?? node.item?.authors ?? []).slice(0, 3).join("，") || "作者未知"}</p>
      {!node.center && node.item ? <div className="space-actions">
        <Button type="primary" loading={saving} disabled={busy || saved} onClick={() => void onSave(node.item!)}>{saved ? "已在文献库" : "加入文献库"}</Button>
      </div> : <Button className="space-collapse" onClick={() => onRemove(node.id)} disabled={busy || saving}>收起这篇主论文</Button>}
      {error ? <p className="space-action-error" role="alert">{error}</p> : null}
      {relations.length ? <><h3>关联依据</h3><div className="space-evidence">{relations.map(edge => {
        const otherId = edge.source === node.id ? edge.target : edge.source;
        const meta = META[edge.category];
        return <div key={edge.id}>
          <span className="space-evidence-kind" style={{ color: meta.color }}>
            <svg width="18" height="6" aria-hidden="true"><line x1="1" y1="3" x2="17" y2="3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeDasharray={meta.dash.join(" ") || undefined} /></svg>
            {meta.name} · {metricText(edge.item, edge.category)}</span>
          <button onClick={() => setActive(otherId)}>{byId.get(otherId)?.title}</button>
          {edge.item.reasons.filter(r => r.type === edge.category || (edge.category === "citation" && ["cites", "bibliographic_coupling", "co_citation"].includes(r.type))).map(r => <p key={r.type + r.text}>{r.text}</p>)}
        </div>;
      })}</div></> : null}
      {node.item?.abstract ? <><h3>摘要</h3><p className="space-abstract">{node.item.abstract}</p></> : null}
      <div className="space-panel-links"><Link to={node.center ? workspacePaperPath(workspaceId, node.center.local_id ?? node.id, from) : paperLocation(node.item!.id, from)}>查看论文详情</Link>{node.item?.url ? <a href={node.item.url} target="_blank" rel="noreferrer">原文链接 ↗</a> : null}</div>
    </div>;
  };

  const graphIssues = data.satellites.filter(g => g.status === "unavailable");
  const hoverNode = graph.nodes.find(n => n.id === hovered);
  const tooltipX = hoverNode ? Math.max(12, Math.min(size.width - Math.min(350, size.width - 24) - 12, hoverNode.x * k + camera.x + 22)) : 12;
  const tooltipY = hoverNode ? Math.max(52, Math.min(size.height - 150, hoverNode.y * k + camera.y - 30)) : 52;
  return <section className={`space-board${settling ? " is-settling" : ""}${shown ? " has-panel" : ""}`} ref={boardRef} aria-label="论文空间关系网">
    <div className="space-heading">
      <strong>{data.centers.length} 篇主论文</strong><span>{graph.nodes.filter(n => !n.center).length} 篇关联论文</span>
      {busy || preparing ? <span className="space-loading" role="status">{preparing ? "后台正在准备关联" : "正在读取关系"}</span> : null}
    </div>
    <div className="space-controls" aria-label="视图控制">
      <button aria-label="放大" title="放大" onClick={() => zoom(1.25)}><PlusOutlined /></button>
      <button aria-label="缩小" title="缩小" onClick={() => zoom(1 / 1.25)}><MinusOutlined /></button>
      <button aria-label="查看整个关系网" title="查看整个关系网" onClick={() => ease(frame(graph, size.width, size.height, 0.25))}><AimOutlined /></button>
      <button aria-label="全屏" title="全屏" onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void boardRef.current?.requestFullscreen(); }}><FullscreenOutlined /></button>
    </div>
    <div ref={canvasRef} className={`space-canvas${dragging ? " is-dragging" : ""}`} aria-label="拖动画布探索论文，滚轮缩放"
      style={{ backgroundSize: `${28 * k}px ${28 * k}px`, backgroundPosition: `${camera.x}px ${camera.y}px` }}
      onPointerDown={e => {
        if ((e.target as Element).closest("[data-node], .space-edge-hit")) return;
        setSettling(0);
        drag.current = { x: e.clientX, y: e.clientY, cx: camera.x, cy: camera.y, moved: false };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={e => {
        const start = drag.current; if (!start) return;
        if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > 4) { start.moved = true; setDragging(true); }
        if (start.moved) setCamera(c => ({ ...c, x: start.cx + e.clientX - start.x, y: start.cy + e.clientY - start.y }));
      }}
      onPointerUp={() => { if (drag.current && !drag.current.moved) setActive(null); drag.current = null; setDragging(false); }}
      onPointerCancel={() => { drag.current = null; setDragging(false); }}>
      <div className="space-world" style={{ width: graph.width, height: graph.height,
        transform: `translate(${camera.x}px, ${camera.y}px) scale(${k})`, "--inv": inv, "--grow": grow } as CSSProperties}>
        <svg className="space-lines" width={graph.width} height={graph.height} aria-label="论文之间的真实关系">
          <defs>{graph.nodes.filter(n => n.center).map(n => <radialGradient key={n.id} id={`space-zone-${n.center!.color_index}`}>
            <stop offset="0%" stopColor={colorOf(n)} stopOpacity={0.13} /><stop offset="100%" stopColor={colorOf(n)} stopOpacity={0} /></radialGradient>)}</defs>
          {graph.nodes.filter(n => n.center).map(n => {
            const own = graph.nodes.filter(s => !s.center && s.owners.length === 1 && s.owners[0] === n.id && visibleIds.has(s.id));
            const r = Math.max(170, ...own.map(s => Math.hypot(s.x - n.x, s.y - n.y) + 90));
            return <circle key={n.id} className={`space-zone${focusId && focusId !== n.id && !connected.has(n.id) ? " is-dim" : ""}`} cx={n.x} cy={n.y} r={r} fill={`url(#space-zone-${n.center!.color_index})`} />;
          })}
          {visibleEdges.filter(e => !(focusId && (e.source === focusId || e.target === focusId))).map(drawEdge)}
          {focusId ? visibleEdges.filter(e => e.source === focusId || e.target === focusId).map(drawEdge) : null}
        </svg>
        {graph.nodes.filter(n => visibleIds.has(n.id)).map(node => {
          const color = colorOf(node);
          const owners = node.owners.map(id => byId.get(id)).filter((n): n is SpaceNode => Boolean(n));
          const origin = !node.center && owners[0] ? { x: owners[0].x - node.x, y: owners[0].y - node.y } : { x: 0, y: 0 };
          const shared = !node.center && node.owners.length > 1;
          const ring = shared ? `conic-gradient(${owners.map((o, i) => `${colorOf(o)} ${i / owners.length * 100}% ${(i + 1) / owners.length * 100}%`).join(", ")})` : undefined;
          const label = Math.max(130, Math.min(node.center ? 232 : 188, (node.center ? 260 : 220) * k));
          const diameter = node.center ? DIAMETER.center : DIAMETER.satellite;
          return <button key={node.id} type="button" data-node={node.id} data-center={Boolean(node.center)}
            className={`space-node${node.center ? " is-center" : ""}${shared ? " is-shared" : ""}${active === node.id ? " is-active" : ""}${focusId && !connected.has(node.id) && focusId !== node.id ? " is-dim" : ""}${promoted.includes(node.id) ? " is-promoted" : ""}`}
            style={{ left: node.x, top: node.y, "--node-color": color, "--d": `${diameter}px`, "--label-width": `${label}px`, "--fx": `${origin.x}px`, "--fy": `${origin.y}px`,
              "--delay": `${node.center ? 0 : 80 + CATEGORY_ORDER.indexOf(node.categories[0] ?? "semantic") * 60}ms`, ...(ring ? { "--ring": ring } : {}) } as CSSProperties}
            onClick={() => { setHovered(null); setActive(node.id); }}
            onPointerEnter={() => setHovered(node.id)} onPointerLeave={() => setHovered(null)}
            onFocus={() => setHovered(node.id)} onBlur={() => setHovered(null)}
            aria-describedby={hovered === node.id ? "space-title-tooltip" : undefined}
            aria-label={`${node.center ? "主论文" : "关联论文"}：${node.title}`} aria-pressed={active === node.id}>
            <span className="space-node-inner">
              <span className="space-orb">{node.center ? <span>{data.centers.indexOf(node.center) + 1}</span> : null}</span>
              <span className="space-node-label"><span className="space-node-title">{node.title}</span>
                <span className="space-node-meta">{node.year ?? "年份未知"}{shared ? ` · ${node.owners.length} 篇共享` : ""}</span></span>
            </span>
          </button>;
        })}
      </div>
    </div>
    {hoverNode ? <div id="space-title-tooltip" role="tooltip" className="space-tooltip" style={{ left: tooltipX, top: tooltipY }}>
      <span>{hoverNode.center ? "主论文" : hoverNode.owners.length > 1 ? "共享关联论文" : "关联论文"} {hoverNode.year ?? ""}</span>
      <strong>{hoverNode.title}</strong>
    </div> : null}
    <div className="space-legend" aria-label="筛选关系类型">{CATEGORY_ORDER.map(key => <button key={key} aria-pressed={categories.includes(key)} onClick={() => setCategories(current => current.includes(key) ? current.filter(c => c !== key) : [...current, key])}>
      <svg width="22" height="8" aria-hidden="true"><line x1="1" y1="4" x2="21" y2="4" stroke={META[key].color} strokeWidth="2" strokeLinecap="round" strokeDasharray={META[key].dash.join(" ") || undefined} /></svg>{META[key].name}</button>)}
      <span>每篇主论文每类取 1 篇</span></div>
    {graphIssues.length ? <div className="space-hint" role="status">部分关系仍待获取，可刷新重试</div> : null}
    {shown ? <aside className={`space-panel${panel ? "" : " is-leaving"}`} aria-label="论文关系详情" aria-hidden={panel ? undefined : true}>
      <button className="space-panel-close" aria-label="关闭详情" onClick={() => setActive(null)}><CloseOutlined /></button>
      {renderPanel(shown)}
    </aside> : null}
  </section>;
}

import { useEffect, useRef, useState } from "react";
import { Graph, NodeEvent, type IElementEvent } from "@antv/g6";
import { AimOutlined, CloseOutlined, MinusOutlined, PlusOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { EDGE_COLOR, PLANET_COLORS, SATELLITE_COLOR, edgeDash, planetPositions } from "../cosmosLayout";
import type { RecommendationItem, RelateCenter, RelateResponse } from "../types";
import { paperLocation, workspacePaperPath } from "../workspace";
import "../styles/cosmos.css";

const CATEGORY_LABELS: Record<string, string> = { citation: "引用关联", semantic: "主题相似", keyword: "关键词关联" };
const CATEGORY_SCORE: Record<string, string> = { citation: "citation_score", semantic: "semantic_score", keyword: "keyword_score" };

function shortTitle(title: string) {
  const chars = Array.from(title);
  return chars.length > 18 ? `${chars.slice(0, 18).join("")}…` : title;
}

function edgeTypeOf(item: RecommendationItem, category: string) {
  if (category === "citation") return item.relations?.includes("cites") ? "cites" : "citation";
  if (category === "semantic") return "similar";
  return "keyword";
}

type SatelliteEdge = {
  id: string;
  source: string;
  target: string;
  category: string;
  type: string;
  label: string;
  directed: boolean;
  strength: number | null;
  sourceColor: string;
};

export function CosmosGraph({ data, workspaceId, from }: { data: RelateResponse; workspaceId: string; from: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const [failed, setFailed] = useState(false);
  const [panel, setPanel] = useState<{ item: RecommendationItem; categories: string[] } | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const container = ref.current;
    if (!container || !data.centers.length) return;
    setFailed(false);
    setPanel(null);
    let alive = true;
    let graph: Graph;

    const sims = data.centers.map((center) => data.centers.map((other) => center.similarities[other.id] ?? null));
    const positions = planetPositions(sims);
    const colorOf = new Map(data.centers.map((center) => [center.id, PLANET_COLORS[center.color_index] ?? PLANET_COLORS[0]]));

    const nodes: { id: string; fx?: number; fy?: number; data: Record<string, unknown> }[] = data.centers.map((center, index) => ({
      id: center.id,
      fx: positions[index].x,
      fy: positions[index].y,
      data: { kind: "planet", center, color: colorOf.get(center.id), label: shortTitle(center.title) },
    }));
    const satelliteIds = new Set<string>();
    const edges: SatelliteEdge[] = [];
    for (const group of data.satellites) {
      const sourceColor = colorOf.get(group.center_id) ?? EDGE_COLOR;
      for (const item of group.items) {
        if (!satelliteIds.has(item.id)) {
          satelliteIds.add(item.id);
          nodes.push({ id: item.id, data: { kind: "satellite", item, label: shortTitle(item.title) } });
        }
        const type = edgeTypeOf(item, group.category);
        edges.push({
          id: `${group.center_id}>${item.id}:${group.category}:${type}`,
          source: group.center_id, target: item.id, category: group.category, type,
          label: group.category === "citation" && type === "cites" ? "引用" : CATEGORY_LABELS[group.category] ?? group.category,
          directed: type === "cites",
          strength: (item as unknown as Record<string, number | null>)[CATEGORY_SCORE[group.category]] ?? null,
          sourceColor,
        });
      }
    }
    for (const centerEdge of data.center_edges) {
      edges.push({
        id: `center>${centerEdge.source}>${centerEdge.target}:${centerEdge.category}`,
        source: centerEdge.source, target: centerEdge.target, category: centerEdge.category,
        type: centerEdge.type, label: centerEdge.label, directed: centerEdge.directed,
        strength: centerEdge.strength ?? null,
        sourceColor: colorOf.get(centerEdge.source) ?? EDGE_COLOR,
      });
    }

    const adjacency = new Map<string, Set<string>>();
    const link = (a: string, b: string) => {
      if (!adjacency.has(a)) adjacency.set(a, new Set());
      adjacency.get(a)!.add(b);
    };
    for (const edge of edges) {
      link(edge.source, edge.target);
      link(edge.target, edge.source);
    }

    try {
      graph = new Graph({
        container,
        autoFit: "view",
        padding: 48,
        data: {
          nodes,
          edges: edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, data: edge })),
        },
        node: {
          type: "circle",
          style: {
            size: (datum) => (datum.data?.kind === "planet" ? 56 : 22),
            fill: (datum) => (datum.data?.kind === "planet" ? String(datum.data.color) : SATELLITE_COLOR),
            stroke: (datum) => (datum.data?.kind === "planet" ? String(datum.data.color) : "#475569"),
            lineWidth: (datum) => (datum.data?.kind === "planet" ? 2 : 1),
            halo: (datum) => datum.data?.kind === "planet",
            haloStroke: (datum) => String(datum.data?.color ?? ""),
            haloOpacity: 0.28,
            haloLineWidth: 14,
            cursor: "pointer",
            labelText: (datum) => String(datum.data?.label ?? ""),
            labelPlacement: "bottom",
            labelFill: (datum) => (datum.data?.kind === "planet" ? "#FFFFFF" : "#94A3B8"),
            labelFontSize: (datum) => (datum.data?.kind === "planet" ? 13 : 11),
            labelFontWeight: (datum) => (datum.data?.kind === "planet" ? 600 : 400),
            labelMaxWidth: 130,
            labelWordWrap: true,
            labelMaxLines: 2,
          },
          state: {
            active: { labelFill: "#FFFFFF" },
            inactive: { opacity: 0.15 },
          },
        },
        edge: {
          type: "line",
          style: {
            stroke: EDGE_COLOR,
            strokeOpacity: (datum) => 0.35 + Math.min(0.5, (Number(datum.data?.strength) || 0) * 0.5),
            lineWidth: (datum) => 1 + 1.5 * Math.min(1, Number(datum.data?.strength) || 0.3),
            lineDash: (datum) => edgeDash(String(datum.data?.category ?? "")),
            endArrow: (datum) => Boolean(datum.data?.directed),
          },
          state: {
            active: { stroke: (datum) => String(datum.data?.sourceColor ?? EDGE_COLOR), strokeOpacity: 0.95, lineWidth: 2 },
            inactive: { opacity: 0.05 },
          },
        },
        layout: { type: "force", preventOverlap: true, nodeSize: 110, linkDistance: 180 },
        behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
      });
    } catch {
      setFailed(true);
      return;
    }
    graphRef.current = graph;

    const highlight = (nodeId: string | null) => {
      const nodeState: Record<string, string[]> = {};
      const edgeState: Record<string, string[]> = {};
      const neighborSet = nodeId ? adjacency.get(nodeId) ?? new Set<string>() : new Set<string>();
      for (const node of nodes) {
        nodeState[node.id] = !nodeId ? [] : node.id === nodeId || neighborSet.has(node.id) ? ["active"] : ["inactive"];
      }
      for (const edge of edges) {
        edgeState[edge.id] = !nodeId ? [] : edge.source === nodeId || edge.target === nodeId ? ["active"] : ["inactive"];
      }
      void graph.setElementState({ ...nodeState, ...edgeState }, false).catch(() => undefined);
    };

    let rendered = false;
    let settled = false;
    graph.render().then(() => {
      settled = true;
      if (!alive) { graph.destroy(); return; }
      rendered = true;
    }).catch(() => {
      settled = true;
      if (!alive) { graph.destroy(); return; }
      setFailed(true);
    });
    const resize = new ResizeObserver(() => {
      if (alive && rendered && container.clientWidth > 0) {
        graph.resize(container.clientWidth, container.clientHeight);
        void graph.fitView().catch(() => undefined);
      }
    });
    resize.observe(container);
    graph.on(NodeEvent.POINTER_ENTER, (event) => {
      const enter = event as IElementEvent;
      if (enter.targetType === "node") highlight(String(enter.target.id));
    });
    graph.on(NodeEvent.POINTER_LEAVE, () => highlight(null));
    graph.on(NodeEvent.CLICK, (event) => {
      const click = event as IElementEvent;
      if (click.targetType !== "node") return;
      const datum = nodes.find((node) => node.id === click.target.id)?.data as { kind: string; center?: RelateCenter; item?: RecommendationItem } | undefined;
      if (!datum) return;
      if (datum.kind === "planet" && datum.center) {
        navigate(workspacePaperPath(workspaceId, datum.center.local_id ?? datum.center.id, from));
        return;
      }
      const categories = data.satellites.filter((group) => group.items.some((item) => item.id === click.target.id)).map((group) => group.category);
      if (datum.item) setPanel({ item: datum.item, categories });
    });
    return () => {
      alive = false;
      resize.disconnect();
      graphRef.current = null;
      if (settled) graph.destroy();
    };
  }, [data, workspaceId, from, navigate]);

  if (!data.centers.length) return null;

  const reasons = panel ? [...new Map(panel.item.reasons.map((reason) => [reason.text, reason])).values()] : [];
  return (
    <section className="cosmos-graph-wrap" aria-label="论文关系宇宙图谱">
      <div ref={ref} className="cosmos-graph-canvas" hidden={failed} />
      {failed ? <p className="cosmos-fallback">关系图渲染失败，请刷新重试。</p> : null}
      <div className="cosmos-toolbar" aria-label="图谱控制">
        <button aria-label="放大" onClick={() => void graphRef.current?.zoomBy(1.2).catch(() => undefined)}><PlusOutlined /></button>
        <button aria-label="缩小" onClick={() => void graphRef.current?.zoomBy(0.8).catch(() => undefined)}><MinusOutlined /></button>
        <button aria-label="适配视图" onClick={() => void graphRef.current?.fitView().catch(() => undefined)}><AimOutlined /></button>
      </div>
      <div className="cosmos-legend" aria-label="图例">
        <div className="cosmos-legend-title">关系类型</div>
        {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
          <div key={key} className="cosmos-legend-row">
            <svg width="34" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="34" y2="4" stroke="#64748B" strokeWidth="1.6"
                strokeDasharray={key === "semantic" ? "5 4" : key === "keyword" ? "2 4" : undefined} />
              {key === "citation" ? <path d="M 28 1 L 34 4 L 28 7" fill="none" stroke="#64748B" strokeWidth="1.4" /> : null}
            </svg>
            <span>{label}</span>
          </div>
        ))}
        <div className="cosmos-legend-title">核心论文</div>
        {data.centers.map((center) => (
          <div key={center.id} className="cosmos-legend-row">
            <i className="cosmos-legend-dot" style={{ background: PLANET_COLORS[center.color_index] }} />
            <span>{center.title}</span>
          </div>
        ))}
      </div>
      {panel ? (
        <aside className="cosmos-panel" aria-label="相关论文详情">
          <button className="cosmos-panel-close" aria-label="关闭详情" onClick={() => setPanel(null)}><CloseOutlined /></button>
          <div className="cosmos-panel-tags">
            {panel.categories.map((category) => <span key={category}>{CATEGORY_LABELS[category] ?? category}</span>)}
          </div>
          <h2>{panel.item.title}</h2>
          <p className="cosmos-panel-meta">{panel.item.year ?? "年份未知"} · {panel.item.authors?.slice(0, 3).join(" · ") || "作者未知"}</p>
          {panel.categories.some((category) => CATEGORY_SCORE[category]) ? (
            <div className="cosmos-panel-scores">
              {panel.categories.map((category) => (
                <div key={category}>
                  <span>{CATEGORY_LABELS[category]}</span>
                  <strong>{String(panel.item[CATEGORY_SCORE[category] as keyof RecommendationItem] ?? "—")}</strong>
                </div>
              ))}
            </div>
          ) : null}
          {reasons.length ? <><h3>为什么关联？</h3><ul>{reasons.map((reason) => <li key={reason.type + reason.text}>{reason.text}</li>)}</ul></> : null}
          {panel.item.shared_keywords?.length ? (
            <div className="cosmos-panel-keywords">{panel.item.shared_keywords.map((term) => <span key={term}>{term}</span>)}</div>
          ) : null}
          <Link className="cosmos-panel-open" to={paperLocation(panel.item.id, from)}>查看论文详情 <span>↗</span></Link>
        </aside>
      ) : null}
    </section>
  );
}

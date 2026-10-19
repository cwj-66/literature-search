import { useEffect, useRef, useState } from "react";
import { Graph, NodeEvent, type IElementEvent } from "@antv/g6";
import type { PaperNetwork } from "../types";
import { tokens } from "../theme";

function shortTitle(title: string) {
  const chars = Array.from(title);
  return chars.length > 18 ? `${chars.slice(0, 18).join("")}…` : title;
}

export function RelationGraph({ data, onOpen }: { data: PaperNetwork; onOpen: (id: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const open = useRef(onOpen);
  open.current = onOpen;

  useEffect(() => {
    const container = ref.current;
    if (!container || !data.nodes.length) return;
    setFailed(false);
    let alive = true;
    let graph: Graph;
    try {
      graph = new Graph({
        container,
        autoFit: "view",
        padding: 28,
        data: {
          nodes: data.nodes.map((node) => ({
            id: node.id,
            data: { ...node, label: node.center ? "当前论文" : shortTitle(node.title) },
          })),
          edges: data.edges.map((edge) => ({
            id: `${edge.type}:${edge.source}>${edge.target}`,
            source: edge.source,
            target: edge.target,
            data: edge,
          })),
        },
        node: {
          type: "circle",
          style: {
            size: (datum) => (datum.data?.center ? 44 : 28),
            fill: (datum) => (datum.data?.center ? tokens.brand : tokens.brandSoft),
            stroke: tokens.brand,
            lineWidth: 1.5,
            labelText: (datum) => String(datum.data?.label ?? ""),
            labelPlacement: "bottom",
            labelFill: tokens.text,
            labelFontSize: 12,
            labelMaxWidth: 128,
            labelWordWrap: true,
            labelMaxLines: 3,
            cursor: "pointer",
          },
        },
        edge: {
          type: (datum) => (datum.data?.type === "cites" ? "line" : "quadratic"),
          style: {
            stroke: (datum) => (datum.data?.type === "cites" ? tokens.brand : datum.data?.type === "keyword" ? "#16845b" : datum.data?.type === "similar" ? "#9a9d9f" : "#8956c7"),
            lineWidth: (datum) => 1 + 2 * Math.max(0, Math.min(1, Number(datum.data?.strength ?? 0.25))),
            curveOffset: (datum: { data?: Record<string, unknown> }) => datum.data?.type === "keyword" ? -35 : datum.data?.type === "similar" ? 35 : 65,
            lineDash: (datum) => (datum.data?.type === "cites" ? [] : datum.data?.type === "keyword" ? [2, 4] : [5, 4]),
            endArrow: (datum) => Boolean(datum.data?.directed),
            labelText: (datum) => String(datum.data?.label ?? ""),
            labelFontSize: 12,
            labelFill: tokens.muted,
            labelBackground: true,
            labelBackgroundFill: "#ffffff",
          },
        },
        layout: {
          type: "radial",
          unitRadius: 180,
          linkDistance: 190,
          preventOverlap: true,
          nodeSize: 90,
        },
        behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
      });
    } catch {
      setFailed(true);
      return;
    }
    let rendered = false;
    let settled = false;
    graph.render().then(() => {
      settled = true;
      if (!alive) { graph.destroy(); return; }
      rendered = true;
    }).catch(() => {
      settled = true;
      if (!alive) { graph.destroy(); return; }
      if (alive) setFailed(true);
    });
    const resize = new ResizeObserver(() => {
      if (alive && rendered && container.clientWidth > 0) {
        graph.resize(container.clientWidth, container.clientHeight);
        void graph.fitView().catch(() => undefined);
      }
    });
    resize.observe(container);
    graph.on(NodeEvent.CLICK, (event) => {
      const click = event as IElementEvent;
      if (click.targetType !== "node") return;
      const id = click.target.id;
      if (id) open.current(id);
    });
    return () => {
      alive = false;
      resize.disconnect();
      if (settled) graph.destroy();
    };
  }, [data]);

  return (
    <>
      {failed ? <p className="muted">关系图渲染失败，仍可从列表打开论文。</p> : null}
      <div ref={ref} className="network-canvas" hidden={failed} aria-label="论文关系网络" />
    </>
  );
}

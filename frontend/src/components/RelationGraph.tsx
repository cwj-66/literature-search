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
            fill: (datum) => (datum.data?.center ? tokens.accent : tokens.accentSoft),
            stroke: tokens.accent,
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
          type: (datum) => (datum.data?.type === "similar" ? "quadratic" : "line"),
          style: {
            stroke: (datum) => (datum.data?.type === "cites" ? tokens.accent : "#9a9d9f"),
            lineWidth: 1.5,
            lineDash: (datum) => (datum.data?.type === "similar" ? [5, 4] : []),
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

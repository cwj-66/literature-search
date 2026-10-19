import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

type Chart = ReturnType<typeof echarts.init>;

// Thin imperative wrapper around echarts: owns init/dispose, re-renders on
// option identity change, and forwards DOM events through echarts' own handler.
export function EChart({ option, height, ariaLabel, onClick }: {
  option: EChartsOption; height: number; ariaLabel: string;
  onClick?: (params: { name?: string }) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el);
    return () => { observer.disconnect(); chart.dispose(); chartRef.current = null; };
  }, []);
  useEffect(() => { chartRef.current?.setOption(option, { notMerge: true }); }, [option]);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    chart.on("click", onClick);
    return () => { chart.off("click", onClick); };
  }, [onClick]);
  return <div ref={containerRef} className="kg-echart" role="img" aria-label={ariaLabel} style={{ height }} />;
}

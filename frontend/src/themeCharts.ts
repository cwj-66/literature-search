import type { EChartsOption } from "echarts";
import { tokens } from "./theme";
import type { KnowledgeTheme, ThemeEvolutionLink, ThemeQuadrant } from "./types";

export const QUADRANT_META: Record<ThemeQuadrant, { label: string; color: string; hint: string }> = {
  motor: { label: "当前热点", color: tokens.vizRose, hint: "高中心度 · 高密集度" },
  niche: { label: "专业主题", color: tokens.placeholder, hint: "低中心度 · 高密集度" },
  basic: { label: "基础主题", color: tokens.vizBlue, hint: "高中心度 · 低密集度" },
  emerging: { label: "新兴或衰退", color: tokens.vizGreen, hint: "低中心度 · 低密集度" },
};

export const VIZ_PALETTE = [tokens.vizBlue, tokens.vizTeal, tokens.vizAmber, tokens.vizRose, tokens.vizViolet, tokens.vizGreen];

const TOOLTIP = {
  backgroundColor: "#ffffff",
  borderColor: tokens.line,
  borderWidth: 1,
  padding: [10, 12],
  textStyle: { color: tokens.text, fontSize: 12, fontFamily: tokens.fonts.ui },
  extraCssText: `border-radius:${tokens.radius.md}px;box-shadow:${tokens.shadow.md};line-height:1.7;max-width:320px;white-space:normal;`,
};
const BASE = {
  textStyle: { fontFamily: tokens.fonts.ui, color: tokens.text },
  animationDuration: 700,
  animationEasing: "cubicOut" as const,
  animationDurationUpdate: 420,
  animationEasingUpdate: "cubicOut" as const,
};
const AXIS_NAME = { color: tokens.muted, fontSize: 11 };

export function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function themeTotal(theme: KnowledgeTheme): number {
  return Object.values(theme.by_year).reduce((sum, count) => sum + count, 0);
}

export function themeYearCount(theme: KnowledgeTheme, year: number): number {
  return theme.by_year[String(year)] ?? 0;
}

/** 图一：研究热点四象限（bibliometrix thematicMap 风格）。x=centrality, y=density, 中位数十字分割。 */
export function quadrantOption(themes: KnowledgeTheme[]): EChartsOption {
  const medX = median(themes.map(t => t.centrality));
  const medY = median(themes.map(t => t.density));
  const maxX = Math.max(0.2, ...themes.map(t => t.centrality)) * 1.15 || 1;
  const maxY = Math.max(0.2, ...themes.map(t => t.density)) * 1.15 || 1;
  const maxSize = Math.max(1, ...themes.map(t => t.size));
  const byName = new Map(themes.map(t => [t.name, t]));
  const area = (x1: number, y1: number, x2: number, y2: number, meta: (typeof QUADRANT_META)[ThemeQuadrant]) => {
    const corner = { name: meta.label, xAxis: x1, yAxis: y1, itemStyle: { color: meta.color, opacity: 0.045 }, label: { show: true, position: "insideTopRight" as const, distance: 10, color: meta.color, fontSize: 12, fontWeight: 600 as const, lineHeight: 17, formatter: `${meta.label}\n{hint|${meta.hint}}`, rich: { hint: { color: tokens.muted, fontSize: 11, fontWeight: 400 as const } } } };
    const opposite = { xAxis: x2, yAxis: y2 };
    return [corner, opposite] as [typeof corner, typeof opposite];
  };
  return {
    ...BASE,
    grid: { left: 64, right: 28, top: 32, bottom: 52 },
    tooltip: {
      ...TOOLTIP,
      trigger: "item",
      formatter: params => {
        const name = (params as { name?: string }).name ?? "";
        const theme = byName.get(name);
        if (!theme) return name;
        const meta = theme.quadrant ? QUADRANT_META[theme.quadrant] : { label: "未划分", color: tokens.placeholder, hint: "" };
        const lines = [
          `<strong>${theme.name}</strong>（${meta.label}）`,
          `论文 ${theme.size} 篇 · 中心度 ${theme.centrality.toFixed(2)} · 密集度 ${theme.density.toFixed(2)}`,
        ];
        if (theme.description) lines.push(theme.description);
        if (theme.keywords.length) lines.push(`关键词：${theme.keywords.join("、")}`);
        return lines.join("<br/>");
      },
    },
    xAxis: { name: "中心度 centrality", nameLocation: "middle", nameGap: 30, nameTextStyle: AXIS_NAME, min: 0, max: maxX, splitLine: { lineStyle: { color: tokens.line, type: [2, 4] } }, axisLine: { lineStyle: { color: tokens.lineStrong } }, axisTick: { show: false }, axisLabel: { color: tokens.faint, fontSize: 11 } },
    yAxis: { name: "密集度 density", nameGap: 42, nameTextStyle: AXIS_NAME, min: 0, max: maxY, splitLine: { lineStyle: { color: tokens.line, type: [2, 4] } }, axisLine: { show: true, lineStyle: { color: tokens.lineStrong } }, axisTick: { show: false }, axisLabel: { color: tokens.faint, fontSize: 11 } },
    series: [{
      type: "scatter",
      data: themes.map(theme => ({
        name: theme.name,
        value: [theme.centrality, theme.density],
        symbolSize: 12 + 30 * Math.sqrt(theme.size / maxSize),
        itemStyle: { color: theme.quadrant ? QUADRANT_META[theme.quadrant].color : tokens.placeholder, opacity: 0.82, borderColor: "#ffffff", borderWidth: 1.5 },
      })),
      label: { show: true, position: "right", distance: 6, color: tokens.text, fontSize: 11, formatter: "{b}" },
      labelLayout: { hideOverlap: true },
      emphasis: { scale: 1.12, focus: "self", label: { color: tokens.ink, fontWeight: 600 } },
      animationDelay: (index: number) => index * 30,
      markLine: {
        silent: true, symbol: "none",
        lineStyle: { color: tokens.lineStrong, type: "dashed" },
        label: { show: false },
        data: [{ xAxis: medX }, { yAxis: medY }],
      },
      markArea: {
        silent: true,
        data: [
          area(medX, medY, maxX, maxY, QUADRANT_META.motor),
          area(0, medY, medX, maxY, QUADRANT_META.niche),
          area(medX, 0, maxX, medY, QUADRANT_META.basic),
          area(0, 0, medX, medY, QUADRANT_META.emerging),
        ],
      },
    }],
  };
}

const RIVER_MAX_THEMES = 12;

/** 图二：主题河流图。按年份展示各主题论文数量的演化。 */
export function themeRiverOption(themes: KnowledgeTheme[], years: number[]): EChartsOption {
  const ranked = [...themes].sort((a, b) => themeTotal(b) - themeTotal(a)).slice(0, RIVER_MAX_THEMES);
  const data = ranked.flatMap(theme => years.map(year => [String(year), themeYearCount(theme, year), theme.name] as [string, number, string]));
  return {
    ...BASE,
    color: VIZ_PALETTE,
    tooltip: { ...TOOLTIP, trigger: "axis", axisPointer: { type: "line", lineStyle: { color: tokens.ink, opacity: .35 } } },
    legend: { type: "scroll", bottom: 0, icon: "roundRect", itemWidth: 12, itemHeight: 8, itemGap: 14, textStyle: { color: tokens.muted, fontSize: 11 }, pageTextStyle: { color: tokens.muted } },
    singleAxis: {
      type: "time", top: 24, bottom: 52, left: 44, right: 24,
      axisLabel: { color: tokens.faint, formatter: "{yyyy}", fontSize: 11 },
      axisLine: { lineStyle: { color: tokens.lineStrong } },
      axisTick: { lineStyle: { color: tokens.lineStrong } },
      splitLine: { show: true, lineStyle: { color: tokens.line, type: [2, 4] } },
    },
    series: [{
      type: "themeRiver",
      itemStyle: { opacity: .88 },
      emphasis: { focus: "series", itemStyle: { opacity: 1 } },
      label: { show: false },
      data,
    }],
  };
}

/** 图三：主题演化桑基图（bibliometrix thematicEvolution 风格）。列=时期，节点标签用 from_label/to_label。 */
export function sankeyOption(links: ThemeEvolutionLink[]): EChartsOption {
  const nodeIds = new Map<string, string>();
  const nodes: { name: string; depth: number; itemStyle: { color: string } }[] = [];
  const nodeAt = (period: number, label: string) => {
    const key = `${period}|${label}`;
    let id = nodeIds.get(key);
    if (!id) {
      id = key;
      nodeIds.set(key, id);
      nodes.push({ name: id, depth: period, itemStyle: { color: VIZ_PALETTE[period % VIZ_PALETTE.length] } });
    }
    return id;
  };
  const stripPeriod = (id: string) => id.slice(id.indexOf("|") + 1);
  const sankeyLinks = links.map(link => ({ source: nodeAt(link.from_period, link.from_label), target: nodeAt(link.to_period, link.to_label), value: Math.max(link.weight, 0.01) }));
  return {
    ...BASE,
    tooltip: {
      ...TOOLTIP,
      trigger: "item",
      formatter: params => {
        const item = params as { dataType?: string; name?: string; data?: { source?: string; target?: string; value?: number } };
        if (item.dataType === "edge" && item.data) {
          return `${stripPeriod(item.data.source ?? "")} → ${stripPeriod(item.data.target ?? "")}<br/>延续强度 ${Number(item.data.value ?? 0).toFixed(2)}`;
        }
        return item.name ? stripPeriod(item.name) : "";
      },
    },
    series: [{
      type: "sankey",
      left: 24, right: 120, top: 16, bottom: 16,
      nodeWidth: 10, nodeGap: 12, draggable: false,
      itemStyle: { borderWidth: 0, borderRadius: 2 },
      label: { color: tokens.text, fontSize: 12, formatter: (params: { name?: string }) => stripPeriod(params.name ?? "") },
      lineStyle: { color: "gradient", curveness: 0.5, opacity: 0.28 },
      emphasis: { focus: "adjacency" },
      data: nodes,
      links: sankeyLinks,
    }],
  };
}

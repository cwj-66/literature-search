import { Alert, Button, Empty, Skeleton } from "antd";
import { useCallback, useMemo } from "react";
import { QUADRANT_META, quadrantOption, sankeyOption, themeRiverOption } from "../themeCharts";
import type { KnowledgeTheme, ThemeQuadrant } from "../types";
import { useKnowledgeThemes } from "../useKnowledgeThemes";
import { EChart } from "./EChart";

export function TopicEvolution({ references, onExploreTheme }: {
  references?: string[];
  onExploreTheme: (theme: KnowledgeTheme) => void;
}) {
  const { data, error, refresh } = useKnowledgeThemes(references);
  const themes = useMemo(() => data?.themes ?? [], [data]);
  const years = useMemo(() => data?.years ?? [], [data]);
  const links = useMemo(() => data?.evolution.links ?? [], [data]);
  const quadOption = useMemo(() => themes.length ? quadrantOption(themes) : null, [themes]);
  const riverOption = useMemo(() => themes.length && years.length ? themeRiverOption(themes, years) : null, [themes, years]);
  const evoOption = useMemo(() => links.length ? sankeyOption(links) : null, [links]);
  const pickTheme = useCallback((params: { name?: string }) => {
    const theme = themes.find(item => item.name === params.name);
    if (theme) onExploreTheme(theme);
  }, [themes, onExploreTheme]);
  const quadrants = (Object.keys(QUADRANT_META) as ThemeQuadrant[]).filter(q => themes.some(t => t.quadrant === q));

  return <section className="kg-evolution" aria-label="主题变化">
    <div className="kg-section-heading"><div><h2>研究热点与主题演化</h2><p>基于工作区论文的 AI 分析结果自动归纳主题：四象限地图看当前热点，河流图看逐年热度，桑基图看主题之间的延续与分化。</p></div></div>
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={refresh}>重试</Button>} /> : null}
    {!data && !error ? <Skeleton active paragraph={{ rows: 8 }} /> : null}
    {data ? <>
      <p className="kg-data-note">统计范围为当前工作区，不代表整个研究领域。共 {data.total_papers} 篇论文，其中 {data.analyzed_papers} 篇完成 AI 分析并进入主题统计。{data.scope_note}主题由算法自动归纳，名称与象限划分仅供参考。</p>
      {!themes.length ? <Empty description="论文不足或未做 AI 分析，暂时无法生成主题地图。请打开论文详情执行 AI 分析后重试。" /> : <>
        <div className="kg-section-heading"><h3>研究热点四象限</h3><span className="kg-data-note">横轴：关联度（与整体研究网络的相关性）· 纵轴：密集度（主题内部关联强度）· 点大小：论文数</span></div>
        <div className="kg-quadrant-legend">{quadrants.map(q => <span key={q} className="kg-quadrant-chip"><i style={{ background: QUADRANT_META[q].color }} />{QUADRANT_META[q].label} · {themes.filter(t => t.quadrant === q).length} 个</span>)}</div>
        {quadOption ? <EChart option={quadOption} height={400} ariaLabel="研究热点四象限图" onClick={pickTheme} /> : null}
        {riverOption ? <>
          <div className="kg-section-heading"><h3>主题河流图</h3><span className="kg-data-note">每年归入各主题的论文数量</span></div>
          <EChart option={riverOption} height={380} ariaLabel="主题河流图" />
        </> : null}
        {evoOption ? <>
          <div className="kg-section-heading"><h3>主题演化</h3><span className="kg-data-note">相邻时期的主题延续关系，流宽为延续强度</span></div>
          <EChart option={evoOption} height={360} ariaLabel="主题演化桑基图" />
        </> : null}
      </>}
    </> : null}
  </section>;
}

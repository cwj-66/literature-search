import { Alert, Button, Empty, Skeleton } from "antd";
import { useCallback, useMemo, useState } from "react";
import { QUADRANT_META, quadrantOption, sankeyOption, themeRiverOption } from "../themeCharts";
import type { KnowledgeTheme, ThemeQuadrant } from "../types";
import { useKnowledgeThemes } from "../useKnowledgeThemes";
import { EChart } from "./EChart";

export function TopicEvolution({ references, onExploreTheme }: {
  references?: string[];
  onExploreTheme: (theme: KnowledgeTheme) => void;
}) {
  const [scope, setScope] = useState<"workspace" | "expanded">("workspace");
  const { data, error, refresh } = useKnowledgeThemes(references, scope);
  const themes = useMemo(() => data?.themes ?? [], [data]);
  const years = useMemo(() => data?.years ?? [], [data]);
  const links = useMemo(() => data?.evolution.links ?? [], [data]);
  const insufficient = data?.status === "insufficient";
  const quadOption = useMemo(() => !insufficient && themes.length ? quadrantOption(themes) : null, [insufficient, themes]);
  const riverOption = useMemo(() => themes.length && years.length ? themeRiverOption(themes, years) : null, [themes, years]);
  const evoOption = useMemo(() => !insufficient && links.length ? sankeyOption(links) : null, [insufficient, links]);
  const pickTheme = useCallback((params: { name?: string }) => {
    const theme = themes.find(item => item.name === params.name);
    if (theme) onExploreTheme(theme);
  }, [themes, onExploreTheme]);
  const quadrants = (Object.keys(QUADRANT_META) as ThemeQuadrant[]).filter(q => themes.some(t => t.quadrant === q));

  return <section className="kg-evolution" aria-label="主题变化">
    <div className="kg-section-heading"><div><h2>研究热点与主题演化</h2><p>基于当前样本的关键词共现归纳主题。</p></div></div>
    <div className="kg-entity-toggles" role="group" aria-label="主题统计范围">
      <Button size="small" type={scope === "workspace" ? "primary" : "default"} onClick={() => setScope("workspace")}>工作区样本</Button>
      <Button size="small" type={scope === "expanded" ? "primary" : "default"} onClick={() => setScope("expanded")}>扩展文献样本</Button>
    </div>
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={refresh}>重试</Button>} /> : null}
    {!data && !error ? <Skeleton active paragraph={{ rows: 8 }} /> : null}
    {data ? <>
      <p className="kg-data-note">{data.scope_note}。共 {data.total_papers} 篇论文，其中 {data.analyzed_papers} 篇有关键词并进入主题统计。主题名称由模型生成，中心度、密集度和演进关系由共现统计得到。</p>
      {!themes.length ? <Empty description={scope === "expanded" ? "没有已缓存的推荐候选，无法做扩展样本主题分析。" : "论文不足或没有可识别关键词，暂时无法生成主题地图。"} /> : <>
        {insufficient ? <>
          <p className="kg-data-note" role="status">{data.status_reason || "样本不足，不绘制战略坐标图。"}{data.evolution.reason ? ` ${data.evolution.reason}` : ""}</p>
          <div className="kg-section-heading"><h3>样本关键词与年份</h3></div>
          <ul className="kg-pick-list">{themes.map(theme => <li key={theme.id}>
            <div>
              <strong>{theme.name || theme.keywords[0] || "未命名主题"}</strong>
              <span>{theme.keywords.join("、") || "无关键词"}{years.length ? ` · ${years.map(year => `${year}年 ${theme.by_year[String(year)] ?? 0}篇`).join("，")}` : ""}</span>
            </div>
            <button type="button" onClick={() => onExploreTheme(theme)}>在关系网中查看</button>
          </li>)}</ul>
        </> : <>
          <div className="kg-section-heading"><h3>研究热点四象限</h3><span className="kg-data-note">横轴：Callon 中心度 · 纵轴：Callon 密集度 · 点大小：论文数</span></div>
          <div className="kg-quadrant-legend">{quadrants.map(q => <span key={q} className="kg-quadrant-chip"><i style={{ background: QUADRANT_META[q].color }} />{QUADRANT_META[q].label} · {themes.filter(t => t.quadrant === q).length} 个</span>)}</div>
          {quadOption ? <EChart option={quadOption} height={400} ariaLabel="研究热点四象限图" onClick={pickTheme} /> : null}
        </>}
        {riverOption ? <>
          <div className="kg-section-heading"><h3>主题河流图</h3><span className="kg-data-note">{scope === "expanded" ? "扩展文献样本中各主题的逐年篇数，不是全领域趋势" : "工作区样本中各主题的逐年篇数，不是全领域趋势"}</span></div>
          <EChart option={riverOption} height={380} ariaLabel="主题河流图" />
        </> : null}
        {evoOption ? <>
          <div className="kg-section-heading"><h3>主题演化</h3><span className="kg-data-note">相邻时期的 inclusion index，流宽为延续强度</span></div>
          <EChart option={evoOption} height={360} ariaLabel="主题演化桑基图" />
        </> : !insufficient && data.evolution.reason ? <p className="kg-data-note">{data.evolution.reason}</p> : null}
      </>}
    </> : null}
  </section>;
}

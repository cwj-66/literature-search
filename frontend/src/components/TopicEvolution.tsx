import { Empty } from "antd";
import { useMemo } from "react";
import { topicStats } from "../knowledgeGraph";
import type { EntityGraph } from "../graphData";

export function TopicEvolution({ data, onExplore }: { data: EntityGraph; onExplore: (id: string, year?: number) => void }) {
  const topics = useMemo(() => topicStats(data), [data]);
  const papers = data.nodes.filter(n => n.kind === "paper");
  const dated = papers.filter(p => p.year != null);
  const years = [...new Set(dated.map(p => p.year!))].sort((a, b) => a - b);
  const topicPaperIds = new Set(data.edges.filter(e => e.relation === "has_keyword").map(e => e.source));
  const maxCount = Math.max(1, ...years.map(y => dated.filter(p => p.year === y).length));
  const shownTopics = topics;
  const yearWidth = Math.max(54, Math.min(94, 650 / Math.max(1, years.length)));

  return <section className="kg-evolution" aria-label="主题变化">
    <div className="kg-section-heading"><div><h2>主题如何随年份变化</h2><p>按论文发表年份统计。同一篇论文在同一主题中只计一次，一篇可以属于多个主题。</p></div></div>
    <p className="kg-data-note">统计范围为当前工作区，反映已收集文献的主题分布，不代表整个研究领域。当前筛选 {papers.length} 篇 · 有年份 {dated.length} 篇 · 有关键词 {topicPaperIds.size} 篇。{papers.length - dated.length ? `${papers.length - dated.length} 篇缺少年份，未进入年度统计。` : ""}{papers.length - topicPaperIds.size ? `${papers.length - topicPaperIds.size} 篇没有关键词，未进入主题统计。` : ""}这里只展示实际数量，不推断热点或研究空白。</p>
    {!years.length ? <Empty description="论文缺少发表年份，暂时无法展示时间分布。" /> : <>
      <div className="kg-section-heading"><h3>年度论文分布</h3><span className="kg-data-note">实色：已提取主题　浅色：尚无主题</span></div>
      <div className="kg-chart-scroll"><svg role="img" aria-label="年度论文数量与主题提取覆盖" viewBox={`0 0 ${Math.max(360, years.length * yearWidth + 80)} 210`} style={{ minWidth: Math.max(360, years.length * yearWidth + 80) }} className="kg-year-chart">
        <line x1="44" y1="166" x2={Math.max(340, years.length * yearWidth + 64)} y2="166" stroke="#e3e8ef" />
        {years.map((year, i) => {
          const total = dated.filter(p => p.year === year).length;
          const analyzed = dated.filter(p => p.year === year && topicPaperIds.has(p.id)).length;
          const x = 60 + i * yearWidth;
          const h = total / maxCount * 115, filled = analyzed / maxCount * 115;
          return <g key={year} role="button" tabIndex={0} aria-label={`${year}年，${total}篇论文，${analyzed}篇有主题`} className="kg-chart-year" onClick={() => onExplore("", year)} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onExplore("", year); } }}>
            <title>{year}年：{total}篇，已提取主题{analyzed}篇</title>
            <rect x={x} y={166 - h} width="30" height={h} rx="3" fill="#e5ebf5" />
            <rect x={x} y={166 - filled} width="30" height={filled} rx="3" fill="#4565a4" />
            <text x={x + 15} y={155 - h} textAnchor="middle" fill="#334155" fontSize="13">{total}</text>
            <text x={x + 15} y="190" textAnchor="middle" fill="#667085" fontSize="12">{year}</text>
          </g>;
        })}
      </svg></div>
    </>}
    {!topics.length ? <Empty description="尚未提取研究主题。请打开论文详情，执行 AI 分析后刷新图谱。" /> : <>
      <div className="kg-section-heading"><h3>主题与年份</h3><span className="kg-data-note">{shownTopics.length} 个主题；数字是涉及该主题的论文数量</span></div>
      {years.length ? <div className="kg-heatmap-scroll"><table className="kg-heatmap"><thead><tr><th>研究主题</th>{years.map(y => <th key={y}>{y}</th>)}<th>合计</th></tr></thead><tbody>
        {shownTopics.map(topic => <tr key={topic.id}><th><button type="button" onClick={() => onExplore(topic.id)}>{topic.label}</button></th>{years.map(year => {
          const count = topic.papers.filter(p => p.year === year).length;
          const denominator = dated.filter(p => p.year === year).length;
          return <td key={year}><button type="button" disabled={!count} aria-label={`${topic.label}，${year}年，${count}篇`} title={`${count}/${denominator}篇，占当年样本${Math.round(count / denominator * 100)}%`} onClick={() => onExplore(topic.id, year)} style={{ background: count ? `rgba(35,128,111,${.12 + .55 * count / denominator})` : "#f7f9fb", color: count / denominator > .5 ? "#fff" : "#35665c" }}>{count || "—"}</button></td>;
        })}<td>{topic.papers.filter(item => item.year != null).length}</td></tr>)}
      </tbody></table></div> : null}
    </>}
  </section>;
}

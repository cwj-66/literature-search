import { Empty } from "antd";
import { useMemo } from "react";
import { topicGroups, topicStats } from "../knowledgeGraph";
import type { EntityGraph } from "../graphData";

export function TopicEvolution({ data, onExplore }: { data: EntityGraph; onExplore: (id: string, year?: number) => void }) {
  const topics = useMemo(() => topicStats(data), [data]);
  const groups = useMemo(() => topicGroups(data), [data]);
  const papers = data.nodes.filter(n => n.kind === "paper");
  const dated = papers.filter(p => p.year != null);
  const years = [...new Set(dated.map(p => p.year!))].sort((a, b) => a - b);
  const topicPaperIds = new Set(data.edges.filter(e => e.relation === "has_keyword").map(e => e.source));
  const datedTopics = dated.filter(p => topicPaperIds.has(p.id));
  const enough = datedTopics.length >= 5 && new Set(datedTopics.map(p => p.year)).size >= 2;
  const maxYear = years.at(-1);
  const firstObserved = topics.filter(t => t.firstYear === maxYear);
  const maxCount = Math.max(1, ...years.map(y => dated.filter(p => p.year === y).length));
  const shownTopics = topics.slice(0, 12);
  const yearWidth = Math.max(54, Math.min(94, 650 / Math.max(1, years.length)));

  return <section className="kg-evolution" aria-label="主题演进">
    <div className="kg-section-heading"><div><h2>研究主题如何随时间变化</h2><p>按论文发表年份统计，每篇论文在同一主题下只计一次。点击年份或热度单元格继续探索。</p></div></div>
    <p className="kg-data-note">当前筛选 {papers.length} 篇 · 有年份 {dated.length} 篇 · 有主题 {topicPaperIds.size} 篇。{papers.length - dated.length ? `${papers.length - dated.length} 篇年份未知，未纳入年度统计。` : ""}热度指样本内论文数量，不代表整个领域的热度。</p>
    {!enough ? <p className="kg-inline-notice" role="status">数据不足以判断趋势：至少需要 5 篇有年份和主题的论文，且跨越 2 个年份。当前仅展示实际年度分布，不推断增长或领域演进。</p> : null}
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
      <div className="kg-section-heading"><h3>主题年度热度</h3><span className="kg-data-note">展示前 {shownTopics.length} 个主题，共 {topics.length} 个；数字为论文数量</span></div>
      {years.length ? <div className="kg-heatmap-scroll"><table className="kg-heatmap"><thead><tr><th>研究主题</th>{years.map(y => <th key={y}>{y}</th>)}<th>合计</th></tr></thead><tbody>
        {shownTopics.map(topic => <tr key={topic.id}><th><button type="button" onClick={() => onExplore(topic.id)}>{topic.label}</button></th>{years.map(year => {
          const count = topic.papers.filter(p => p.year === year).length;
          const denominator = dated.filter(p => p.year === year).length;
          return <td key={year}><button type="button" disabled={!count} aria-label={`${topic.label}，${year}年，${count}篇`} title={`${count}/${denominator}篇，占当年样本${Math.round(count / denominator * 100)}%`} onClick={() => onExplore(topic.id, year)} style={{ background: count ? `rgba(35,128,111,${.12 + .55 * count / denominator})` : "#f7f9fb", color: count / denominator > .5 ? "#fff" : "#35665c" }}>{count || "—"}</button></td>;
        })}<td>{topic.count}</td></tr>)}
      </tbody></table></div> : null}
      {enough ? <div className="kg-evolution-bottom"><section><h3>样本中新出现的主题 · {maxYear}</h3><p className="kg-data-note">在当前筛选样本中首次出现于最新年份；不等于领域首次提出。</p>
        <div className="kg-topic-tags">{firstObserved.length ? firstObserved.map(t => <button type="button" key={t.id} onClick={() => onExplore(t.id)}>{t.label}<span>{t.count} 篇</span></button>) : <p className="muted">最新年份没有首次出现的主题。</p>}</div>
      </section><section><h3>主题共现分组 <small>推断关系</small></h3><p className="kg-data-note">关键词至少共同出现在 2 篇论文中才连接，再按连通分量分组；不表示方法继承。</p>
        {groups.length ? groups.slice(0, 8).map((group, i) => <div className="kg-topic-group" key={i}>{group.map(t => <button type="button" key={t.id} onClick={() => onExplore(t.id)}>{t.label}</button>)}</div>) : <p className="muted">重复共现证据不足，暂不生成主题分组。</p>}
      </section></div> : null}
    </>}
  </section>;
}

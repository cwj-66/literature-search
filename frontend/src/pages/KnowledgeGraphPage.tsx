import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Segmented, Select, Spin, Tag } from "antd";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { paperLocation, safeReturn } from "../workspace";
import { KnowledgeCanvas } from "../components/KnowledgeCanvas";
import { KIND_COLOR, KIND_LABEL, ragExample, type EntityGraph, type EntityKind } from "../graphData";

const KINDS: EntityKind[] = ["paper", "author", "keyword", "venue"];
const example = ragExample();

export default function KnowledgeGraphPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const current = location.pathname + location.search;
  const mode = params.get("mode") === "example" ? "example" : "local";
  const update = (values: Record<string, string>) => setParams(current => {
    const next = new URLSearchParams(current);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    return next;
  }, { replace: true });
  const setSelected = (value: string) => update({ selected: value, focus: "" });
  const setTab = (value: string) => update({ tab: value });
  const [data, setData] = useState<EntityGraph>({ nodes: [], edges: [], total_papers: 0, scope: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const query = params.get("q") || "";
  const year = Number(params.get("year")) || 0;
  const focus = params.get("focus") || "";
  const focused = data.nodes.find(n => n.kind === "paper" && (n.paper_id === focus || n.id === `paper:${focus}`));
  const selected = params.get("selected") || focused?.id || "";
  const kinds = params.has("kinds") ? KINDS.filter(kind => params.get("kinds")!.split(",").includes(kind)) : KINDS;
  const tab = params.get("tab") === "evolution" ? "evolution" : "graph";
  useEffect(() => { document.title = "知识图谱 - 文献检索"; }, []);
  useEffect(() => {
    setError("");
    if (mode === "example") { setData(example); setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setData({ nodes: [], edges: [], total_papers: 0, scope: "" });
    fetch("/api/knowledge-graph", { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error("本地知识图谱加载失败");
      return await r.json() as EntityGraph;
    }).then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(err => { if (!controller.signal.aborted) setError(err.message || "加载失败，请重试"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [mode, retry]);
  const papers = data.nodes.filter(n => n.kind === "paper");
  const years = [...new Set(papers.flatMap(p => p.year ? [p.year] : []))].sort((a, b) => a - b);
  const visible = useMemo(() => {
    const nodes = new Map(data.nodes.map(n => [n.id, n]));
    const term = query.trim().toLocaleLowerCase();
    const matched = new Set(data.nodes.filter(n => n.label.toLocaleLowerCase().includes(term)).map(n => n.id));
    const eligible = data.nodes.filter(n => n.kind === "paper" && (!year || (n.year != null && n.year <= year))
      && (!term || matched.has(n.id) || data.edges.some(e => e.source === n.id && matched.has(e.target))));
    const paperIds = new Set(eligible.map(n => n.id));
    const edges = data.edges.filter(e => paperIds.has(e.source));
    const ids = new Set([...paperIds, ...edges.map(e => e.target)]);
    return { nodes: [...ids].flatMap(id => nodes.has(id) ? [nodes.get(id)!] : []), edges, total_papers: eligible.length, scope: data.scope };
  }, [data, query, year]);
  const visiblePapers = visible.nodes.filter(n => n.kind === "paper");
  // Keep the prototype readable; searching narrows the sample before this cap.
  const canvasData = useMemo(() => {
    const candidates = visible.nodes.filter(n => n.kind === "paper");
    const priority = candidates.find(n => n.id === selected);
    const first = candidates.slice(0, 16);
    if (priority && !first.includes(priority)) first.splice(15, 1, priority);
    const pids = new Set(first.map(n => n.id));
    const related = visible.edges.filter(e => pids.has(e.source));
    const allowed = new Set([...pids]);
    for (const kind of ["keyword", "author", "venue"] as EntityKind[]) {
      const counts = new Map<string, number>();
      related.forEach(e => counts.set(e.target, (counts.get(e.target) || 0) + 1));
      visible.nodes.filter(n => n.kind === kind && counts.has(n.id)).sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0)).slice(0, kind === "venue" ? 5 : 18).forEach(n => allowed.add(n.id));
    }
    const nodes = visible.nodes.filter(n => allowed.has(n.id) && kinds.includes(n.kind));
    const ids = new Set(nodes.map(n => n.id));
    return { ...visible, nodes, edges: related.filter(e => ids.has(e.source) && ids.has(e.target)) };
  }, [visible, kinds, selected]);
  const selectedNode = visible.nodes.find(n => n.id === selected);
  const neighbors = selectedNode ? visible.edges.filter(e => e.source === selected || e.target === selected)
    .map(e => visible.nodes.find(n => n.id === (e.source === selected ? e.target : e.source))).filter(n => n != null) : [];
  const themes = visible.nodes.filter(n => n.kind === "keyword").map(n => ({ ...n,
    count: visible.edges.filter(e => e.target === n.id).length,
  })).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "zh-CN"));
  const trendThemes = themes.slice(0, 5);
  const themeCount = (id: string, y: number) => visible.edges.filter(e => e.target === id && visiblePapers.some(p => p.id === e.source && p.year === y)).length;
  function reset() { update({ q: "", year: "", kinds: "", selected: "", focus: "" }); }

  return <main className="kg-page">
    <header className="kg-header">
      <div><div className="kg-breadcrumb"><Link to="/library">我的文献</Link><span>/</span>集合关系</div>
        <h1>集合关系图谱</h1><p>探索已入库文献的作者、主题和时间脉络。点击节点预览，打开研读后可返回原位置。</p></div>
      <Link className="kg-library-link" to={safeReturn(params.get("from"))}>返回研读来源 ↗</Link>
    </header>
    <div className="kg-scope-bar"><Segmented aria-label="图谱数据来源" value={mode} onChange={v => update({ mode: String(v), selected: "", focus: "", q: "", year: "" })} options={[{ label: "我的文献", value: "local" }, { label: "体验 RAG 示例", value: "example" }]} />
      <Tag color={mode === "example" ? "gold" : "blue"}>{mode === "example" ? "原型 · 演示样本" : "已入库数据"}</Tag></div>
    <p className="kg-scope">{data.scope || "正在读取本地论文库…"} {mode === "local" && data.total_papers > papers.length ? `本地共 ${data.total_papers} 篇。` : ""}</p>
    <div className="kg-stats">{KINDS.map(kind => <div key={kind}><i style={{ background: KIND_COLOR[kind] }} /><strong>{visible.nodes.filter(n => n.kind === kind).length}</strong><span>{KIND_LABEL[kind]}</span></div>)}<div><strong>{visible.edges.length}</strong><span>实体关系</span></div></div>
    <div className="kg-filters">
      <Input.Search aria-label="在图谱中搜索" placeholder="搜索论文、作者或主题，如：评估" value={query} onChange={e => update({ q: e.target.value, selected: "", focus: "" })} allowClear style={{ maxWidth: 380 }} />
      <Select aria-label="论文年份范围" value={year} onChange={v => update({ year: String(v), selected: "", focus: "" })} options={[{ value: 0, label: "全部年份" }, ...years.map(y => ({ value: y, label: `截至 ${y} 年` }))]} style={{ width: 150 }} />
      <Button onClick={reset}>重置</Button>
    </div>
    {!loading && !error && focus && !focused ? <Alert type="info" message="当前论文未包含在图谱数据范围内，可先加入我的文献，再查看其关系。" /> : null}
    {loading ? <Spin tip="正在构建本地图谱"><div style={{ height: 100 }} /></Spin> : null}
    {error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => setRetry(r => r + 1)}>重试</Button>} /> : null}
    {!loading && !error && !visiblePapers.length ? <Empty description={mode === "local" && !papers.length ? "本地论文库为空，加入论文并完成AI分析后即可连接主题" : "当前筛选没有论文，请尝试重置"} /> : null}
    {visiblePapers.length ? <>
      <div className="kg-view-bar"><Segmented aria-label="知识探索视图" value={tab} onChange={v => setTab(String(v))} options={[{ value: "graph", label: "关系图谱" }, { value: "evolution", label: "时间脉络" }]} />
        <span className="meta">{tab === "graph" ? "连线表示实体关系，不表示引用或技术继承" : "按发表年份排列，不代表因果或技术继承"}</span></div>
      {tab === "graph" ? <div className="kg-workspace">
        <section className="kg-graph-panel" aria-label="关系图谱">
          <div className="kg-legend"><Checkbox.Group value={kinds} onChange={v => update({ kinds: v.length ? v.join(",") : "none" })} options={KINDS.map(kind => ({ value: kind, label: <span><i style={{ background: KIND_COLOR[kind] }} />{KIND_LABEL[kind]}</span> }))} /></div>
          {canvasData.nodes.length ? <KnowledgeCanvas key={`${mode}-${year}-${query}`} scope={`${mode}-${year}-${query}`} data={canvasData} selected={selectedNode?.id || ""} onSelect={setSelected} /> : <Empty description="请选择至少一种实体" />}
          <p className="kg-canvas-caption">为便于阅读，画布最多展示16篇论文、18位作者、18个关键词及5个刊会；统计和时间视图使用当前筛选的全部样本。</p>
        </section>
        <aside className="kg-inspector">
          {selectedNode ? <>
            <span className="kg-eyebrow">节点详情</span><Tag color={KIND_COLOR[selectedNode.kind]}>{KIND_LABEL[selectedNode.kind]}</Tag>
            <h2>{selectedNode.label}</h2>
            {selectedNode.year ? <p className="meta">发表年份 · {selectedNode.year}</p> : null}
            <p className="meta">{mode === "example" && selectedNode.kind === "keyword" ? "人工演示主题，非自动提取结果。" : selectedNode.kind === "keyword" ? "来自已有AI分析；同名归并不等于完成同义词消歧。" : "来自论文元数据；作者同名关系尚未消歧。"}</p>
            {selectedNode.paper_id ? <Link to={paperLocation(selectedNode.paper_id, current)}>打开阅读与分析 →</Link> : null}
            <h3>关联实体 · {neighbors.length}</h3><div className="kg-neighbors">{neighbors.map((n, i) => n ? <button key={`${n.id}-${i}`} onClick={() => setSelected(n.id)}><i style={{ background: KIND_COLOR[n.kind] }} /><span>{n.label}</span><small>{KIND_LABEL[n.kind]}</small></button> : null)}</div>
          </> : <>
            <span className="kg-eyebrow">探索指南</span><h2>先找一个你感兴趣的节点</h2><p>点击论文看它的作者和主题；点击关键词，看哪些论文在讨论同一个问题。</p>
            <div className="kg-tip">{mode === "example" ? "例如点击「幻觉」，查看演示论文之间的主题联系。" : "关系来自当前文献；分析论文后，可以继续探索提取出的主题。"}</div>
            <h3>样本中常见的主题</h3><div className="kg-topic-tags">{themes.slice(0, 8).map(t => <button key={t.id} onClick={() => setSelected(t.id)}>{t.label}<span>{t.count}</span></button>)}</div>
          </>}
        </aside>
      </div> : <section className="kg-timeline" aria-label="论文时间脉络">
        {years.filter(y => !year || y <= year).map(y => { const items = visiblePapers.filter(p => p.year === y); if (!items.length) return null;
          return <div className="kg-time-row" key={y}><div className="kg-time-year">{y}<small>{items.length} 篇样本</small></div><div className="kg-time-papers">{items.map(p => <article key={p.id}><h3><button className="timeline-preview" onClick={() => update({ selected: p.id, focus: "", tab: "graph" })}>{p.label}</button></h3><div>{visible.edges.filter(e => e.source === p.id).map(e => visible.nodes.find(n => n.id === e.target)).filter(n => n?.kind === "keyword").map(n => n ? <Tag key={n.id} color="cyan">{n.label}</Tag> : null)}</div></article>)}</div></div>;
        })}
        {visiblePapers.some(p => !p.year) ? <p className="meta">另有 {visiblePapers.filter(p => !p.year).length} 篇年份未知论文，未纳入时间脉络与年度统计。</p> : null}
      </section>}
      <section className="kg-trends" aria-label="样本主题分布">
        <div className="kg-section-heading"><div><span className="kg-eyebrow">TOPIC SIGNALS</span><h2>这些主题，在什么时候出现？</h2></div><Tag>当前样本统计</Tag></div>
        <p className="meta">统计每年涉及该主题的论文篇数，一篇论文可属于多个主题。样本未覆盖的年份不代表该领域没有研究，数量不等于学术热度。</p>
        {trendThemes.length ? <div className="kg-topic-chart"><div className="kg-chart-row kg-chart-head"><span>主题 / 篇数</span>{years.filter(y => !year || y <= year).map(y => <span key={y}>{y}</span>)}</div>
          {trendThemes.map(t => <div className="kg-chart-row" key={t.id}><button onClick={() => update({ selected: t.id, focus: "", tab: "graph" })}>{t.label}</button>{years.filter(y => !year || y <= year).map(y => { const count = themeCount(t.id, y); return <span key={y} className="kg-heat-cell" style={{ background: count ? `rgba(38,157,139,${.14 + Math.min(count, 5) * .14})` : "#f3f5f8" }} aria-label={`${t.label}，${y}年，${count}篇`}>{count}</span>; })}</div>)}
        </div> : <Empty description="尚无主题关键词，请先完成论文AI分析" />}
      </section>
    </> : null}
    <footer className="kg-footer">知识图谱原型 · 当前不自动发起外部搜索，也不调用模型。领域趋势需要持续、可比的论文样本。</footer>
  </main>;
}

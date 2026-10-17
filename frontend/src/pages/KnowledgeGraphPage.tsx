import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Segmented, Select, Skeleton } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { fetchNetwork } from "../api";
import { useProjects } from "../projects";
import type { PaperNetwork } from "../types";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { paperLocation, returnLabel, safeReturn } from "../workspace";
import { KnowledgeCanvas } from "../components/KnowledgeCanvas";
import { KIND_COLOR, KIND_LABEL, ragExample, type EntityGraph, type EntityKind, type EntityNode } from "../graphData";

const KINDS: EntityKind[] = ["paper", "author", "keyword", "venue"];
const example = ragExample();

export default function KnowledgeGraphPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const current = location.pathname + location.search;
  const { current: project } = useProjects();
  const projectIds = useMemo(() => new Set(Object.entries(project?.papers ?? {}).flatMap(([key, item]) => [key, item.paper.id])), [project]);
  const modeParam = params.get("mode");
  const mode = modeParam === "example" ? "example" : modeParam === "local" ? "local" : projectIds.size ? "project" : "local";
  const update = (values: Record<string, string>) => setParams(current => {
    const next = new URLSearchParams(current);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    return next;
  }, { replace: true });
  const setSelected = (value: string) => update({ selected: value, focus: "" });
  const setTab = (value: string) => update({ tab: value });
  const [raw, setRaw] = useState<EntityGraph>({ nodes: [], edges: [], total_papers: 0, scope: "" });
  const data = useMemo<EntityGraph>(() => {
    if (mode !== "project") return raw;
    const keep = (n: EntityNode) => n.kind !== "paper" || projectIds.has(n.paper_id ?? "") || projectIds.has(n.id.slice(6));
    const nodes = raw.nodes.filter(keep);
    const ids = new Set(nodes.map(n => n.id));
    return { nodes, edges: raw.edges.filter(e => ids.has(e.source)), total_papers: nodes.filter(n => n.kind === "paper").length,
      scope: `当前项目「${project?.name ?? ""}」中已在文献库的论文；关键词来自已有 AI 分析，未分析论文仍展示作者和刊会。` };
  }, [raw, mode, projectIds, project]);
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
  useEffect(() => { document.title = "集合图谱 - 文献研读"; }, []);
  useEffect(() => {
    setError("");
    if (mode === "example") { setRaw(example); setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setRaw({ nodes: [], edges: [], total_papers: 0, scope: "" });
    fetch("/api/knowledge-graph", { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error("本地知识图谱加载失败");
      return await r.json() as EntityGraph;
    }).then(result => { if (!controller.signal.aborted) setRaw(result); })
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
  const [net, setNet] = useState<PaperNetwork | null>(null);
  const netPaperId = mode !== "example" && selectedNode?.kind === "paper" ? selectedNode.paper_id : undefined;
  useEffect(() => {
    setNet(null);
    if (!netPaperId) return;
    const controller = new AbortController();
    fetchNetwork(netPaperId, false, controller.signal).then(d => { if (!controller.signal.aborted) setNet(d); }).catch(() => undefined);
    return () => controller.abort();
  }, [netPaperId]);
  const shown = useMemo(() => {
    if (!net || !selectedNode) return canvasData;
    const byPaper = new Map(canvasData.nodes.filter(n => n.kind === "paper" && n.paper_id).map(n => [n.paper_id!, n.id]));
    const nodes = [...canvasData.nodes];
    const map = new Map<string, string>([[net.paper_id, selectedNode.id]]);
    let external = 0;
    for (const n of net.nodes) {
      if (n.center || external >= 12 && !byPaper.has(n.id)) continue;
      if (byPaper.has(n.id)) { map.set(n.id, byPaper.get(n.id)!); continue; }
      external += 1;
      map.set(n.id, `net:${n.id}`);
      nodes.push({ id: `net:${n.id}`, kind: "paper", label: n.title, year: n.year, paper_id: n.id, external: true });
    }
    const edges = [...canvasData.edges];
    for (const e of net.edges) {
      const source = map.get(e.source), target = map.get(e.target);
      if (source && target) edges.push({ source, target, relation: e.type });
    }
    return { ...canvasData, nodes, edges };
  }, [canvasData, net, selectedNode]);
  const netItems = net ? net.nodes.filter(n => !n.center).map(n => ({ node: n, labels: [...new Set(net.edges.filter(e => e.source === n.id || e.target === n.id).map(e => e.label))] })) : [];
  const neighbors = selectedNode ? visible.edges.filter(e => e.source === selected || e.target === selected)
    .map(e => visible.nodes.find(n => n.id === (e.source === selected ? e.target : e.source))).filter(n => n != null) : [];
  const themes = visible.nodes.filter(n => n.kind === "keyword").map(n => ({ ...n,
    count: visible.edges.filter(e => e.target === n.id).length,
  })).sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "zh-CN"));
  const trendThemes = themes.slice(0, 5);
  const themeCount = (id: string, y: number) => visible.edges.filter(e => e.target === id && visiblePapers.some(p => p.id === e.source && p.year === y)).length;
  function reset() { update({ q: "", year: "", kinds: "", selected: "", focus: "" }); }

  const from = params.get("from") ? safeReturn(params.get("from")) : "";
  const shownYears = years.filter(y => !year || y <= year);
  return <main className="page page-wide kg-page">
    {from ? <Link to={from} className="back-link"><ArrowLeftOutlined aria-hidden />返回{returnLabel(from)}</Link> : null}
    <header className="page-head">
      <div>
        <h1 className="page-title">知识图谱</h1>
        <p className="page-sub">论文与作者、关键词、期刊／会议的实体关系；选中论文后叠加真实引用边和主题相似边。灰色实体连线不表示引用。</p>
      </div>
      <div className="page-actions">
        <Segmented aria-label="图谱数据来源" value={mode} onChange={v => update({ mode: String(v), selected: "", focus: "", q: "", year: "" })} options={[{ label: "当前项目", value: "project", disabled: !projectIds.size }, { label: "我的文献库", value: "local" }, { label: "RAG 示例", value: "example" }]} />
      </div>
    </header>
    <p className="kg-scope">{mode === "example" ? <><span className="status status-warning">演示数据</span> {data.scope}</> : <>{data.scope || "正在读取文献库…"}{data.total_papers > papers.length ? ` 文献库共 ${data.total_papers} 篇。` : ""}</>}</p>
    <div className="kg-toolbar">
      <Input.Search aria-label="在图谱中搜索" placeholder="搜索论文、作者或主题" value={query} onChange={e => update({ q: e.target.value, selected: "", focus: "" })} allowClear />
      <Select aria-label="论文年份范围" value={year} onChange={v => update({ year: String(v), selected: "", focus: "" })} options={[{ value: 0, label: "全部年份" }, ...years.map(y => ({ value: y, label: `截至 ${y} 年` }))]} />
      <Button type="text" onClick={reset}>重置</Button>
      <Segmented aria-label="探索视图" value={tab} onChange={v => setTab(String(v))} options={[{ value: "graph", label: "关系图" }, { value: "evolution", label: "时间脉络" }]} />
    </div>
    <p className="kg-counts">{KINDS.map(kind => <span key={kind}><i style={{ background: KIND_COLOR[kind] }} />{KIND_LABEL[kind]} {visible.nodes.filter(n => n.kind === kind).length}</span>)}<span>关系 {visible.edges.length}</span></p>
    {!loading && !error && focus && !focused ? <Alert type="info" showIcon message="这篇论文不在图谱数据范围内。加入文献库后即可查看它的关系。" /> : null}
    {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : null}
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(r => r + 1)}>重试</Button>} /> : null}
    {!loading && !error && !visiblePapers.length ? <Empty description={mode === "local" && !papers.length ? "文献库为空。加入论文并完成 AI 分析后，可在这里看到主题关系。" : "当前筛选没有论文，请重置筛选"} /> : null}
    {visiblePapers.length ? <>
      {tab === "graph" ? <div className="kg-workspace">
        <section className="kg-graph-panel" aria-label="关系图">
          <div className="kg-legend"><Checkbox.Group value={kinds} onChange={v => update({ kinds: v.length ? v.join(",") : "none" })} options={KINDS.map(kind => ({ value: kind, label: <span><i style={{ background: KIND_COLOR[kind] }} />{KIND_LABEL[kind]}</span> }))} />
            {net ? <span className="kg-edge-legend"><span><b className="edge-cites" />引用</span><span><b className="edge-similar" />主题相似</span><span><b className="edge-external" />不在当前图中的论文</span></span> : null}</div>
          {canvasData.nodes.length ? <KnowledgeCanvas key={`${mode}-${year}-${query}`} scope={`${mode}-${year}-${query}`} data={shown} selected={selectedNode?.id || ""}
            onSelect={id => id.startsWith("net:") ? navigate(paperLocation(id.slice(4), current)) : setSelected(id)} /> : <Empty description="请至少选择一种实体" />}
          <p className="kg-caption">画布最多展示 16 篇论文、18 位作者、18 个关键词和 5 个期刊／会议；计数和时间脉络使用当前筛选的全部样本。</p>
        </section>
        <aside className="kg-inspector" aria-label="节点详情">
          {selectedNode ? <>
            <p className="kg-kind"><i style={{ background: KIND_COLOR[selectedNode.kind] }} />{KIND_LABEL[selectedNode.kind]}{selectedNode.year ? ` · ${selectedNode.year}` : ""}</p>
            <h2>{selectedNode.label}</h2>
            <p className="muted">{mode === "example" && selectedNode.kind === "keyword" ? "人工演示主题，不是自动提取结果。" : selectedNode.kind === "keyword" ? "来自已有 AI 分析；同名合并不等于同义词消歧。" : "来自论文元数据；同名作者尚未消歧。"}</p>
            {selectedNode.paper_id ? <Link className="inspector-open" to={paperLocation(selectedNode.paper_id, current)}>打开论文</Link> : null}
            <h3>关联实体 {neighbors.length}</h3>
            <div className="kg-neighbors">{neighbors.map((n, i) => n ? <button type="button" key={`${n.id}-${i}`} onClick={() => setSelected(n.id)}><i style={{ background: KIND_COLOR[n.kind] }} /><span>{n.label}</span><small>{KIND_LABEL[n.kind]}</small></button> : null)}</div>
            {selectedNode.kind === "paper" && netPaperId ? <>
              <h3>引用与相似论文{net ? ` ${netItems.length}` : ""}</h3>
              {!net ? <p className="muted">正在读取关系…</p> : !netItems.length ? <p className="muted">暂未找到可核实的引用或相似论文。</p>
                : <div className="kg-neighbors">{netItems.map(({ node, labels }) => <Link key={node.id} to={paperLocation(node.id, current)} className="kg-net-item"><span>{node.title}</span><small>{[node.year, ...labels].filter(Boolean).join(" · ")}</small></Link>)}</div>}
              {net?.warnings.map(w => <p key={w} className="small-print">{w}</p>)}
            </> : null}
          </> : <>
            <h2>论文列表 {visiblePapers.length}</h2>
            <p className="muted">点击论文在图中定位，再展开它的引用与相似关系。</p>
            <div className="kg-neighbors">{visiblePapers.map(p => <button type="button" key={p.id} onClick={() => setSelected(p.id)}><span>{p.label}</span><small>{p.year ?? "年份未知"}</small></button>)}</div>
            {themes.length ? <><h3>常见主题</h3><div className="kg-topic-tags">{themes.slice(0, 8).map(t => <button type="button" key={t.id} onClick={() => setSelected(t.id)}>{t.label}<span>{t.count}</span></button>)}</div></> : <p className="muted">还没有主题关键词，完成论文 AI 分析后会出现。</p>}
          </>}
        </aside>
      </div> : <section className="panel kg-timeline" aria-label="时间脉络">
        <p className="muted">按发表年份排列，不代表因果或技术继承。</p>
        {shownYears.map(y => { const items = visiblePapers.filter(p => p.year === y); if (!items.length) return null;
          return <div className="kg-time-row" key={y}><div className="kg-time-year">{y}<small>{items.length} 篇</small></div><ul className="kg-time-papers">{items.map(p => <li key={p.id}><button type="button" className="link-button" onClick={() => update({ selected: p.id, focus: "", tab: "graph" })}>{p.label}</button><span className="muted">{visible.edges.filter(e => e.source === p.id).map(e => visible.nodes.find(n => n.id === e.target)).filter(n => n?.kind === "keyword").map(n => n!.label).join("，")}</span></li>)}</ul></div>;
        })}
        {visiblePapers.some(p => !p.year) ? <p className="muted">另有 {visiblePapers.filter(p => !p.year).length} 篇年份未知，未纳入时间脉络。</p> : null}
      </section>}
      <section className="panel kg-trends" aria-labelledby="trend-title">
        <h2 id="trend-title" className="view-title">主题的年度分布</h2>
        <p className="muted">每格为当年涉及该主题的样本论文篇数，一篇论文可属于多个主题。只反映当前样本，不代表领域热度。</p>
        {trendThemes.length ? <div className="table-scroll"><table className="heat-table">
          <thead><tr><th scope="col">主题</th>{shownYears.map(y => <th scope="col" key={y}>{y}</th>)}</tr></thead>
          <tbody>{trendThemes.map(t => <tr key={t.id}><th scope="row"><button type="button" className="link-button" onClick={() => update({ selected: t.id, focus: "", tab: "graph" })}>{t.label}</button></th>
            {shownYears.map(y => { const count = themeCount(t.id, y); return <td key={y} style={{ background: count ? `rgba(42, 83, 176, ${.08 + Math.min(count, 5) * .1})` : undefined }} aria-label={`${t.label}，${y} 年，${count} 篇`}>{count || ""}</td>; })}</tr>)}</tbody>
        </table></div> : <p className="muted">尚无主题关键词，完成论文 AI 分析后再查看。</p>}
      </section>
    </> : null}
  </main>;
}

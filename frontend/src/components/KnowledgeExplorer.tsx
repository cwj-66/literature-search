import { useMemo, useState } from "react";
import { Button, Checkbox, Empty, Input, Select } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { KIND_COLOR, KIND_LABEL, RELATIONS, type EntityGraph, type EntityNode } from "../graphData";
import { canvasGraph, ENTITY_KINDS, filterGraph, relatedPapers, topicStats } from "../knowledgeGraph";
import { ExplorerCanvas } from "./ExplorerCanvas";
import { TopicEvolution } from "./TopicEvolution";

export function KnowledgeExplorer({ data, scope, paperHref, onRefresh }: {
  data: EntityGraph; scope: string; paperHref: (node: EntityNode) => string; onRefresh: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const [resetKey, setResetKey] = useState(0);
  const query = params.get("q") ?? "";
  const from = Number(params.get("year_from")) || 0;
  const to = Number(params.get("year_to")) || 0;
  const tab = params.get("tab") === "evolution" ? "evolution" : "graph";
  const focused = data.nodes.find(n => n.paper_id === params.get("focus") || n.local_id === params.get("focus"));
  const selected = params.get("selected") ?? focused?.id ?? "";
  const relationKey = params.get("relations") ?? Object.keys(RELATIONS).join(",");
  const relations = useMemo(() => relationKey.split(",").filter(r => r in RELATIONS), [relationKey]);
  function update(values: Record<string, string>) {
    setParams(current => { const next = new URLSearchParams(current); Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key)); return next; }, { replace: true });
  }
  const visible = useMemo(() => filterGraph(data, query, from, to), [data, query, from, to]);
  const priority = visible.nodes.filter(n => n.kind === "paper").length > 36 || visible.nodes.filter(n => n.kind !== "paper").length > 90 ? selected : "";
  const canvas = useMemo(() => canvasGraph(visible, relations, priority), [visible, relations, priority]);
  const selectedNode = visible.nodes.find(n => n.id === selected);
  const papers = visible.nodes.filter(n => n.kind === "paper");
  const years = [...new Set(data.nodes.flatMap(n => n.kind === "paper" && n.year != null ? [n.year] : []))].sort((a, b) => a - b);
  const topics = useMemo(() => topicStats(visible), [visible]);
  const neighbors = selectedNode ? visible.edges.filter(e => e.source === selected || e.target === selected).map(e => ({
    edge: e, node: visible.nodes.find(n => n.id === (e.source === selected ? e.target : e.source))!,
  })).filter(item => item.node) : [];
  const related = useMemo(() => selectedNode?.kind === "paper" ? relatedPapers(visible, selectedNode) : [], [visible, selectedNode]);
  const keywordPapers = new Set(data.edges.filter(e => e.relation === "has_keyword").map(e => e.source));
  const allPapers = data.nodes.filter(n => n.kind === "paper");
  const unknownYears = allPapers.filter(n => n.year == null).length;
  function select(id: string) {
    update({ selected: id, focus: "" });
  }
  function explore(id: string, year?: number) {
    update({ selected: id, focus: "", tab: "graph", relations: "", ...(year ? { year_from: String(year), year_to: String(year) } : {}) });
  }
  function reset() {
    update({ q: "", year_from: "", year_to: "", selected: "", focus: "", relations: "" });
    setResetKey(k => k + 1);
  }
  const paperButton = (node: EntityNode, sub?: string) => <button type="button" key={node.id} onClick={() => select(node.id)}><i style={{ background: KIND_COLOR.paper }} /><span>{node.label}{sub ? <small className="kg-reason">{sub}</small> : null}</span><small>{node.year ?? "年份未知"}</small></button>;

  return <div className="kg-explorer">
    <div className="kg-summary" aria-label="图谱统计">{ENTITY_KINDS.map(kind => <div key={kind}><span><i style={{ background: KIND_COLOR[kind] }} />{KIND_LABEL[kind]}</span><strong>{visible.nodes.filter(n => n.kind === kind).length}</strong></div>)}<div><span>关系</span><strong>{visible.edges.length}</strong></div></div>
    <div className="kg-view-tabs" role="tablist" aria-label="知识图谱视图"><button role="tab" aria-selected={tab === "graph"} onClick={() => update({ tab: "graph" })}>关系网络</button><button role="tab" aria-selected={tab === "evolution"} onClick={() => update({ tab: "evolution" })}>主题演进</button><span>关键词覆盖 {keywordPapers.size}/{allPapers.length} 篇</span><Button size="small" onClick={onRefresh}>刷新数据</Button></div>
    <div className="kg-explore-toolbar">
      <Input allowClear placeholder="搜索论文、作者、关键词或刊会" aria-label="搜索图谱实体" value={query} onChange={e => update({ q: e.target.value, selected: "", focus: "" })} />
      <div className="kg-year-filters"><Select aria-label="起始年份" value={from || undefined} placeholder="起始年份" allowClear onChange={value => update({ year_from: value ? String(value) : "", selected: "", focus: "" })} options={years.map(y => ({ value: y, label: String(y), disabled: Boolean(to && y > to) }))} /><span>—</span><Select aria-label="截止年份" value={to || undefined} placeholder="截止年份" allowClear onChange={value => update({ year_to: value ? String(value) : "", selected: "", focus: "" })} options={years.map(y => ({ value: y, label: String(y), disabled: Boolean(from && y < from) }))} /></div>
      <Button onClick={reset}>一键复位</Button>
    </div>
    {from > 0 && to > 0 && from > to ? <p className="kg-inline-notice">起始年份不能晚于截止年份，请重新选择或复位。</p> : null}
    <p className="kg-data-note">当前显示 {papers.length}/{allPapers.length} 篇。{unknownYears && (from || to) ? `${unknownYears} 篇年份未知，已从年份筛选中排除。` : ""}元数据来自文献库；主题、方法和创新点来自已保存的 AI 分析。</p>
    {!keywordPapers.size ? <div className="kg-inline-notice">尚未提取研究主题。<Link to={allPapers[0] ? paperHref(allPapers[0]) : "/library"}>打开论文并执行 AI 分析</Link>，完成后刷新图谱；作者和发表渠道仍可探索。</div> : keywordPapers.size < allPapers.length ? <p className="kg-data-note">还有 {allPapers.length - keywordPapers.size} 篇论文没有有效主题，年度主题分布可能不完整。</p> : null}
    {!papers.length ? <Empty description="没有符合条件的论文。请调整搜索或年份范围。"><Button onClick={reset}>清除筛选</Button></Empty> : tab === "evolution" ? <TopicEvolution data={visible} onExplore={explore} /> : <div className="kg-workspace kg-exploration-workspace">
      <section className="kg-graph-panel" aria-label="关系网络画布">
        <div className="kg-relation-filters"><span>关系</span><Checkbox.Group value={relations} onChange={v => update({ relations: v.length ? v.join(",") : "none" })} options={Object.entries(RELATIONS).map(([value, meta]) => ({ value, label: <span className="kg-relation-label"><b style={{ borderColor: meta.color, borderTopStyle: meta.dash ? meta.dash[0] === 2 ? "dotted" : "dashed" : "solid" }} />{meta.label}</span> }))} /></div>
        <ExplorerCanvas data={canvas} selected={selectedNode?.id ?? ""} onSelect={select} scope={scope} resetKey={resetKey} />
        <div className="kg-node-key">{ENTITY_KINDS.map(kind => <span key={kind}><i style={{ background: KIND_COLOR[kind], borderRadius: kind === "paper" ? 2 : kind === "keyword" ? 0 : "50%", transform: kind === "keyword" ? "rotate(45deg)" : undefined }} />{KIND_LABEL[kind]}</span>)}</div>
        <p className="kg-caption">画布 {canvas.nodes.filter(n => n.kind === "paper").length}/{papers.length} 篇论文、{canvas.nodes.length} 个节点、{canvas.edges.length} 条关系；最多绘制 36 篇论文及 90 个关联实体。关系筛选仅影响画布，统计与演进使用全部搜索及年份筛选样本。{!data.edges.some(e => e.relation === "cites") ? "当前没有范围内的真实引用缓存，不绘制引用线。" : "引用箭头从引用者指向被引用论文。"}</p>
      </section>
      <aside className="kg-inspector kg-detail" aria-label="实体详情">
        {selectedNode ? <>
          <div className="kg-detail-top"><span className="kg-kind"><i style={{ background: KIND_COLOR[selectedNode.kind] }} />{KIND_LABEL[selectedNode.kind]}{selectedNode.year ? ` · ${selectedNode.year}` : ""}</span><button type="button" onClick={() => select("")} aria-label="关闭实体详情">×</button></div>
          <h2>{selectedNode.label}</h2>
          {!canvas.nodes.some(n => n.id === selectedNode.id) ? <p className="kg-inline-notice">此实体未显示在当前画布中。<button type="button" className="link-button" onClick={() => update({ relations: "" })}>显示全部关系</button></p> : null}
          <p className="kg-data-note">{selectedNode.kind === "keyword" ? "来源：关联论文的 AI 主题提取，点击论文可核查。" : selectedNode.provenance === "workspace_metadata" ? "来源：工作区保存的论文元数据，尚未入库。" : "来源：已入库论文元数据。"}</p>
          {selectedNode.kind === "paper" ? <>
            <Link className="inspector-open" to={paperHref(selectedNode)}>打开论文 / 分析与原文 →</Link>
            <h3>作者与发表渠道</h3><div className="kg-neighbors">{neighbors.filter(n => n.node.kind === "author" || n.node.kind === "venue").map(({ node }) => <button type="button" key={node.id} onClick={() => select(node.id)}><i style={{ background: KIND_COLOR[node.kind] }} /><span>{node.label}</span><small>{KIND_LABEL[node.kind]}</small></button>)}</div>
            {!selectedNode.venue ? <p className="kg-data-note">发表渠道未提供。</p> : null}
            <h3>摘要</h3><p className="kg-abstract">{selectedNode.abstract || "暂无摘要，请打开论文查看原文。"}</p>
            <h3>研究方法 <small>AI 提取</small></h3><p className="kg-detail-copy">{selectedNode.analysis?.technical_method || "尚无分析结果。打开论文执行 AI 分析后可查看。"}</p>
            <h3>创新点 <small>AI 提取</small></h3>{selectedNode.analysis?.innovations.length ? <ul className="kg-innovations">{selectedNode.analysis.innovations.map((text, i) => <li key={i}>{text}</li>)}</ul> : <p className="kg-detail-copy">尚无分析结果。</p>}
            {selectedNode.analysis ? <details className="kg-analysis-source"><summary>查看分析来源</summary><p>{selectedNode.analysis.analysis_basis === "full_text" ? "基于全文" : "基于摘要"} · {selectedNode.analysis.model_name}<br />{selectedNode.analysis.generated_at}<br />分析记录：{selectedNode.analysis.result_id}</p>{selectedNode.analysis.degradation_reason ? <p>{selectedNode.analysis.degradation_reason}</p> : null}<Link to={paperHref(selectedNode)}>查看原文证据与完整分析</Link></details> : null}
            <h3>研究主题</h3><div className="kg-topic-tags">{neighbors.filter(n => n.node.kind === "keyword").map(({ node }) => <button type="button" key={node.id} onClick={() => select(node.id)}>{node.label}</button>)}</div>{!neighbors.some(n => n.node.kind === "keyword") ? <p className="kg-data-note">尚未提取有效关键词。</p> : null}
            <h3>关联论文 · {related.length}</h3><p className="kg-data-note">按共同作者、AI 提取的共同主题或真实引用关联；共同主题不表示引用。</p><div className="kg-neighbors">{related.map(({ node, reasons }) => paperButton(node, reasons.join("；")))}</div>{!related.length ? <p className="kg-data-note">当前样本中尚无可确认的关联论文。</p> : null}
          </> : <>
            {selectedNode.kind === "author" ? <p className="kg-data-note">按作者姓名归并，可能存在同名作者；仅包含当前筛选样本。</p> : null}
            <h3>关联论文 · {neighbors.filter(n => n.node.kind === "paper").length}</h3>
            <div className="kg-neighbors">{neighbors.filter(n => n.node.kind === "paper").map(({ node, edge }) => paperButton(node, edge.provenance === "model" ? `AI 提取 · ${edge.analysis_basis === "full_text" ? "全文" : "摘要"} · ${edge.generated_at?.slice(0, 10) ?? ""}` : RELATIONS[edge.relation]?.label))}</div>
            {selectedNode.kind === "keyword" ? <><h3>主题年度分布</h3><div className="kg-topic-years">{years.filter(y => neighbors.some(n => n.node.year === y)).map(y => <button type="button" key={y} onClick={() => explore(selectedNode.id, y)}>{y}<strong>{neighbors.filter(n => n.node.year === y).length} 篇</strong></button>)}</div><p className="kg-data-note">按论文发表年份统计，不是关键词首次被提出的时间。</p></> : null}
          </>}
          {neighbors.some(n => n.edge.relation === "cites") ? <><h3>真实引用记录</h3><div className="kg-neighbors">{neighbors.filter(n => n.edge.relation === "cites").map(({ node, edge }) => paperButton(node, `${edge.source === selected ? "引用了" : "被此论文引用"} · Semantic Scholar 引用缓存 · ${edge.generated_at?.slice(0, 10) ?? ""}`))}</div></> : null}
        </> : <>
          <p className="kg-eyebrow">探索当前研究</p><h2>从一篇论文出发</h2><p className="kg-data-note">点击节点或下方论文，查看作者、主题、发表渠道，再沿关系继续探索。完整名称也可通过搜索定位。</p>
          {selected ? <p className="kg-inline-notice">当前选中的实体不在筛选范围内。</p> : null}
          <h3>论文 · {papers.length}</h3><div className="kg-neighbors">{papers.map(node => paperButton(node))}</div>
          {topics.length ? <><h3>常见研究主题</h3><div className="kg-topic-tags">{topics.slice(0, 12).map(t => <button type="button" key={t.id} onClick={() => select(t.id)}>{t.label}<span>{t.count}</span></button>)}</div></> : null}
        </>}
      </aside>
    </div>}
  </div>;
}

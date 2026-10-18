import { useMemo, useState } from "react";
import { Button, Checkbox, Drawer, Empty, Input, Select } from "antd";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { KIND_COLOR, KIND_LABEL, type EntityEdge, type EntityGraph, type EntityNode } from "../graphData";
import { useMediaQuery } from "../hooks";
import { canvasGraph, filterGraph } from "../knowledgeGraph";
import { ExplorerCanvas } from "./ExplorerCanvas";
import { TopicEvolution } from "./TopicEvolution";

const SHOW_DEFAULT = "paper,keyword,cites";
const RELATIONS = ["cites", "has_keyword", "authored_by", "published_in"];

function keyOf(node: EntityNode) {
  return node.local_id || node.paper_id || node.id;
}

function linkedPapers(node: EntityNode, papers: EntityNode[], edges: EntityEdge[]) {
  const relation = node.kind === "keyword" ? "has_keyword" : node.kind === "author" ? "authored_by" : "published_in";
  const ids = new Set(edges.filter(edge => edge.relation === relation && edge.target === node.id).map(edge => edge.source));
  return papers.filter(item => ids.has(item.id));
}

export function KnowledgeExplorer({ data, scope, paperHref, paperKey, workspaceId, recommendHref, onFill }: {
  data: EntityGraph; scope: string; paperHref: (node: EntityNode) => string;
  paperKey?: (node: EntityNode) => string | undefined; workspaceId?: string;
  recommendHref?: (node: EntityNode) => string;
  onFill?: (ids: string[]) => Promise<{ queried: number; failed: number }>;
}) {
  const navigate = useNavigate();
  const narrow = useMediaQuery("(max-width: 900px)");
  const [params, setParams] = useSearchParams();
  const [resetKey, setResetKey] = useState(0);
  const [picks, setPicks] = useState<string[]>([]);
  const [edgeId, setEdgeId] = useState("");
  const [filling, setFilling] = useState(false);
  const [fillNote, setFillNote] = useState("");
  const query = params.get("q") ?? "";
  const year = Number(params.get("year")) || 0;
  const tab = params.get("tab") === "evolution" ? "evolution" : "graph";
  const selected = params.get("selected") ?? "";
  const show = (params.get("show") || SHOW_DEFAULT).split(",").filter(Boolean);
  function update(values: Record<string, string>) {
    setParams(current => {
      const next = new URLSearchParams(current);
      Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
      return next;
    }, { replace: true });
  }
  const visible = useMemo(() => filterGraph(data, query, year, year), [data, query, year]);
  const shown = useMemo(() => ({
    ...visible,
    nodes: visible.nodes.filter(node => show.includes(node.kind)),
    edges: visible.edges.filter(edge => {
      if (edge.relation === "cites") return show.includes("cites") && show.includes("paper");
      if (edge.relation === "has_keyword") return show.includes("keyword") && show.includes("paper");
      if (edge.relation === "authored_by") return show.includes("author") && show.includes("paper");
      return show.includes("venue") && show.includes("paper");
    }),
  }), [visible, show]);
  const canvas = useMemo(() => canvasGraph(shown, RELATIONS, selected), [shown, selected]);
  const selectedNode = visible.nodes.find(node => node.id === selected);
  const selectedEdge = edgeId.startsWith("edge-") ? canvas.edges[Number(edgeId.slice(5))] : undefined;
  const papers = visible.nodes.filter(node => node.kind === "paper");
  const years = [...new Set(data.nodes.flatMap(node => node.kind === "paper" && node.year != null ? [node.year] : []))].sort((a, b) => a - b);
  const analyzed = papers.filter(node => node.analysis);
  const cites = visible.edges.filter(edge => edge.relation === "cites");
  const keywords = visible.nodes.filter(node => node.kind === "keyword");
  const missingKeywords = papers.filter(node => !visible.edges.some(edge => edge.relation === "has_keyword" && edge.source === node.id));
  const unknownYears = data.nodes.filter(node => node.kind === "paper" && node.year == null).length;
  const pickedPapers = papers.filter(node => picks.includes(paperKey?.(node) || keyOf(node)));
  const unanalyzed = pickedPapers.filter(node => !node.analysis);
  const readyCompare = pickedPapers.length >= 2 && pickedPapers.length <= 5 && unanalyzed.length === 0;
  function select(id: string) { setEdgeId(""); update({ selected: id }); }
  function selectEdge(id: string) {
    const edge = canvas.edges[Number(id.slice(5))];
    if (!edge || edge.relation !== "cites") return;
    setEdgeId(id);
    update({ selected: "" });
  }
  function reset() {
    setPicks([]); setEdgeId(""); setFillNote("");
    update({ q: "", year: "", selected: "", show: "" });
    setResetKey(key => key + 1);
  }
  function togglePick(node: EntityNode) {
    const key = paperKey?.(node) || keyOf(node);
    setPicks(current => current.includes(key) ? current.filter(item => item !== key) : current.length >= 5 ? current : [...current, key]);
  }
  function openCompare() {
    if (!workspaceId || !readyCompare) return;
    const search = new URLSearchParams();
    pickedPapers.forEach(node => search.append("pick", paperKey?.(node) || keyOf(node)));
    navigate(`/w/${workspaceId}/compare?${search}`);
  }
  async function fill() {
    if (!onFill || filling) return;
    setFilling(true); setFillNote("");
    try {
      const result = await onFill(papers.flatMap(node => [node.local_id, node.paper_id].filter((item): item is string => Boolean(item))));
      setFillNote(result.failed ? `已查询 ${result.queried} 篇，${result.failed} 篇未能完整获取。已保留查到的引用，外部论文不会加入工作区。` : `已查询 ${result.queried} 篇。外部论文不会加入当前工作区。`);
    } catch (err) {
      setFillNote(err instanceof Error ? err.message : "引用补全失败，请重试");
    } finally { setFilling(false); }
  }
  const idOf = (node: EntityNode) => paperKey?.(node) || keyOf(node);
  const detail = <GraphDetail node={selectedNode} edge={selectedEdge} nodes={visible.nodes} papers={papers} edges={visible.edges} picks={picks} idOf={idOf} onPick={togglePick} onSelect={select} paperHref={paperHref} recommendHref={recommendHref} readyCompare={readyCompare} unanalyzed={unanalyzed} onCompare={openCompare} canCompare={Boolean(workspaceId)} />;

  return <div className="kg-explorer">
    <div className="kg-top">
      <div className="kg-view-tabs" role="tablist" aria-label="知识图谱视图">
        <button type="button" role="tab" aria-selected={tab === "graph"} onClick={() => update({ tab: "graph" })}>关系网络</button>
        <button type="button" role="tab" aria-selected={tab === "evolution"} onClick={() => update({ tab: "evolution" })}>主题变化</button>
      </div>
      <div className="kg-head-stats" aria-label="当前范围统计">
        <div><span>论文</span><strong>{papers.length}</strong></div>
        <div><span>已分析</span><strong>{analyzed.length}</strong></div>
        <div><span>关键词</span><strong>{keywords.length}</strong></div>
        <div><span>已确认引用</span><strong>{cites.length}</strong></div>
      </div>
    </div>
    <div className="kg-explore-toolbar">
      <Input allowClear placeholder="搜索论文、关键词、作者或期刊／会议" aria-label="搜索图谱" value={query} onChange={event => update({ q: event.target.value, selected: "" })} />
      <Select aria-label="发表年份" value={year || ""} onChange={value => update({ year: value ? String(value) : "", selected: "" })} options={[{ value: "", label: "全部年份" }, ...years.map(item => ({ value: item, label: String(item) }))]} />
      <Button type="primary" loading={filling} onClick={fill} disabled={!onFill || !papers.length}>补全引用关系</Button>
      <Button onClick={reset}>复位</Button>
    </div>
    {fillNote ? <p className="kg-data-note" role="status">{fillNote}</p> : null}
    {year && unknownYears ? <p className="kg-data-note">{unknownYears} 篇缺少年份，未进入该年份的网络和统计。</p> : null}
    {missingKeywords.length ? <p className="kg-data-note">{missingKeywords.length} 篇没有关键词。未分析的论文仍显示作者和发表渠道，不推测主题。</p> : null}
    {!papers.length ? <Empty description="没有符合条件的论文"><Button onClick={reset}>清除筛选</Button></Empty> : tab === "evolution" ? <TopicEvolution data={visible} onExplore={(id, nextYear) => update({ selected: id, tab: "graph", ...(nextYear ? { year: String(nextYear) } : {}) })} /> : <>
      <div className="kg-entity-toggles">
        {([["paper", "论文"], ["keyword", "关键词"], ["cites", "引用"], ["author", "作者"], ["venue", "期刊／会议"]] as const).map(([value, label]) => <Checkbox key={value} checked={show.includes(value)} onChange={event => {
          const next = event.target.checked ? [...new Set([...show, value])] : show.filter(item => item !== value);
          update({ show: next.join(",") || "none" });
        }}>{label}</Checkbox>)}
      </div>
      <div className="kg-workspace kg-exploration-workspace">
        <section className="kg-graph-panel" aria-label="关系网络画布">
          <h2 className="kg-panel-title">当前工作区关系网络</h2>
          <ExplorerCanvas data={canvas} selected={selectedNode?.id ?? ""} onSelect={select} onSelectEdge={selectEdge} scope={scope} resetKey={resetKey} />
          <div className="kg-node-key">
            <span><i style={{ background: "#e7f0ff", border: `1.5px solid ${KIND_COLOR.paper}` }} />论文</span>
            <span><i style={{ background: "#e5f8ef", border: `1.5px solid ${KIND_COLOR.keyword}`, borderRadius: 999 }} />关键词</span>
            <span><i style={{ background: "#f3e9ff", border: `1.5px solid ${KIND_COLOR.author}`, borderRadius: "50%" }} />作者</span>
            <span><i style={{ background: "#fff6e8", border: `1.5px solid ${KIND_COLOR.venue}` }} />期刊／会议</span>
            <span>箭头：已确认引用</span>
          </div>
        </section>
        {narrow ? <Drawer open={Boolean(selectedNode || selectedEdge)} onClose={() => { select(""); setEdgeId(""); }} title="详情" placement="bottom" height="72%">{detail}</Drawer> : <aside className="kg-inspector kg-detail" aria-label="实体详情">{detail}</aside>}
      </div>
      <p className="kg-data-note">范围：当前筛选的 {papers.length} 篇论文；不代表整个研究领域。当前绘制 {canvas.nodes.filter(node => node.kind === "paper").length}/{papers.length} 篇，顶部统计使用全部筛选结果。没有共同关系的论文会单独显示。</p>
    </>}
  </div>;
}

function GraphDetail({ node, edge, nodes, papers, edges, picks, idOf, onPick, onSelect, paperHref, recommendHref, readyCompare, unanalyzed, onCompare, canCompare }: {
  node?: EntityNode; edge?: EntityEdge; nodes: EntityNode[]; papers: EntityNode[]; edges: EntityEdge[]; picks: string[];
  idOf: (node: EntityNode) => string; onPick: (node: EntityNode) => void; onSelect: (id: string) => void; paperHref: (node: EntityNode) => string;
  recommendHref?: (node: EntityNode) => string; readyCompare: boolean; unanalyzed: EntityNode[]; onCompare: () => void; canCompare: boolean;
}) {
  const byId = new Map(nodes.map(item => [item.id, item]));
  if (edge) {
    return <>
      <p className="kg-eyebrow">已确认引用</p>
      <h2>{byId.get(edge.source)?.label ?? "引用方"}</h2>
      <p className="kg-detail-copy">引用了</p>
      <h2>{byId.get(edge.target)?.label ?? "被引用方"}</h2>
      <p className="kg-data-note">箭头从引用方指向被引用方。记录来自文献平台的参考文献或被引列表，不是主题相似，也不表示改进或继承。</p>
    </>;
  }
  if (!node) return <>
    <p className="kg-eyebrow">从这里继续</p>
    <h2>选择一个节点</h2>
    <p className="kg-data-note">点击论文、关键词、作者或引用箭头，查看它在当前筛选范围内的联系。</p>
    <div className="kg-neighbors">{papers.slice(0, 8).map(item => <button type="button" key={item.id} onClick={() => onSelect(item.id)}><span>{item.label}</span><small>{item.year ?? "年份未知"}</small></button>)}</div>
  </>;
  if (node.kind === "paper") {
    const topics = edges.filter(item => item.relation === "has_keyword" && item.source === node.id).map(item => byId.get(item.target)).filter((item): item is EntityNode => Boolean(item));
    const people = edges.filter(item => item.relation === "authored_by" && item.source === node.id).map(item => byId.get(item.target)).filter((item): item is EntityNode => Boolean(item));
    return <>
      <div className="kg-detail-top"><span className="kg-kind"><i style={{ background: KIND_COLOR.paper }} />论文</span></div>
      <h2>{node.label}</h2>
      <p className="kg-detail-copy">{[node.year ?? "年份未知", node.authors?.join("、") || people.map(item => item.label).join("、") || "作者未知", node.venue || "发表渠道未知"].join(" · ")}</p>
      <p className="kg-data-note">{node.analysis ? "已完成分析。" : "尚未分析，没有关键词，不推测主题。"} {node.citation_note || "尚未查询引用关系。"}</p>
      {topics.length ? <div className="kg-topic-tags">{topics.map(item => <button type="button" key={item.id} onClick={() => onSelect(item.id)}>{item.label}</button>)}</div> : <p className="kg-data-note">当前没有可显示的关键词。</p>}
      <div className="kg-detail-links">
        <Link to={paperHref(node)}>查看分析</Link>
        {recommendHref ? <Link to={recommendHref(node)}>相关推荐</Link> : null}
        <button type="button" onClick={() => onPick(node)}>{picks.includes(idOf(node)) ? "已选入对比" : "加入对比"}</button>
      </div>
      {canCompare ? <CompareBar ready={readyCompare} unanalyzed={unanalyzed} onCompare={onCompare} /> : null}
    </>;
  }
  const related = linkedPapers(node, papers, edges);
  return <>
    <div className="kg-detail-top"><span className="kg-kind"><i style={{ background: KIND_COLOR[node.kind] }} />{node.kind === "keyword" ? "已选关键词" : KIND_LABEL[node.kind]}</span></div>
    <h2>{node.label}</h2>
    <p className="kg-detail-count">关联论文 {related.length} 篇</p>
    <p className="kg-data-note">{node.kind === "keyword" ? "以下论文的已有分析包含该关键词。" : node.kind === "author" ? "按平台作者标识归并。同名但标识不同不会当成同一作者。" : "以下论文发表在该渠道。"}</p>
    <ul className="kg-pick-list">{related.map(item => <li key={item.id}>
      <Checkbox checked={picks.includes(idOf(item))} onChange={() => onPick(item)} aria-label={`选择 ${item.label}`} />
      <div><strong>{item.label}</strong><span>{item.year ?? "年份未知"} · {item.analysis ? "已分析" : "未分析"}</span></div>
      <Link to={paperHref(item)}>查看分析</Link>
    </li>)}</ul>
    {!related.length ? <p className="kg-data-note">当前筛选范围内没有关联论文。</p> : null}
    {canCompare ? <CompareBar ready={readyCompare} unanalyzed={unanalyzed} onCompare={onCompare} /> : null}
  </>;
}

function CompareBar({ ready, unanalyzed, onCompare }: { ready: boolean; unanalyzed: EntityNode[]; onCompare: () => void }) {
  return <div className="kg-compare-bar">
    <Button type="primary" block disabled={!ready} onClick={onCompare}>{ready ? "加入对比" : "选择论文后加入对比"}</Button>
    {unanalyzed.length ? <p className="kg-data-note">这些论文尚未完成分析，不能加入对比：{unanalyzed.map(item => item.label).join("、")}。</p> : <p className="kg-data-note">仅可对比 2–5 篇已分析论文。</p>}
  </div>;
}

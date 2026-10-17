import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Empty, Skeleton } from "antd";
import { Link, useNavigate } from "react-router-dom";
import { GraphEmpty } from "../../components/EmptyState";
import { KnowledgeCanvas } from "../../components/KnowledgeCanvas";
import { KIND_COLOR, KIND_LABEL, type EntityGraph, type EntityKind, type EntityNode } from "../../graphData";
import { paperLocation } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

const KINDS: EntityKind[] = ["paper", "author", "keyword", "venue"];

function scopedGraph(raw: EntityGraph, ids: Set<string>): EntityGraph {
  const papers = raw.nodes.filter(node => node.kind === "paper" && (ids.has(node.paper_id ?? "") || ids.has(node.id.slice(6)))).slice(0, 16);
  const paperIds = new Set(papers.map(node => node.id));
  const edges = raw.edges.filter(edge => paperIds.has(edge.source));
  const rank = new Map<string, number>();
  edges.forEach(edge => rank.set(edge.target, (rank.get(edge.target) || 0) + 1));
  const extra = KINDS.filter(kind => kind !== "paper").flatMap(kind => raw.nodes.filter(node => node.kind === kind && rank.has(node.id))
    .sort((a, b) => (rank.get(b.id) || 0) - (rank.get(a.id) || 0)).slice(0, kind === "venue" ? 5 : 12));
  const nodes = [...papers, ...extra];
  const keep = new Set(nodes.map(node => node.id));
  return { nodes, edges: edges.filter(edge => keep.has(edge.target)), total_papers: papers.length, scope: "" };
}

export default function GraphPage() {
  const navigate = useNavigate();
  const { id, project } = useWorkspaceProject();
  const [raw, setRaw] = useState<EntityGraph | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState("");
  const here = `/w/${id}/graph`;
  const ids = useMemo(() => new Set(Object.entries(project?.papers ?? {}).flatMap(([key, item]) => [key, item.paper.id])), [project]);
  useEffect(() => { document.title = project ? `${project.name} - 知识图谱` : "工作区"; }, [project]);
  useEffect(() => {
    if (!ids.size) { setRaw(null); return; }
    const controller = new AbortController();
    setError(""); setRaw(null);
    fetch("/api/knowledge-graph", { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error("知识图谱加载失败");
      return await response.json() as EntityGraph;
    }).then(result => { if (!controller.signal.aborted) setRaw(result); })
      .catch(err => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "加载失败，请重试"); });
    return () => controller.abort();
  }, [ids.size, retry]);
  const data = useMemo(() => raw ? scopedGraph(raw, ids) : { nodes: [], edges: [], total_papers: 0, scope: "" }, [raw, ids]);
  if (!project) return <MissingWorkspace />;
  const papers = data.nodes.filter(node => node.kind === "paper");
  const selectedNode = data.nodes.find(node => node.id === selected);
  const neighbors = selected ? data.edges.filter(edge => edge.source === selected || edge.target === selected)
    .map(edge => data.nodes.find(node => node.id === (edge.source === selected ? edge.target : edge.source))).filter((node): node is EntityNode => Boolean(node)) : [];
  const count = Object.keys(project.papers).length;
  if (!count) return <main className="ws-page kg-page ws-empty-page">
    <header className="ws-page-head">
      <div>
        <h1>知识图谱</h1>
        <p>基于工作区中的论文、作者和关键词，生成可视化的知识图谱，帮助你发现研究脉络与关联。</p>
      </div>
    </header>
    <GraphEmpty workspaceId={id} />
  </main>;
  return <main className="ws-page kg-page">
    <header className="ws-page-head">
      <div>
        <h1>知识图谱</h1>
        <p>这个工作区里的论文，以及它们的作者、关键词、期刊或会议。关键词来自已有的 AI 分析。</p>
      </div>
    </header>
    {count && error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(value => value + 1)}>重试</Button>} /> : null}
    {count && !raw && !error ? <Skeleton active paragraph={{ rows: 6 }} /> : null}
    {count && raw && !papers.length ? <Empty description="这些论文还没有进入本地知识图谱。收藏进文献库后，这里会显示作者和期刊。" /> : null}
    {papers.length ? <>
      <p className="kg-counts">{KINDS.map(kind => <span key={kind}><i style={{ background: KIND_COLOR[kind] }} />{KIND_LABEL[kind]} {data.nodes.filter(node => node.kind === kind).length}</span>)}<span>关系 {data.edges.length}</span></p>
      <div className="kg-workspace">
        <section className="kg-graph-panel" aria-label="关系图">
          <KnowledgeCanvas scope={`ws-${id}`} data={data} selected={selected} onSelect={setSelected} />
          <p className="kg-caption">最多展示 16 篇论文。灰色连线表示作者、关键词或刊会，不表示引用。</p>
        </section>
        <aside className="kg-inspector" aria-label="节点详情">
          {selectedNode ? <>
            <p className="kg-kind"><i style={{ background: KIND_COLOR[selectedNode.kind] }} />{KIND_LABEL[selectedNode.kind]}{selectedNode.year ? ` · ${selectedNode.year}` : ""}</p>
            <h2>{selectedNode.label}</h2>
            {selectedNode.paper_id ? <Link className="inspector-open" to={paperLocation(selectedNode.paper_id, here)}>打开论文</Link> : null}
            <h3>关联 {neighbors.length}</h3>
            <div className="kg-neighbors">{neighbors.map(node => <button type="button" key={node.id} onClick={() => node.kind === "paper" && node.paper_id ? navigate(paperLocation(node.paper_id, here)) : setSelected(node.id)}><i style={{ background: KIND_COLOR[node.kind] }} /><span>{node.label}</span><small>{KIND_LABEL[node.kind]}</small></button>)}</div>
          </> : <>
            <h2>论文 {papers.length}</h2>
            <p className="muted">点击论文，查看它和作者、主题的关系。</p>
            <div className="kg-neighbors">{papers.map(node => <button type="button" key={node.id} onClick={() => setSelected(node.id)}><span>{node.label}</span><small>{node.year ?? "年份未知"}</small></button>)}</div>
          </>}
        </aside>
      </div>
    </> : null}
  </main>;
}

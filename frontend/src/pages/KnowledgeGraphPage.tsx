import { useEffect, useMemo } from "react";
import { Alert, Button, Skeleton } from "antd";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { fillGraphCitations, fillGraphSimilar } from "../api";
import { KnowledgeExplorer } from "../components/KnowledgeExplorer";
import { useProjects } from "../projects";
import { useKnowledgeGraph, withWorkspaceMetadata, workspaceNodeKey } from "../useKnowledgeGraph";
import { paperLocation, recommendLocation, safeReturn, workspacePaperPath } from "../workspace";

export default function KnowledgeGraphPage() {
  const { current: project } = useProjects();
  const [params] = useSearchParams();
  const location = useLocation();
  const inProject = (params.get("mode") === "project" || !params.has("mode")) && Boolean(project);
  const references = inProject ? Object.entries(project!.papers).flatMap(([key, item]) => [key, item.paper.id]) : undefined;
  const { data: raw, error, refresh } = useKnowledgeGraph(references);
  const data = useMemo(() => raw && inProject && project ? withWorkspaceMetadata(raw, project) : raw, [raw, inProject, project]);
  const here = location.pathname + location.search;
  const from = safeReturn(params.get("from"), "/library");
  useEffect(() => { document.title = "知识图谱 - 文献研读"; }, []);
  return <main className="page page-wide kg-page">
    <Link to={from} className="back-link">← 返回研究</Link>
    <header className="page-head"><div><h1 className="page-title">知识图谱</h1><p className="muted">{inProject ? `工作区「${project!.name}」` : "本地文献库"} · 基于真实论文与已保存的分析结果</p></div></header>
    {error ? <Alert type="error" showIcon message={error} action={<Button onClick={refresh}>重试</Button>} /> : null}
    {!data && !error ? <Skeleton active paragraph={{ rows: 8 }} /> : null}
    {data ? <>{!inProject && data.total_papers > data.nodes.filter(n => n.kind === "paper").length ? <p className="kg-inline-notice">文献库共 {data.total_papers} 篇，此处统计最近 2000 篇。进入具体工作区可按其论文范围探索。</p> : null}<KnowledgeExplorer data={data} scope={inProject ? `workspace-${project!.id}` : "library"} workspaceId={inProject ? project!.id : undefined} references={references} onFill={async ids => { const result = await fillGraphCitations(ids); refresh(); return result; }} onFillSimilar={async ids => { const result = await fillGraphSimilar(ids); refresh(); return result; }} paperKey={node => project ? workspaceNodeKey(project, node) : undefined} paperHref={node => {
      const key = project ? workspaceNodeKey(project, node) : undefined;
      return inProject && key ? workspacePaperPath(project!.id, key, here) : paperLocation(node.paper_id ?? node.local_id ?? "", here);
    }} recommendHref={node => recommendLocation((project && workspaceNodeKey(project, node)) || node.paper_id || node.local_id || "", here)} /></> : null}
  </main>;
}

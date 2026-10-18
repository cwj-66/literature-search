import { useEffect, useMemo } from "react";
import { Alert, Button, Skeleton } from "antd";
import { useLocation } from "react-router-dom";
import { fillGraphCitations } from "../../api";
import { GraphEmpty } from "../../components/EmptyState";
import { KnowledgeExplorer } from "../../components/KnowledgeExplorer";
import { useKnowledgeGraph, withWorkspaceMetadata, workspaceNodeKey } from "../../useKnowledgeGraph";
import { paperLocation, recommendLocation, workspacePaperPath } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

export default function GraphPage() {
  const { id, project } = useWorkspaceProject();
  const location = useLocation();
  const references = Object.entries(project?.papers ?? {}).flatMap(([key, item]) => [key, item.paper.id]);
  const { data: raw, error, refresh } = useKnowledgeGraph(references);
  const data = useMemo(() => raw && project ? withWorkspaceMetadata(raw, project) : null, [raw, project]);
  const here = location.pathname + location.search;
  useEffect(() => { document.title = project ? `${project.name} - 知识图谱` : "工作区"; }, [project]);
  if (!project) return <MissingWorkspace />;
  const count = Object.keys(project.papers).length;
  const snapshots = data?.nodes.filter(n => n.kind === "paper" && n.provenance === "workspace_metadata").length ?? 0;
  return <main className="ws-page kg-page">
    <header className="ws-page-head"><div><h1>知识图谱</h1><p>把当前工作区的论文联系起来，沿主题、作者和引用继续探索。</p></div></header>
    {!count ? <GraphEmpty workspaceId={id} /> : <>
      {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={refresh}>重试</Button>} /> : null}
      {!raw && !error ? <Skeleton active paragraph={{ rows: 8 }} /> : null}
      {snapshots ? <p className="kg-data-note">{snapshots} 篇论文尚未入库，展示其真实工作区元数据。打开论文执行 AI 分析后，可补齐主题和方法信息。</p> : null}
      {data ? <KnowledgeExplorer data={data} scope={`workspace-${id}`} workspaceId={id} onFill={async ids => { const result = await fillGraphCitations(ids); refresh(); return result; }} paperKey={node => workspaceNodeKey(project, node)} paperHref={node => {
        const key = workspaceNodeKey(project, node);
        return key ? workspacePaperPath(id, key, here) : paperLocation(node.paper_id ?? node.local_id ?? "", here);
      }} recommendHref={node => {
        const key = workspaceNodeKey(project, node) || node.paper_id || node.local_id || "";
        return recommendLocation(key, here);
      }} /> : null}
    </>}
  </main>;
}

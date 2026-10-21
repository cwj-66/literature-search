import { useState } from "react";
import { Button, Dropdown, Tooltip, message } from "antd";
import { BookOutlined, CheckOutlined, PlusOutlined } from "@ant-design/icons";
import { useLocation } from "react-router-dom";
import { importPaper } from "../api";
import { findKey, projects, useProjects } from "../projects";
import type { Paper } from "../types";
import { workspaceContext } from "../workspace";

function snapshotOf(paper: Paper) {
  return {
    title: paper.title, authors: paper.authors, year: paper.year,
    venue: paper.venue, abstract: paper.abstract, url: paper.url,
    citation_count: paper.citation_count,
  };
}

export function CollectButton({ paper, onSaved, size = "small", iconOnly }: { paper: Paper; onSaved?: (paper: Paper) => void; size?: "small" | "middle"; iconOnly?: boolean }) {
  const location = useLocation();
  const ctx = workspaceContext(location.pathname, location.search);
  const { projects: list, current } = useProjects();
  // Inside a workspace the target is that workspace; on global pages the user picks one.
  const target = ctx ? list.find(item => item.id === ctx.id) ?? current : undefined;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const collected = Boolean(findKey(target, paper));
  async function collectTo(projectId: string, projectName: string) {
    setLoading(true); setError("");
    try {
      const { paper: saved } = await importPaper(paper.id, snapshotOf(paper));
      projects.addTo(projectId, saved);
      if (!target) message.success(`已收藏到「${projectName}」`);
      onSaved?.(saved);
    } catch (err) {
      const text = err instanceof Error ? err.message : "收藏失败，请重试";
      if (target) setError(text); else message.error(text);
    } finally { setLoading(false); }
  }
  if (target) {
    const scope = target;
    async function save() { await collectTo(scope.id, scope.name); }
    if (collected) {
      if (iconOnly) return <Tooltip title={`已在「${target.name}」`}><span className="saved-mark saved-mark-icon" role="img" aria-label={`已在「${target.name}」`}><CheckOutlined /></span></Tooltip>;
      return <span className="saved-mark"><CheckOutlined aria-hidden />已在「{target.name}」</span>;
    }
    if (iconOnly) return <span className="import-action">
      <Tooltip title={`收藏到「${target.name}」`}>
        <Button size={size} type="text" aria-label={`收藏到「${target.name}」`} icon={<BookOutlined />} loading={loading} onClick={save} />
      </Tooltip>
      {error ? <span className="inline-error" role="alert">{error}</span> : null}
    </span>;
    return <span className="import-action">
      <Button size={size} icon={<BookOutlined />} loading={loading} onClick={save}>收藏到「{target.name}」</Button>
      {error ? <span className="inline-error" role="alert">{error}</span> : null}
    </span>;
  }
  const items = [
    ...list.map(project => ({
      key: project.id,
      icon: findKey(project, paper) ? <CheckOutlined /> : undefined,
      label: project.name,
    })),
    { key: "__new__", icon: <PlusOutlined />, label: "新建工作区并收藏" },
  ];
  const button = iconOnly
    ? <Button size={size} type="text" aria-label="收藏" icon={<BookOutlined />} loading={loading} />
    : <Button size={size} icon={<BookOutlined />} loading={loading}>收藏</Button>;
  return <Dropdown trigger={["click"]} menu={{
    items,
    onClick: ({ key }) => {
      if (key === "__new__") {
        void collectTo(projects.create("新的研究工作区"), "新的研究工作区");
        return;
      }
      const project = list.find(item => item.id === key);
      if (project) void collectTo(project.id, project.name);
    },
  }}>{iconOnly ? <Tooltip title="收藏到工作区">{button}</Tooltip> : button}</Dropdown>;
}

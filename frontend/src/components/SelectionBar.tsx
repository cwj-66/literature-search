import { useState } from "react";
import { Button } from "antd";
import { useNavigate } from "react-router-dom";
import { fetchPaper, prepareWorkspace } from "../api";
import { findKey, projects, useProjects } from "../projects";
import { selection, useSelection } from "../selection";

export function SelectionBar() {
  const items = useSelection();
  const { current } = useProjects();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  if (!items.length) return null;
  async function open(compare = false) {
    if (!current || busy) return;
    setBusy(true); setError("");
    try {
      const papers = await Promise.all(items.map(item => fetchPaper(item.id)));
      projects.select(current.id);
      papers.forEach(paper => projects.add(paper));
      const refs = papers.map(paper => paper.local_id ?? paper.id);
      await prepareWorkspace(current.id, current.name, [...new Set([...Object.keys(current.papers), ...refs])]);
      if (compare) navigate(`/w/${current.id}/compare/table?${new URLSearchParams(refs.map(ref => ["pick", ref]))}`);
      else {
        navigate(`/w/${current.id}/studio?papers=${encodeURIComponent(refs.join(","))}`);
      }
      selection.clear();
    } catch (err) { setError(err instanceof Error ? err.message : "打开智能问答失败"); }
    finally { setBusy(false); }
  }
  const missing = current ? items.filter(item => !findKey(current, { id: item.id, local_id: item.id })).length : 0;
  return <div className="selection-bar" role="region" aria-label="已选论文操作"><div className="selection-bar-inner">
    <span className="selection-count">已选 <strong>{items.length}</strong> 篇</span>
    <span className="selection-hint">{current ? `在「${current.name}」的智能问答中提问${missing ? "（会先加入所选论文）" : ""}` : "请先创建或打开一个研究工作区"}</span>
    <div className="selection-actions">
      <Button type="text" disabled={busy} onClick={() => selection.clear()}>清除</Button>
      <Button disabled={busy || !current || items.length < 3 || items.length > 5} onClick={() => open(true)}>对比</Button>
      <Button type="primary" disabled={!current} loading={busy} onClick={() => current ? open() : navigate("/")}>加入工作区并提问</Button>
    </div>
    {error ? <span role="alert" className="inline-error">{error}</span> : null}
  </div></div>;
}

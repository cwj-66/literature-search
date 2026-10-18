import { useEffect, useState } from "react";
import { Alert, Button, Checkbox } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { compareLibrary } from "../../api";
import { CompareEmpty } from "../../components/EmptyState";
import { formatAuthors } from "../../components/PaperRow";
import type { CompareTable } from "../../types";
import { paperLocation } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

function clip(text: string | null) {
  if (!text) return "无摘要";
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

export default function ComparePage() {
  const { id, project } = useWorkspaceProject();
  const [picked, setPicked] = useState<string[]>([]);
  const [table, setTable] = useState<CompareTable | null>(null);
  const [tableIds, setTableIds] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [params] = useSearchParams();
  const here = `/w/${id}/compare`;
  useEffect(() => { document.title = project ? `${project.name} - 论文对比` : "工作区"; }, [project]);
  useEffect(() => {
    const initial = params.getAll("pick").filter(key => project?.papers[key]);
    if (initial.length) setPicked(current => [...new Set([...current, ...initial])].slice(0, 5));
  }, [params, project]);
  if (!project) return <MissingWorkspace />;
  const entries = Object.entries(project.papers);
  const selected = entries.filter(([key]) => picked.includes(key));
  const signature = picked.join("|");
  function toggle(key: string) {
    setPicked(current => current.includes(key) ? current.filter(item => item !== key) : current.length >= 5 ? current : [...current, key]);
  }
  function run() {
    if (selected.length < 3 || selected.length > 5 || busy) return;
    const mark = signature;
    const ids = selected.map(([key]) => key);
    setBusy(true); setError("");
    compareLibrary(ids).then(data => { setTable(data); setTableIds(mark); })
      .catch((err: Error) => setError(err.message || "生成对比表失败"))
      .finally(() => setBusy(false));
  }
  const cell = (row: CompareTable["rows"][number], dimension: string) => row.cells.find(item => item.dimension === dimension);
  return <main className={entries.length ? "ws-page" : "ws-page ws-empty-page"}>
    <header className="ws-page-head">
      <div>
        <h1>论文对比</h1>
        <p>{entries.length ? "选出 2–5 篇并排查看年份、作者和摘要。选出 3–5 篇后，可以按已有的 AI 分析生成对比表。" : "选择 2–5 篇论文进行对比，分析它们的研究问题、方法、创新点和实验结果。"}</p>
      </div>
    </header>
    {!entries.length ? <CompareEmpty workspaceId={id} /> : <>
      <ul className="ws-pick">
        {entries.map(([key, item]) => <li key={key}>
          <Checkbox checked={picked.includes(key)} disabled={!picked.includes(key) && picked.length >= 5} onChange={() => toggle(key)}>{item.paper.title}</Checkbox>
        </li>)}
      </ul>
      {selected.length < 2 ? <p className="empty-hint">{selected.length ? "再选一篇就可以并排查看。" : "勾选论文后在这里比较。"}</p> : <div className="table-scroll" tabIndex={0} aria-label="论文基本信息对比">
        <table className="compare-table">
          <thead><tr><th scope="col">项目</th>{selected.map(([key, item]) => <th scope="col" key={key}><Link to={paperLocation(key, here)}>{item.paper.title}</Link></th>)}</tr></thead>
          <tbody>
            <tr><th scope="row">年份</th>{selected.map(([key, item]) => <td key={key}>{item.paper.year ?? "未知"}</td>)}</tr>
            <tr><th scope="row">作者</th>{selected.map(([key, item]) => <td key={key}>{formatAuthors(item.paper.authors, 3)}</td>)}</tr>
            <tr><th scope="row">期刊／会议</th>{selected.map(([key, item]) => <td key={key}>{item.paper.venue || "未知"}</td>)}</tr>
            <tr><th scope="row">引用</th>{selected.map(([key, item]) => <td key={key}>{item.paper.citation_count ?? "未知"}</td>)}</tr>
            <tr><th scope="row">摘要</th>{selected.map(([key, item]) => <td key={key}>{clip(item.paper.abstract)}</td>)}</tr>
          </tbody>
        </table>
      </div>}
      <div className="ws-compare-actions">
        <span className="small-print">AI 对比表汇总各论文已有分析；还没分析过的维度会显示为未提取到。</span>
        <Button type="primary" disabled={selected.length < 3 || selected.length > 5} loading={busy} onClick={run}>{table && tableIds === signature ? "重新生成" : "生成 AI 对比表"}</Button>
      </div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {table && tableIds !== signature ? <Alert type="info" showIcon message="选择已变化，下表仍是上次的结果。" /> : null}
      {table ? <section aria-label="AI 对比表">
        {table.notes.map(note => <Alert key={note} type="warning" showIcon message={note} />)}
        <div className="table-scroll" tabIndex={0} aria-label="AI 对比表，可横向滚动">
          <table className="compare-table">
            <thead><tr><th scope="col">维度</th>{table.rows.map(row => <th scope="col" key={row.paper_id}><Link to={paperLocation(row.paper_id, here)}>{row.title}</Link></th>)}</tr></thead>
            <tbody>{table.dimensions.map(dimension => <tr key={dimension}>
              <th scope="row">{dimension}</th>
              {table.rows.map(row => { const value = cell(row, dimension); return <td key={row.paper_id} className={!value || value.missing ? "is-missing" : undefined}>{!value || value.missing ? "未提取到" : value.text}</td>; })}
            </tr>)}</tbody>
          </table>
        </div>
      </section> : null}
    </>}
  </main>;
}

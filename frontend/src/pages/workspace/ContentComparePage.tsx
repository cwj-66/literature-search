import { pageTitle } from "../../presentation";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Alert, Button, Checkbox } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { compareLibrary } from "../../api";
import { AnalysisBadge, compareBlockReason, useAnalysisStates } from "../../components/AnalysisBadge";
import { CompareMatrix } from "../../components/CompareMatrix";
import { CompareEmpty } from "../../components/EmptyState";
import { formatAuthors } from "../../components/PaperRow";
import type { CompareTable } from "../../types";
import { workspacePaperPath } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

export default function ComparePage() {
  const { id, project } = useWorkspaceProject();
  const [picked, setPicked] = useState<string[]>([]);
  const [table, setTable] = useState<CompareTable | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [params] = useSearchParams();
  const request = useRef(0);
  const seen = useRef<string | null>(null);
  const here = `/w/${id}/compare/table`;
  const signature = picked.join("|");
  useEffect(() => { document.title = pageTitle(project ? `${project.name} - 论文对比` : "工作区"); }, [project]);
  useEffect(() => {
    if (seen.current === null) { seen.current = signature; return; }
    if (seen.current === signature) return;
    seen.current = signature;
    request.current += 1;
    setTable(null);
    setError("");
    setBusy(false);
  }, [signature]);
  useEffect(() => {
    const initial = params.getAll("pick").filter(key => project?.papers[key]);
    if (!initial.length) return;
    setPicked(current => {
      const next = [...new Set([...current, ...initial])].slice(0, 5);
      return next.join("|") === current.join("|") ? current : next;
    });
  }, [params, project]);
  const entries = Object.entries(project?.papers ?? {});
  const states = useAnalysisStates(entries.map(([key]) => key));
  if (!project) return <MissingWorkspace />;
  const selected = entries.filter(([key]) => picked.includes(key));
  const pending = selected.some(([key]) => states[key] === undefined);
  const blockers = selected.flatMap(([key, item]) => {
    const reason = compareBlockReason(states[key]);
    return reason && reason !== "状态读取中" ? [{ key, title: item.paper.title, reason }] : [];
  });
  const ready = selected.length >= 2 && selected.length <= 5 && !pending && !blockers.length;

  function toggle(key: string) {
    setPicked(current => current.includes(key) ? current.filter(item => item !== key) : current.length >= 5 ? current : [...current, key]);
  }
  function run() {
    if (!ready || busy) return;
    const token = ++request.current;
    const ids = selected.map(([key]) => key);
    setBusy(true);
    setError("");
    compareLibrary(ids).then(data => { if (request.current === token) setTable(data); })
      .catch((err: Error) => { if (request.current === token) setError(err.message || "生成对比表失败"); })
      .finally(() => { if (request.current === token) setBusy(false); });
  }
  return <main className={entries.length ? "ws-page" : "ws-page ws-empty-page"}>
    <header className="ws-page-head">
      <div>
        <h1>论文对比</h1>
        <p>{entries.length
          ? "选择 2–5 篇论文。选中后先看基本信息；全部完成单篇分析后，再生成研究内容对比表。打开本页或生成对比表都不会查找相关论文。"
          : "选择 2–5 篇论文，在全部完成单篇分析后对比研究内容。"}</p>
      </div>
    </header>
    {!entries.length ? <CompareEmpty workspaceId={id} /> : <>
      <ul className="ws-pick">
        {entries.map(([key, item]) => <li key={key}>
          <Checkbox checked={picked.includes(key)} disabled={!picked.includes(key) && picked.length >= 5} onChange={() => toggle(key)}>{item.paper.title}</Checkbox>
          <span className="ws-pick-side">
            <AnalysisBadge state={states[key]} />
            <Link to={workspacePaperPath(id, key, here)}>查看分析</Link>
          </span>
        </li>)}
      </ul>
      {selected.length < 2 ? <p className="empty-hint">{selected.length ? "再选一篇就可以并排查看基本信息。" : "勾选 2–5 篇论文后在这里比较。"}</p> : <>
        <h2 className="compare-section-title">基本信息</h2>
        <div className="table-scroll" tabIndex={0} aria-label="论文基本信息对比">
          <table className="compare-table" style={{ "--compare-cols": selected.length } as CSSProperties}>
            <thead><tr><th scope="col">项目</th>{selected.map(([key, item]) => <th scope="col" key={key}><Link to={workspacePaperPath(id, key, here)}>{item.paper.title}</Link></th>)}</tr></thead>
            <tbody>
              <tr><th scope="row">年份</th>{selected.map(([key, item]) => <td key={key}>{item.paper.year ?? "未知"}</td>)}</tr>
              <tr><th scope="row">作者</th>{selected.map(([key, item]) => <td key={key}>{formatAuthors(item.paper.authors, 4)}</td>)}</tr>
              <tr><th scope="row">期刊／会议</th>{selected.map(([key, item]) => <td key={key}>{item.paper.venue || "未知"}</td>)}</tr>
              <tr><th scope="row">被引用次数</th>{selected.map(([key, item]) => <td key={key}>{item.paper.citation_count ?? "未知／暂不可用"}</td>)}</tr>
              <tr><th scope="row">摘要</th>{selected.map(([key, item]) => <td key={key} className="compare-abstract">{item.paper.abstract || "无摘要"}</td>)}</tr>
            </tbody>
          </table>
        </div>
      </>}
      <div className="ws-compare-actions">
        <span className="small-print">生成对比表只读取已保存的分析结果，不会重新分析，也不会调用大模型。</span>
        <Button type="primary" disabled={!ready} loading={busy} onClick={run}>生成对比表</Button>
      </div>
      {pending && selected.length >= 2 ? <p className="muted">正在确认各论文的分析状态。</p> : null}
      {blockers.length ? <Alert type="warning" showIcon message="以下论文尚未完成分析，不能生成对比表" description={
        <ul className="compare-blockers">{blockers.map(item => <li key={item.key}>{item.title}（{item.reason}）<Link to={workspacePaperPath(id, item.key, here)}>进入分析页</Link></li>)}</ul>
      } /> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {table ? <CompareMatrix table={table} hrefFor={paperId => workspacePaperPath(id, paperId, here)} /> : null}
    </>}
  </main>;
}

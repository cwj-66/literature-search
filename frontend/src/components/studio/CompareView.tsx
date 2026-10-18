import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import { CloseOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { compareLibrary } from "../../api";
import { useSessionState } from "../../hooks";
import { selection, type Picked } from "../../selection";
import type { CompareTable } from "../../types";
import { paperLocation } from "../../workspace";
import { AnalysisBadge, compareBlockReason, type AnalysisLookup } from "../AnalysisBadge";
import { CompareMatrix } from "../CompareMatrix";

export function CompareView({ picked, states, from }: { picked: Picked[]; states: AnalysisLookup; from: string }) {
  const [table, setTable] = useSessionState<{ ids: string[]; data: CompareTable } | null>("studio-compare", null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const signature = picked.map(item => item.id).join("|");
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (table && table.ids.join("|") !== signature) setTable(null);
  }, [signature, table, setTable]);
  const blockers = picked.flatMap(item => {
    const reason = compareBlockReason(states[item.id]);
    return reason && reason !== "状态读取中" ? [{ ...item, reason }] : [];
  });
  const pending = picked.some(item => states[item.id] === undefined);
  const ready = picked.length >= 2 && picked.length <= 5 && !pending && !blockers.length;
  function run() {
    if (!ready || busy) return;
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true); setError("");
    const ids = picked.map(item => item.id);
    compareLibrary(ids, abort.signal).then(data => setTable({ ids, data }))
      .catch((err: Error) => { if (!abort.signal.aborted) setError(err.message || "生成对比表失败"); })
      .finally(() => setBusy(false));
  }
  const analysisHref = (paperId: string) => `${paperLocation(paperId, from)}#analysis`;
  return <div className="studio-view">
    <div className="composer">
      <h2 className="view-title">对比 {picked.length ? `${picked.length} 篇论文` : ""}</h2>
      {picked.length < 2 || picked.length > 5 ? <p className="field-help">{picked.length < 2 ? `在论文范围中勾选 2–5 篇论文，还需要 ${2 - picked.length} 篇。` : "最多比较 5 篇，请移除部分论文。"}</p> : null}
      {picked.length ? <ul className="compare-picked">{picked.map(item => <li key={item.id}><AnalysisBadge state={states[item.id]} /><Link to={analysisHref(item.id)}>{item.title}</Link><Button type="text" size="small" icon={<CloseOutlined />} aria-label={`移除：${item.title}`} onClick={() => selection.remove(item.id)} /></li>)}</ul> : null}
      {blockers.length ? <Alert type="warning" showIcon message="以下论文尚未完成分析，不能生成对比表" description={
        <ul className="compare-blockers">{blockers.map(item => <li key={item.id}>{item.title}（{item.reason}）<Link to={analysisHref(item.id)}>进入分析页</Link></li>)}</ul>
      } /> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
      <div className="composer-actions">
        <span className="muted">生成对比表只读取已保存的分析结果，不会调用大模型</span>
        {busy ? <Button onClick={() => controller.current?.abort()}>停止等待</Button> : null}
        <Button type="primary" disabled={!ready} loading={busy} onClick={run}>生成对比表</Button>
      </div>
    </div>
    {table ? <CompareMatrix table={table.data} hrefFor={analysisHref} /> : null}
  </div>;
}

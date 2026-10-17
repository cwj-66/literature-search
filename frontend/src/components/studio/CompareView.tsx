import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import { CloseOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { compareLibrary } from "../../api";
import { useSessionState } from "../../hooks";
import { selection, type Picked } from "../../selection";
import type { CompareTable } from "../../types";
import { paperLocation } from "../../workspace";
import { AnalysisBadge, isAnalyzed, type AnalysisLookup } from "../AnalysisBadge";

export function CompareView({ picked, states, from }: { picked: Picked[]; states: AnalysisLookup; from: string }) {
  const [table, setTable] = useSessionState<{ ids: string[]; data: CompareTable } | null>("studio-compare", null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const ready = picked.length >= 3 && picked.length <= 5;
  const unanalyzed = picked.filter(item => states[item.id] !== undefined && !isAnalyzed(states[item.id]));
  const stale = table && table.ids.join("|") !== picked.map(item => item.id).join("|");
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
  const cell = (row: CompareTable["rows"][number], dimension: string) => row.cells.find(item => item.dimension === dimension);
  return <div className="studio-view">
    <div className="composer">
      <h2 className="view-title">对比 {picked.length ? `${picked.length} 篇论文` : ""}</h2>
      {!ready ? <p className="field-help">{picked.length < 3 ? `在论文范围中勾选 3–5 篇论文，还需要 ${3 - picked.length} 篇。` : "最多比较 5 篇，请移除部分论文。"}</p> : null}
      {picked.length ? <ul className="compare-picked">{picked.map(item => <li key={item.id}><AnalysisBadge state={states[item.id]} /><Link to={paperLocation(item.id, from)}>{item.title}</Link><Button type="text" size="small" icon={<CloseOutlined />} aria-label={`移除：${item.title}`} onClick={() => selection.remove(item.id)} /></li>)}</ul> : null}
      {ready && unanalyzed.length ? <Alert type="warning" showIcon message={`${unanalyzed.length} 篇尚未分析，对比表中对应单元格会显示为缺失。可先在论文页完成分析。`} /> : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
      <div className="composer-actions">
        <span className="muted">对比内容汇总自各论文已有的 AI 分析结论</span>
        {busy ? <Button onClick={() => controller.current?.abort()}>停止等待</Button> : null}
        <Button type="primary" disabled={!ready} loading={busy} onClick={run}>{table && !stale ? "重新生成" : "生成对比表"}</Button>
      </div>
    </div>
    {table ? <section aria-label="对比表">
      {stale ? <Alert type="info" showIcon message="选择已变化，下表仍是上次的结果。" /> : null}
      {table.data.notes.map(note => <Alert key={note} type="warning" showIcon message={note} />)}
      <div className="table-scroll" tabIndex={0} aria-label="对比表，可横向滚动">
        <table className="compare-table">
          <thead><tr><th scope="col">维度</th>{table.data.rows.map(row => <th scope="col" key={row.paper_id}><Link to={paperLocation(row.paper_id, from)}>{row.title}</Link></th>)}</tr></thead>
          <tbody>{table.data.dimensions.map(dimension => <tr key={dimension}>
            <th scope="row">{dimension}</th>
            {table.data.rows.map(row => {
              const value = cell(row, dimension);
              return <td key={row.paper_id} className={!value || value.missing ? "is-missing" : undefined}>{!value || value.missing ? "未提取到" : value.text}</td>;
            })}
          </tr>)}</tbody>
        </table>
      </div>
      <p className="muted small-print">单元格来自 AI 分析结论，原文依据请在对应论文页查看。数据集或指标不同的结果不能直接排名。</p>
    </section> : null}
  </div>;
}

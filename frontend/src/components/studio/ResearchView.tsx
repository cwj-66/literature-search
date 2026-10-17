import { useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Input, InputNumber } from "antd";
import { DeleteOutlined, LoadingOutlined, PlusOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { resumeResearch, startResearch } from "../../api";
import { useSessionState } from "../../hooks";
import type { ResearchEvent, ResearchTask } from "../../types";
import { paperLocation } from "../../workspace";

const STEP: Record<string, string> = { plan: "规划检索式", search: "检索", expand: "扩展检索", read: "补读全文", synthesize: "综合证据", stop: "结束" };

function eventText(event: ResearchEvent) {
  if (event.step === "plan") return (event.queries || []).join("；");
  if (event.step === "search") return `${event.query ?? ""} · ${event.count ?? 0} 篇候选${event.errors && Object.keys(event.errors).length ? " · 部分来源失败" : ""}`;
  return [event.query, event.reason].filter(Boolean).join(" · ");
}

export function ResearchView({ from }: { from: string }) {
  const [goal, setGoal] = useSessionState("studio-goal", "");
  const [years, setYears] = useState<{ from: number | null; to: number | null }>({ from: null, to: null });
  const [autoApprove, setAutoApprove] = useState(false);
  const [task, setTask] = useSessionState<ResearchTask | null>("studio-research", null);
  const [queries, setQueries] = useState<string[]>(task?.interrupt?.queries ?? []);
  const [phase, setPhase] = useState<"" | "planning" | "running">("");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  function track(request: (signal: AbortSignal) => Promise<ResearchTask>, next: "planning" | "running") {
    const abort = new AbortController();
    controller.current = abort;
    setPhase(next); setError("");
    request(abort.signal).then(result => {
      setTask(result);
      setQueries(result.interrupt?.queries ?? result.queries ?? []);
    }).catch((err: Error) => {
      setError(abort.signal.aborted ? "已停止等待。服务器上的任务可能仍在继续，但结果不会显示在这里。" : err.message || "调研任务失败");
    }).finally(() => setPhase(""));
  }
  function start() {
    if (years.from && years.to && years.from > years.to) { setError("起始年份不能晚于结束年份"); return; }
    setTask(null);
    track(signal => startResearch(goal.trim(), { autoApprove, yearFrom: years.from, yearTo: years.to }, signal), autoApprove ? "running" : "planning");
  }
  const waiting = task?.status === "waiting";
  const cleaned = queries.map(q => q.trim()).filter(Boolean);
  const reads = task?.events?.filter(event => event.step === "read" && event.paper_id) ?? [];

  return <div className="studio-view studio-research">
    <div className="composer">
      <label className="field-label" htmlFor="research-goal">调研问题</label>
      <Input.TextArea id="research-goal" value={goal} onChange={event => setGoal(event.target.value)} autoSize={{ minRows: 2, maxRows: 5 }} maxLength={2000}
        placeholder="例如：近三年检索增强生成中降低幻觉的方法" disabled={Boolean(phase)} />
      <div className="research-options">
        <div className="year-range" role="group" aria-label="发表年份范围" aria-disabled={Boolean(phase)}>
          <span className="field-label">年份</span>
          <InputNumber disabled={Boolean(phase)} aria-label="起始年份" placeholder="不限" controls={false} min={1900} max={2100} value={years.from} onChange={value => setYears(v => ({ ...v, from: typeof value === "number" ? value : null }))} />
          <span aria-hidden>–</span>
          <InputNumber disabled={Boolean(phase)} aria-label="结束年份" placeholder="不限" controls={false} min={1900} max={2100} value={years.to} onChange={value => setYears(v => ({ ...v, to: typeof value === "number" ? value : null }))} />
        </div>
        <Checkbox disabled={Boolean(phase)} checked={autoApprove} onChange={event => setAutoApprove(event.target.checked)}>跳过检索式确认，直接执行</Checkbox>
      </div>
      <p className="field-help">任务会把问题改写为英文检索式，在外部来源检索候选，补读少量可获取全文的论文（会加入文献库），最后基于证据综合回答。</p>
      {error ? <Alert type={error.startsWith("已停止") ? "info" : "error"} showIcon message={error} /> : null}
      <div className="composer-actions">
        {phase ? <Button onClick={() => controller.current?.abort()}>停止等待</Button> : null}
        <Button type={waiting ? "default" : "primary"} disabled={!goal.trim() || Boolean(phase)} loading={phase === "planning" || (phase === "running" && !waiting)} onClick={start}>
          {autoApprove ? "开始调研" : "生成检索计划"}
        </Button>
      </div>
    </div>

    {phase ? <p className="pending" role="status"><LoadingOutlined aria-hidden />{phase === "planning" ? "正在生成检索式…" : "正在检索候选、补读全文并综合证据。完成前不会返回中间进度，请耐心等待。"}</p> : null}

    {waiting && !phase ? <section className="panel plan-review" aria-labelledby="plan-title">
      <h2 id="plan-title" className="view-title">确认检索式</h2>
      <p className="field-help">任务已暂停，等待确认。可以修改、删除或补充检索式，确认后才会开始检索。</p>
      <ol className="query-list">{queries.map((query, index) => <li key={index}>
        <Input value={query} aria-label={`检索式 ${index + 1}`} onChange={event => setQueries(list => list.map((item, i) => i === index ? event.target.value : item))} />
        <Button type="text" icon={<DeleteOutlined />} aria-label={`删除检索式 ${index + 1}`} disabled={queries.length <= 1} onClick={() => setQueries(list => list.filter((_, i) => i !== index))} />
      </li>)}</ol>
      <div className="composer-actions">
        <Button type="text" icon={<PlusOutlined />} disabled={queries.length >= 4} onClick={() => setQueries(list => [...list, ""])}>添加检索式</Button>
        <Button onClick={() => setTask(null)}>放弃</Button>
        <Button type="primary" disabled={!cleaned.length} onClick={() => track(signal => resumeResearch(task!.task_id, cleaned, signal), "running")}>确认并执行</Button>
      </div>
    </section> : null}

    {task?.status === "done" ? <section className="research-result" aria-labelledby="research-result-title">
      <h2 id="research-result-title" className="view-title">调研结果</h2>
      <p className="research-goal">“{task.goal}”</p>
      <dl className="stat-line">
        <div><dt>检索调用</dt><dd>{task.api_calls ?? 0}</dd></div>
        <div><dt>候选论文</dt><dd>{task.paper_ids?.length ?? 0}</dd></div>
        <div><dt>补读全文</dt><dd>{task.deep_reads ?? 0}</dd></div>
        <div><dt>证据</dt><dd>{task.evidence_ids?.length ?? 0}</dd></div>
      </dl>
      {task.stop_reason ? <p className="muted">结束原因：{task.stop_reason}</p> : null}
      {task.answer ? <p className="answer-text">{task.answer.replace(/\[(E\d+)\]/g, "［$1］")}</p> : <Alert type="info" showIcon message="没有生成综合回答。" />}
      {task.gaps?.length ? <Alert type="warning" showIcon message="覆盖缺口" description={<ul>{task.gaps.map(gap => <li key={gap}>{gap}</li>)}</ul>} /> : null}
      {reads.length ? <><h3>补读的论文</h3><ul className="link-list">{reads.map(event => <li key={event.paper_id}><Link to={paperLocation(event.paper_id!, from)}>{event.paper_id}</Link><span className="muted"> · {event.reason}</span></li>)}</ul></> : null}
      {task.events?.length ? <details className="explain">
        <summary>执行记录（{task.events.length} 步，任务结束后返回）</summary>
        <ol className="event-list">{task.events.map((event, index) => <li key={index}><strong>{STEP[event.step] ?? event.step}</strong><span>{eventText(event)}</span></li>)}</ol>
      </details> : null}
    </section> : null}
  </div>;
}

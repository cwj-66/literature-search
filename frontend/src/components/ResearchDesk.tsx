import { useState } from "react";
import { Alert, Button, Input } from "antd";
import { askLibrary, compareLibrary, startResearch } from "../api";
import type { Paper, QAResponse, ResearchTask } from "../types";

export function ResearchDesk({ papers }: { papers: Paper[] }) {
  const [question, setQuestion] = useState("");
  const [goal, setGoal] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [qa, setQa] = useState<QAResponse | null>(null);
  const [task, setTask] = useState<ResearchTask | null>(null);
  const [table, setTable] = useState<{ notes: string[]; rows: { title: string; cells: { dimension: string; text: string; missing: boolean }[] }[] } | null>(null);
  const ids = papers.map(paper => paper.local_id || paper.id);
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id].slice(-5));
  return <section className="surface guide-panel research-desk">
    <span className="page-kicker">项目内综合</span>
    <h2>问答、对比与调研</h2>
    <p>问题只在当前文献库内检索。对比需先分析 3 至 5 篇。调研 Agent 使用已有检索与精读工具。</p>
    {error ? <Alert type="error" message={error} /> : null}
    <label>跨篇提问
      <Input.TextArea value={question} onChange={event => setQuestion(event.target.value)} rows={2} maxLength={2000} placeholder="这些方法如何降低幻觉？" />
    </label>
    <Button loading={busy === "qa"} disabled={!question.trim()} onClick={() => {
      setBusy("qa"); setError(""); setQa(null);
      askLibrary(question.trim(), selected.length ? selected : ids).then(setQa).catch(err => setError(err.message)).finally(() => setBusy(""));
    }}>提问</Button>
    {qa ? <div className="research-output"><p>{qa.answer}</p>{qa.evidence.slice(0, 4).map(item => <small key={item.evidence_id}>[{item.evidence_id}] {item.quote.slice(0, 120)}</small>)}</div> : null}
    <label>调研问题
      <Input value={goal} onChange={event => setGoal(event.target.value)} maxLength={500} placeholder="近三年 RAG 幻觉治理" />
    </label>
    <Button loading={busy === "agent"} disabled={!goal.trim()} onClick={() => {
      setBusy("agent"); setError(""); setTask(null);
      startResearch(goal.trim(), true).then(setTask).catch(err => setError(err.message)).finally(() => setBusy(""));
    }}>启动调研</Button>
    {task ? <div className="research-output"><p>{task.stop_reason || task.status}</p><small>{(task.queries || []).join(" · ")}</small></div> : null}
    <p className="meta">勾选 3–5 篇后对比；未勾选时提问使用整库。</p>
    <div className="research-select">{papers.slice(0, 12).map(paper => {
      const id = paper.local_id || paper.id;
      return <label key={id}><input type="checkbox" checked={selected.includes(id)} onChange={() => toggle(id)} />{paper.title}</label>;
    })}</div>
    <Button loading={busy === "compare"} disabled={selected.length < 3 || selected.length > 5} onClick={() => {
      setBusy("compare"); setError(""); setTable(null);
      compareLibrary(selected).then(setTable).catch(err => setError(err.message)).finally(() => setBusy(""));
    }}>生成对比表</Button>
    {table ? <div className="research-output">{table.notes.map(note => <p key={note}>{note}</p>)}{table.rows.map(row => <p key={row.title}><b>{row.title}</b>{row.cells.slice(0, 3).map(cell => ` · ${cell.dimension}：${cell.text}`)}</p>)}</div> : null}
  </section>;
}

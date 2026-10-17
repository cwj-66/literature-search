import { Fragment, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Segmented } from "antd";
import { LoadingOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { askLibrary } from "../../api";
import { useSessionState } from "../../hooks";
import type { Picked } from "../../selection";
import type { QAResponse } from "../../types";
import { paperLocation } from "../../workspace";

interface Turn { id: number; question: string; scope: string; response?: QAResponse; error?: string }

const VERDICT: Record<string, string> = { supported: "有证据支持", insufficient: "证据不足", contradictory: "与证据矛盾", unverifiable: "无法核验" };
const MATERIAL: Record<string, string> = { model_summary: "模型摘要", note: "笔记" };
export const QA_LIBRARY_LIMIT = 100;

function Answer({ turn, titles, from }: { turn: Turn; titles: Map<string, string>; from: string }) {
  const [active, setActive] = useState("");
  const response = turn.response!;
  const valid = new Set(response.evidence.map(item => item.evidence_id));
  const parts = response.answer.split(/\[(E\d+)\]/g);
  const verdicts = response.reviews.reduce<Record<string, number>>((acc, review) => ({ ...acc, [review.verdict]: (acc[review.verdict] || 0) + 1 }), {});
  const jump = (id: string) => {
    setActive(id);
    document.getElementById(`ev-${turn.id}-${id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  return <div className="answer-layout">
    <div className="answer-body">
      {response.coverage_gap ? <Alert type="warning" showIcon message="证据覆盖不足：这个范围内没有找到足以回答问题的原文证据，回答可能不完整。" /> : null}
      <p className="answer-text">{parts.map((part, index) => index % 2 === 1
        ? valid.has(part) ? <button key={index} type="button" className={`cite-mark${active === part ? " is-active" : ""}`} onClick={() => jump(part)} aria-label={`查看证据 ${part}`}>{part}</button> : null
        : <Fragment key={index}>{part}</Fragment>)}</p>
      {response.reviews.length ? <details className="review-details">
        <summary>结论核验：{Object.entries(verdicts).map(([key, count]) => `${count} 条${VERDICT[key] ?? key}`).join("，")}</summary>
        <ul>{response.reviews.map(review => <li key={review.claim_id}><span className={`verdict verdict-${review.verdict}`}>{VERDICT[review.verdict] ?? review.verdict}</span>{review.note || "无说明"}{review.evidence_ids.length ? <span className="muted">（{review.evidence_ids.join("、")}）</span> : null}</li>)}</ul>
      </details> : null}
    </div>
    <div className="answer-evidence" aria-label="回答引用的证据">
      <h3>证据 {response.evidence.length}</h3>
      {!response.evidence.length ? <p className="muted">没有可引用的证据。</p> : null}
      {response.evidence.map(item => <figure key={item.evidence_id} id={`ev-${turn.id}-${item.evidence_id}`} className={`evidence-item${active === item.evidence_id ? " is-active" : ""}`}>
        <figcaption>
          <span className="evidence-id">{item.evidence_id}</span>
          <Link to={paperLocation(item.paper_id, from)}>{titles.get(item.paper_id) ?? item.paper_id}</Link>
          {item.section ? <span className="muted"> · {item.section}</span> : null}
          {item.material && item.material !== "original" ? <span className="muted"> · {MATERIAL[item.material] ?? item.material}</span> : null}
        </figcaption>
        <blockquote>{item.quote}</blockquote>
      </figure>)}
    </div>
  </div>;
}

export function AskView({ picked, libraryTotal, titles, scope, onScope, from }: {
  picked: Picked[]; libraryTotal: number | null; titles: Map<string, string>;
  scope: "selected" | "library"; onScope: (scope: "selected" | "library") => void; from: string;
}) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useSessionState<Turn[]>("studio-qa", []);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const effective = scope === "selected" && picked.length ? "selected" : "library";
  const libraryNote = libraryTotal != null && libraryTotal > QA_LIBRARY_LIMIT
    ? `最近导入的 ${QA_LIBRARY_LIMIT} 篇（文献库共 ${libraryTotal} 篇，其余不参与）`
    : `全部 ${libraryTotal ?? "…"} 篇`;
  function ask() {
    const text = question.trim();
    if (!text || busy || (effective === "library" && !libraryTotal)) return;
    const ids = effective === "selected" ? picked.map(item => item.id) : [];
    const label = effective === "selected" ? `所选 ${ids.length} 篇` : `整个文献库：${libraryNote}`;
    const id = Date.now();
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setTurns(current => [{ id, question: text, scope: label }, ...current].slice(0, 8));
    askLibrary(text, ids, abort.signal)
      .then(response => { setTurns(current => current.map(turn => turn.id === id ? { ...turn, response } : turn)); setQuestion(""); })
      .catch((err: Error) => setTurns(current => abort.signal.aborted ? current.filter(turn => turn.id !== id) : current.map(turn => turn.id === id ? { ...turn, error: err.message || "提问失败" } : turn)))
      .finally(() => { setBusy(false); controller.current = null; });
  }
  return <div className="studio-view">
    <div className="composer">
      <div className="scope-row">
        <span className="field-label" id="qa-scope">回答范围</span>
        <Segmented aria-labelledby="qa-scope" value={effective} onChange={value => onScope(value as "selected" | "library")} options={[
          { value: "selected", label: `所选论文（${picked.length}）`, disabled: !picked.length },
          { value: "library", label: "整个文献库" },
        ]} />
      </div>
      <p className="field-help">{effective === "selected"
        ? `只在所选 ${picked.length} 篇论文的摘要与已建立的全文索引中检索证据。`
        : `在${libraryNote}的摘要与已建立的全文索引中检索证据。${picked.length ? "" : "在左侧勾选论文可缩小范围。"}`}</p>
      <Input.TextArea disabled={busy} value={question} onChange={event => setQuestion(event.target.value)} autoSize={{ minRows: 3, maxRows: 8 }} maxLength={2000}
        aria-label="问题" placeholder="例如：这些方法如何降低大模型幻觉？"
        onKeyDown={event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); ask(); } }} />
      <div className="composer-actions">
        <span className="muted">Ctrl + Enter 发送</span>
        {busy ? <Button onClick={() => controller.current?.abort()}>停止等待</Button> : null}
        <Button type="primary" loading={busy} disabled={!question.trim() || (effective === "library" && !libraryTotal)} onClick={ask}>提问</Button>
      </div>
    </div>
    {!turns.length ? <section className="ask-start" aria-label="提问建议"><h2>从你关心的问题开始</h2><p>{libraryTotal === 0 ? "先把论文加入文献库，再开始提问。" : "选择一个角度，或直接写下你的问题。回答中的引用可回到原文核验。"}</p><div className="question-suggestions">{["这些论文分别解决了什么问题？", "这些方法有哪些共同点和差异？", "这些研究有哪些局限性？"].map(text => <button type="button" key={text} disabled={busy} onClick={() => setQuestion(text)}>{text}</button>)}</div></section> : null}
    {turns.map((turn, index) => <article key={turn.id} className={`qa-turn${index ? " is-past" : ""}`}>
      <header><h2 className="qa-question">{turn.question}</h2><span className="muted">范围：{turn.scope}</span></header>
      {turn.error ? <Alert type="error" showIcon message={turn.error} />
        : !turn.response && !(busy && index === 0) ? <Alert type="info" showIcon message="这次请求在离开页面时中断，请重新提问。" />
        : !turn.response ? <p className="pending"><LoadingOutlined aria-hidden />正在检索证据并生成回答…</p>
        : <Answer turn={turn} titles={titles} from={from} />}
    </article>)}
    {turns.length ? <Button type="link" className="clear-history" onClick={() => setTurns([])}>清空问答记录</Button> : null}
  </div>;
}

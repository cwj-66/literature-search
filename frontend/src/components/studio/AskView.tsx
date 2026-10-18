import { Fragment, useState } from "react";
import { Alert } from "antd";
import { Link } from "react-router-dom";
import type { QAResponse } from "../../types";
import { paperLocation } from "../../workspace";

interface Turn { id: string; question: string; scope: string; response?: QAResponse; error?: string }

const VERDICT: Record<string, string> = { supported: "有证据支持", insufficient: "证据不足", contradictory: "与证据矛盾", unverifiable: "无法核验" };
const MATERIAL: Record<string, string> = { model_summary: "模型摘要", note: "笔记" };

export function Answer({ turn, titles, from, paths }: { turn: Turn; titles: Map<string, string>; from: string; paths?: Map<string, string> }) {
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
          <Link to={paths?.get(item.paper_id) ?? paperLocation(item.paper_id, from)}>{titles.get(item.paper_id) ?? item.paper_id}</Link>
          {item.section ? <span className="muted"> · {item.section}</span> : null}
          {item.material && item.material !== "original" ? <span className="muted"> · {MATERIAL[item.material] ?? item.material}</span> : null}
        </figcaption>
        <blockquote>{item.quote}</blockquote>
      </figure>)}
    </div>
  </div>;
}

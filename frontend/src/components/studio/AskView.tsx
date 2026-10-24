import type { QAResponse } from "../../types";
import { evidenceLabels } from "../../presentation";
import { AnswerMarkdown } from "../AnswerMarkdown";

interface Turn { id: string; question: string; scope: string; response?: QAResponse; error?: string }

const VERDICT: Record<string, string> = { supported: "通过检查", insufficient: "依据不足", contradictory: "与引用内容矛盾", unverifiable: "无法核验" };

export function Answer({ turn, titles, from, paths }: { turn: Turn; titles: Map<string, string>; from: string; paths?: Map<string, string> }) {
  const response = turn.response!;
  const labels = evidenceLabels(response.evidence);
  const verdicts = response.reviews.reduce<Record<string, number>>((acc, review) => ({ ...acc, [review.verdict]: (acc[review.verdict] || 0) + 1 }), {});
  return <div className="answer-layout is-compact">
    <div className="answer-body">
      <AnswerMarkdown text={response.answer} evidence={response.evidence} titles={titles} from={from} paths={paths} />
      {response.reviews.length ? <details className="review-details">
        <summary>依据与分析检查：{Object.entries(verdicts).map(([key, count]) => `${count} 条${VERDICT[key] ?? key}`).join("，")}</summary>
        <p className="muted">检查引用、论述与分析边界，不代表研究结论已经得到独立验证。</p>
        <ul>{response.reviews.map(review => <li key={review.claim_id}><span className={`verdict verdict-${review.verdict}`}>{VERDICT[review.verdict] ?? review.verdict}</span>{(review.note || "无说明").replace(/E\d+/g, id => labels.get(id) ?? "来源")}{review.evidence_ids.some(id => labels.has(id)) ? <span className="muted">（{review.evidence_ids.map(id => labels.get(id)).filter(Boolean).join("、")}）</span> : null}</li>)}</ul>
      </details> : null}
    </div>
  </div>;
}

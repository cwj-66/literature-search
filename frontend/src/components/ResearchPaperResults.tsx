import { Link } from "react-router-dom";
import { CollectButton } from "./CollectButton";
import { formatAuthors, paperFacts } from "./PaperRow";
import type { ResearchTurn } from "../researchChats";
import { researchResults } from "../researchPresentation";
import { paperKey } from "../types";
import { paperLocation } from "../workspace";

export function ResearchPaperResults({ turn, from }: { turn: ResearchTurn; from: string }) {
  const { recommended, candidates, legacy } = researchResults(turn);
  if (!recommended.length && !candidates.length) return null;
  return <section className="research-results" aria-label="论文检索结果">
    {recommended.length ? <>
      <h3 className="research-results-heading">推荐 {recommended.length} 篇{turn.task?.result_limit && recommended.length < turn.task.result_limit ? `（目标 ${turn.task.result_limit} 篇，本次不足）` : ""}</h3>
      {recommended.map(paper => <article className="assistant-hit" key={paperKey(paper)}>
        <h3><Link to={paperLocation(paperKey(paper), from)}>{paper.title}</Link></h3>
        {turn.task?.recommendation_reasons?.[paper.id] ? <p className="research-match-reason">{turn.task.recommendation_reasons[paper.id]}</p> : null}
        <p className="paper-byline">{formatAuthors(paper.authors)}</p>
        <p className="paper-facts">{paperFacts(paper).map((fact, index) => <span key={index}>{fact}</span>)}</p>
        <div className="assistant-hit-actions"><CollectButton paper={paper} /></div>
      </article>)}
    </> : null}
    {candidates.length ? <details className="research-evidence research-candidates">
      <summary>{legacy ? "历史检索候选" : "另有未选用候选"} · {candidates.length} 篇</summary>
      <p className="small-print">候选不等于推荐，也不表示已读。部分结果可能与主题无关。</p>
      <ul>{candidates.map(paper => <li key={paperKey(paper)}><Link to={paperLocation(paperKey(paper), from)}>{paper.title}</Link><span className="muted"> · {paper.year ?? "年份未知"}</span></li>)}</ul>
    </details> : null}
  </section>;
}

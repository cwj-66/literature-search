import { useLayoutEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CollectButton } from "./CollectButton";
import { formatAuthors } from "./PaperRow";
import type { ResearchTurn } from "../researchChats";
import { researchResults } from "../researchPresentation";
import { ccfLabel, paperKey, SOURCE_LABEL, type Paper } from "../types";
import { paperLocation } from "../workspace";

function RecommendationCard({ paper, reason, index, from }: { paper: Paper; reason?: string; index: number; from: string }) {
  const [expanded, setExpanded] = useState(false);
  const [clipped, setClipped] = useState(false);
  const reasonRef = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    const node = reasonRef.current;
    if (!node || expanded) return;
    const measure = () => setClipped(node.scrollHeight > node.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [reason, expanded]);
  const venue = paper.venue || paper.sources.map(source => SOURCE_LABEL[source] ?? source).join(" / ");
  const sources = paper.sources.map(source => SOURCE_LABEL[source] ?? source).join(" / ");
  const facts = [venue, ccfLabel(paper)].filter(Boolean);
  const href = paperLocation(paperKey(paper), from);
  return <article className="research-paper-card">
    <div className="research-paper-rail"><span className="research-paper-number">{String(index + 1).padStart(2, "0")}</span><span>{paper.year ?? "年份未知"}</span>{sources ? <span>{sources}</span> : null}</div>
    <div className="research-paper-main">
    <h3><Link to={href}>{paper.title}</Link></h3>
    {reason ? <div className="research-paper-reason">
      <p ref={reasonRef} className={expanded ? "is-expanded" : "is-clamped"}>{reason}</p>
      {expanded || clipped ? <button type="button" className="link-button" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起理由" : "展开理由"}</button> : null}
    </div> : null}
    <p className="research-paper-authors" title={paper.authors.join(", ")}>{formatAuthors(paper.authors, 2)}</p>
    <div className="research-paper-footer"><span className="research-paper-meta" title={facts.join(" · ")}>{facts.join(" · ")}{paper.citation_count != null ? `${facts.length ? " · " : ""}引用 ${paper.citation_count.toLocaleString("zh-CN")}` : ""}</span><div className="research-paper-actions"><Link className="research-paper-open" to={href}>查看论文</Link><CollectButton paper={paper} /></div></div>
    </div>
  </article>;
}

export function ResearchPaperResults({ turn, from }: { turn: ResearchTurn; from: string }) {
  const { recommended, candidates, legacy } = researchResults(turn);
  if (!recommended.length && !candidates.length) return null;
  const hasFutureYear = recommended.some(paper => paper.year != null && paper.year > new Date().getFullYear());
  return <section className="research-results" aria-label="论文检索结果">
    {recommended.length ? <>
      <h3 className="research-results-heading">推荐 {recommended.length} 篇{turn.task?.result_limit && recommended.length < turn.task.result_limit ? `（目标 ${turn.task.result_limit} 篇，本次不足）` : ""}</h3>
      {hasFutureYear ? <p className="small-print">部分文献的来源年份晚于当前年份，可能对应预排刊期；实际在线发表日期请以出版社页面为准。</p> : null}
      <div className="research-recommendation-list">{recommended.map((paper, index) => <RecommendationCard key={paperKey(paper)} paper={paper} index={index} from={from} reason={turn.task?.recommendation_reasons?.[paper.id]} />)}</div>
    </> : null}
    {candidates.length ? <details className="research-evidence research-candidates">
      <summary>{legacy ? "历史检索候选" : "另有未选用候选"} · {candidates.length} 篇</summary>
      <p className="small-print">候选不等于推荐，也不表示已读。部分结果可能与主题无关。</p>
      <ul>{candidates.map(paper => <li key={paperKey(paper)}><Link to={paperLocation(paperKey(paper), from)}>{paper.title}</Link><span className="muted"> · {paper.year ?? "年份未知"}</span></li>)}</ul>
    </details> : null}
  </section>;
}

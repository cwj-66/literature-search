import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Paper } from "../types";
import { ccfLabel, paperKey, SOURCE_LABEL } from "../types";
import { paperLocation } from "../workspace";

export function formatAuthors(authors: string[], limit = 5) {
  if (!authors.length) return "作者未知";
  if (authors.length <= limit) return authors.join(", ");
  return `${authors.slice(0, limit).join(", ")} 等 ${authors.length} 人`;
}

export function paperFacts(paper: Paper) {
  return [
    paper.year ? String(paper.year) : "年份未知",
    paper.venue,
    paper.citation_count != null ? `引用 ${paper.citation_count.toLocaleString("zh-CN")}` : null,
    ccfLabel(paper),
    paper.cas_partition ? `中科院 ${paper.cas_partition}` : null,
  ].filter((item): item is string => Boolean(item));
}

export function Abstract({ text, lines = 2 }: { text: string | null; lines?: number }) {
  const [open, setOpen] = useState(false);
  if (!text) return <p className="paper-abstract is-missing">无摘要</p>;
  const long = text.length > lines * 110;
  return <div className="paper-abstract-wrap">
    <p className={`paper-abstract${open || !long ? "" : " is-clamped"}`} style={{ WebkitLineClamp: lines }}>{text}</p>
    {long ? <button type="button" className="link-button" aria-expanded={open} onClick={() => setOpen(value => !value)}>{open ? "收起摘要" : "展开摘要"}</button> : null}
  </div>;
}

export function ExternalLinks({ paper }: { paper: Paper }) {
  if (!paper.url && !paper.pdf_url) return null;
  return <span className="external-links">
    {paper.url ? <a href={paper.url} target="_blank" rel="noopener noreferrer" aria-label="原文页面（新窗口）">原文 ↗</a> : null}
    {paper.pdf_url ? <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer" aria-label="PDF（新窗口）">PDF ↗</a> : null}
  </span>;
}

export function PaperRow({ paper, from, leading, side, abstract = true, selected = false }: { paper: Paper; from: string; leading?: ReactNode; side?: ReactNode; abstract?: boolean; selected?: boolean }) {
  const sources = paper.sources.map(source => SOURCE_LABEL[source] ?? source).join(" · ");
  return <article className={`paper-row${leading ? " has-leading" : ""}${selected ? " is-selected" : ""}`}>
    {leading ? <div className="paper-row-leading">{leading}</div> : null}
    <div className="paper-row-body">
      <h3 className="paper-title"><Link to={paperLocation(paperKey(paper), from)}>{paper.title}</Link></h3>
      <p className="paper-byline">{formatAuthors(paper.authors)}</p>
      <p className="paper-facts">{paperFacts(paper).map(item => <span key={item}>{item}</span>)}{sources ? <span className="paper-source">{sources}</span> : null}</p>
      {abstract ? <Abstract text={paper.abstract} /> : null}
    </div>
    {side ? <div className="paper-row-side">{side}</div> : null}
  </article>;
}

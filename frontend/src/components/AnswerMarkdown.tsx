import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { visit, SKIP } from "unist-util-visit";
import type { Root, PhrasingContent } from "mdast";
import { Link } from "react-router-dom";
import { useState } from "react";
import { Modal } from "antd";
import { evidenceLabels } from "../presentation";
import { paperLocation } from "../workspace";
import "katex/dist/katex.min.css";

// Work on Markdown text nodes, so tables/strong text support citations while
// code, existing links and mathematical expressions remain intact.
export function remarkEvidence() {
  return (tree: Root) => {
    visit(tree, "text", (node, index, parent) => {
      if (!parent || index == null || parent.type === "link" || parent.type === "linkReference") return;
      const matches = [...node.value.matchAll(/\[E\d+\](?:[ \t]*\[E\d+\])*/g)];
      if (!matches.length) return;
      const nodes: PhrasingContent[] = [];
      let start = 0;
      for (const match of matches) {
        if (match.index! > start) nodes.push({ type: "text", value: node.value.slice(start, match.index) });
        const ids = [...new Set(match[0].match(/E\d+/g))];
        nodes.push({ type: "link", url: `#qa-source-${ids.join(",")}`, children: [{ type: "text", value: ids.join(",") }] });
        start = match.index! + match[0].length;
      }
      if (start < node.value.length) nodes.push({ type: "text", value: node.value.slice(start) });
      parent.children.splice(index, 1, ...nodes as typeof parent.children);
      return [SKIP, index + nodes.length] as const;
    });
  };
}

type CitationEvidence = {
  evidence_id: string; paper_id?: string; quote?: string; material?: string; basis?: string;
  section?: string | null; section_path?: string[]; page_start?: number | null; page_end?: number | null;
};

export function EvidenceExcerpt({ source, label, title, path }: { source: CitationEvidence; label: string; title?: string; path?: string }) {
  const abstractSection = /^(abstract|摘要)$/i.test((source.section_path?.slice(-1)[0] || source.section || "").trim());
  const kind = source.material === "model_summary" ? "模型摘要（非原文）" : source.material === "note" ? "研究笔记（非原文）"
    : source.basis === "full_text" ? abstractSection ? "摘要原文（取自全文）" : "正文原文" : source.basis === "abstract" ? "摘要原文" : "检索片段（资料类型未标明）";
  const section = source.section_path?.filter(Boolean).join(" › ") || source.section;
  const page = source.page_start != null && source.page_start > 0
    ? `第 ${source.page_start}${source.page_end != null && source.page_end > source.page_start ? `–${source.page_end}` : ""} 页` : "";
  return <section className="citation-excerpt" aria-label={label}>
    <div className="citation-excerpt-meta"><strong>{label}</strong><span>{kind}</span></div>
    <h3>{title || "论文标题未记录"}</h3>
    {section || page ? <p className="citation-excerpt-location">{[section, page].filter(Boolean).join(" · ")}</p> : null}
    {source.quote?.trim() ? <blockquote>{source.quote}</blockquote> : <p className="citation-excerpt-missing">这条历史记录没有保存原文片段，可打开论文查看。</p>}
    {path ? <Link to={path} className="citation-paper-link">查看完整论文 →</Link> : null}
  </section>;
}

export function AnswerMarkdown({ text, evidence, titles, from, paths }: {
  text: string; evidence: CitationEvidence[]; titles: Map<string, string>; from: string; paths?: Map<string, string>;
}) {
  const [opened, setOpened] = useState<string[]>([]);
  const sources = new Map(evidence.map(e => [e.evidence_id, e]));
  const labels = evidenceLabels(evidence);
  const active = opened.map(id => sources.get(id)).filter((e): e is CitationEvidence => !!e);
  return <div className="answer-text answer-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath, remarkEvidence]} rehypePlugins={[rehypeKatex]} skipHtml components={{
      a({ href, children }) {
        if (href?.startsWith("#qa-source-")) {
          const ids = [...new Set(href.slice("#qa-source-".length).split(","))].filter(id => sources.has(id));
          if (!ids.length) return null;
          const label = "来源 " + ids.map(id => labels.get(id)?.replace(/^来源\s*/, "")).join("、");
          return <button type="button" className="cite-mark" aria-haspopup="dialog" aria-label={`查看${label}的原文片段`} title="查看引用片段" onClick={() => setOpened(ids)}>{label}</button>;
        }
        return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
      },
      table({ children }) { return <div className="answer-table-scroll" role="region" aria-label="论文对比表格" tabIndex={0}><table>{children}</table></div>; },
      img() { return null; },
    }}>{text}</ReactMarkdown>
    <Modal title="引用原文" open={active.length > 0} onCancel={() => setOpened([])} footer={null} width={760}
      style={{ top: 40, maxWidth: "calc(100vw - 32px)" }} styles={{ body: { maxHeight: "calc(100dvh - 170px)", overflowY: "auto" } }} className="citation-dialog">
      {active.map(source => <EvidenceExcerpt key={source.evidence_id} source={source} label={labels.get(source.evidence_id) || "来源"}
        title={source.paper_id ? titles.get(source.paper_id) : undefined}
        path={source.paper_id ? paths?.get(source.paper_id) ?? paperLocation(source.paper_id, from) : undefined} />)}
    </Modal>
  </div>;
}

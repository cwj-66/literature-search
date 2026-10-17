import { Tag, Typography } from "antd";
import { Link, useLocation } from "react-router-dom";
import type { Paper } from "../types";
import { ccfLabel } from "../types";

const SOURCE_LABEL: Record<string, string> = {
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
};

export function formatAuthors(authors: string[]) {
  if (!authors.length) return "作者未知";
  if (authors.length <= 5) return authors.join(", ");
  return `${authors.slice(0, 5).join(", ")} 等 ${authors.length} 人`;
}

export function detailPath(id: string, from: string) {
  const query = new URLSearchParams({ id, from });
  return `/paper?${query}`;
}

export function PaperCard({ paper, analysisMode = false }: { paper: Paper; analysisMode?: boolean }) {
  const location = useLocation();
  const href = detailPath(paper.local_id ?? paper.id, location.pathname + location.search) + (analysisMode ? "#analysis" : "");
  return (
    <article className="paper-card">
      <div className="paper-topline"><div className="tag-row">
        {paper.sources.map((source) => (
          <Tag key={source} color={source === "arxiv" ? "blue" : "geekblue"}>
            {SOURCE_LABEL[source] ?? source}
          </Tag>
        ))}
      </div><span className="paper-year">{paper.year ?? "年份未知"}</span></div>
      <h2 className="paper-title">
        <Link to={href}>{paper.title}</Link>
      </h2>
      <div className="meta">{formatAuthors(paper.authors)}</div>
      {paper.venue ? <div className="meta">{paper.venue}</div> : null}
      {paper.abstract ? (
        <Typography.Paragraph style={{ margin: "8px 0" }} ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}>
          {paper.abstract}
        </Typography.Paragraph>
      ) : (
        <Typography.Paragraph type="secondary">摘要缺失</Typography.Paragraph>
      )}
      <div className="paper-card-metrics"><span>引用 <b>{paper.citation_count != null ? paper.citation_count.toLocaleString("zh-CN") : "未知"}</b></span><span>CCF <b>{ccfLabel(paper)}</b></span><span>中科院分区 <b>{paper.cas_partition ?? "未知"}</b></span></div>
      <div className="card-links paper-card-actions">
        <Link to={href}>阅读与分析 →</Link>
        <Link to={`/recommend?${new URLSearchParams({ id: paper.local_id ?? paper.id, from: location.pathname + location.search })}`}>相关推荐</Link>
        {paper.url ? (
          <a href={paper.url} target="_blank" rel="noopener noreferrer" aria-label="原文（将打开新窗口）" title="将打开新窗口">
            原文
          </a>
        ) : null}
        {paper.pdf_url ? (
          <a href={paper.pdf_url} target="_blank" rel="noopener noreferrer" aria-label="PDF（将打开新窗口）" title="将打开新窗口">
            PDF
          </a>
        ) : null}
      </div>
    </article>
  );
}

import type { AnalyzePaperResult } from "../types";

const PAPER_STATUS = { completed: "完成", partial: "部分完成", failed: "失败", not_found: "未找到", out_of_scope: "不在分析范围" };
const FIELD_STATUS = { found: "已提取", partial: "部分提取", not_found: "未找到" };

export function AnalyzeResult({ result }: { result: AnalyzePaperResult }) {
  return <div className="analyze-result">
    {result.papers.map(paper => <article key={paper.paper_id}>
      <header>
        <h3>{paper.title || paper.paper_id}</h3>
        <span className="small-print">{PAPER_STATUS[paper.status]}{paper.depth_applied === "deep" ? " · 深度" : " · 标准"}{paper.message ? ` · ${paper.message}` : ""}</span>
      </header>
      {paper.fields.map(field => <section key={field.field}>
        <h4>{field.label}<span>{FIELD_STATUS[field.status]}</span></h4>
        {field.content ? <p>{field.content}</p> : <p className="muted">这项没有提取到内容。</p>}
        {field.missing.length ? <p className="small-print">未找到：{field.missing.join("、")}</p> : null}
        {field.evidence.map((item, index) => <blockquote key={`${item.paragraph_id}-${index}`}>
          <footer>{[item.section, item.page ? `第 ${item.page} 页` : ""].filter(Boolean).join(" · ") || "原文"}</footer>
          {item.quote}
        </blockquote>)}
      </section>)}
    </article>)}
  </div>;
}

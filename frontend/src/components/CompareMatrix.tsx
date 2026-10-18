import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import type { CompareTable } from "../types";

export const COMPARE_NOTE = "内容来自各论文已保存的分析结果；实验任务、数据集或指标不同，不直接按数字排名。";

export function CompareMatrix({ table, hrefFor }: { table: CompareTable; hrefFor: (paperId: string) => string }) {
  const cellOf = (row: CompareTable["rows"][number], dimension: string) => row.cells.find(item => item.dimension === dimension);
  const extra = table.notes.filter(note => note !== COMPARE_NOTE);
  return <section aria-label="研究内容对比">
    <h2 className="compare-section-title">研究内容对比</h2>
    <p className="compare-note">{COMPARE_NOTE}</p>
    {extra.map(note => <p key={note} className="compare-note">{note}</p>)}
    <div className="table-scroll" tabIndex={0} aria-label="研究内容对比表，可横向滚动">
      <table className="compare-table" style={{ "--compare-cols": table.rows.length } as CSSProperties}>
        <thead><tr><th scope="col">维度</th>{table.rows.map(row => <th scope="col" key={row.paper_id}><Link to={hrefFor(row.paper_id)}>{row.title}</Link></th>)}</tr></thead>
        <tbody>{table.dimensions.map(dimension => <tr key={dimension}>
          <th scope="row">{dimension}</th>
          {table.rows.map(row => {
            const value = cellOf(row, dimension);
            const missing = !value || value.missing;
            const evidence = value?.evidence?.filter(item => item.paragraph_id && item.quote) ?? [];
            const href = hrefFor(row.paper_id);
            return <td key={row.paper_id} className={missing ? "is-missing" : undefined}>
              {missing ? "未提取到" : value.text}
              {missing || !evidence.length
                ? <Link className="compare-cell-link" to={href}>查看论文分析</Link>
                : <details className="compare-evidence">
                  <summary>原文依据 {evidence.length}</summary>
                  {evidence.map(item => <blockquote key={`${item.paragraph_id}-${item.quote.slice(0, 24)}`}>
                    <span className="compare-loc">{item.section ? `${item.section} · ` : ""}段落 {item.paragraph_id}</span>
                    {item.quote}
                  </blockquote>)}
                  <Link className="compare-cell-link" to={href}>在论文分析页查看</Link>
                </details>}
            </td>;
          })}
        </tr>)}</tbody>
      </table>
    </div>
  </section>;
}

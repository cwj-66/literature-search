import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Descriptions, Spin, Tag, Typography } from "antd";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { AnalysisPanel } from "../components/AnalysisPanel";
import { ImportButton } from "../components/ImportPaper";
import { fetchPaper } from "../api";
import { formatAuthors } from "../components/PaperCard";
import type { Paper } from "../types";
import { ccfLabel } from "../types";
import { PaperViews } from "../components/ResearchWorkspace";
import { graphLocation, safeReturn } from "../workspace";

const SOURCE_LABEL: Record<string, string> = {
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
};

export default function PaperPage() {
  const [params] = useSearchParams();
  const location = useLocation();
  const id = params.get("id")?.trim() ?? "";
  const back = safeReturn(params.get("from"));
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const refreshPaper = useCallback(() => {
    if (!id) return;
    fetchPaper(id).then((data) => {
      setPaper((current) => current ? data : current);
      window.dispatchEvent(new Event("library-changed"));
    }).catch(() => undefined);
  }, [id]);

  useEffect(() => {
    document.title = paper ? `${paper.title} - 论文详情 - 文献检索` : "论文详情 - 文献检索";
  }, [paper]);
  useEffect(() => {
    if (paper && location.hash === "#analysis") document.getElementById("analysis")?.scrollIntoView({ block: "start" });
  }, [paper, location.hash]);

  useEffect(() => {
    setPaper(null);
    if (!id) {
      setLoading(false);
      setError("缺少论文编号");
      setPaper(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetchPaper(id, controller.signal)
      .then((data) => setPaper(data))
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setPaper(null);
        setError(err.message || "论文详情加载失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [id, retry]);

  return (
    <main className="wrap detail paper-detail">
      <div className="breadcrumbs">
        <Link to={back}>{back.startsWith("/recommend") ? "返回相关推荐" : back.startsWith("/graph") ? "返回原图谱" : back.startsWith("/library") ? "返回文献列表" : "返回检索结果"}</Link>
        <span>论文详情</span>
      </div>
      {id ? <PaperViews id={id} from={back} /> : null}
      {loading ? (
        <Spin tip="正在加载论文"><div style={{ height: 120 }} /></Spin>
      ) : null}
      {error ? (
        <Alert
          style={{ marginTop: 16 }}
          type="error"
          showIcon
          message={error}
          action={
            <Button size="small" onClick={() => setRetry((value) => value + 1)}>
              重试
            </Button>
          }
        />
      ) : null}
      {paper ? (
        <div className="paper-layout"><div className="paper-reading">
          <header className="surface paper-heading"><div className="tag-row">
            {paper.sources.map((source) => (
              <Tag key={source} color={source === "arxiv" ? "blue" : "geekblue"}>
                {SOURCE_LABEL[source] ?? source}
              </Tag>
            ))}
          </div>
          <h1>{paper.title}</h1>
          <p className="paper-authors">{formatAuthors(paper.authors)}</p>
          <p className="meta">{paper.year ?? "年份未知"} · {paper.venue ?? "发表来源未知"}</p>
          <div className="card-links">
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
          </header>
          <section className="surface paper-abstract" id="abstract"><div className="section-heading"><div><span className="page-kicker">原文内容</span><h2>论文摘要</h2></div></div>
            <Typography.Paragraph>{paper.abstract || "摘要缺失"}</Typography.Paragraph>
          </section>
          <AnalysisPanel paperId={id} onImported={refreshPaper} />
        </div><aside className="paper-sidebar">
          <section className="surface paper-facts"><h2>文献信息</h2><Descriptions column={1} size="small">
            <Descriptions.Item label="年份">{paper.year ?? "未知"}</Descriptions.Item>
            <Descriptions.Item label="期刊／会议">{paper.venue ?? "未知"}</Descriptions.Item>
            <Descriptions.Item label="引用量">{paper.citation_count != null ? paper.citation_count.toLocaleString("zh-CN") : "未知"}</Descriptions.Item>
            <Descriptions.Item label="CCF">{ccfLabel(paper)}</Descriptions.Item>
            <Descriptions.Item label="中科院分区">{paper.cas_partition ?? "未知"}</Descriptions.Item>
            <Descriptions.Item label="arXiv">{paper.arxiv_id ?? "无"}</Descriptions.Item>
            <Descriptions.Item label="DOI">{paper.doi ?? "无"}</Descriptions.Item>
          </Descriptions><ImportButton key={paper.id} paper={paper} onSaved={(saved) => setPaper((current) => current?.id === paper.id ? saved : current)} /></section>
          <nav className="surface reading-nav" aria-label="论文阅读与探索"><span className="page-kicker">本篇阅读目录</span><a href="#abstract">论文摘要</a><a href="#analysis">AI 内容分析</a><Link to={graphLocation(location.pathname + location.search, id)}>在集合图谱中定位 ↗</Link></nav>
        </aside></div>
      ) : null}
    </main>
  );
}

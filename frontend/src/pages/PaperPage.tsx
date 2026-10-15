import { useEffect, useState } from "react";
import { Alert, Button, Descriptions, Spin, Tag, Typography } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { fetchPaper } from "../api";
import { formatAuthors } from "../components/PaperCard";
import type { Paper } from "../types";

const SOURCE_LABEL: Record<string, string> = {
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
};

export default function PaperPage() {
  const [params] = useSearchParams();
  const id = params.get("id")?.trim() ?? "";
  const from = params.get("from") ?? "/";
  const back = from.startsWith("/") && !from.startsWith("//") ? from : "/";
  const [paper, setPaper] = useState<Paper | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    document.title = paper ? `${paper.title} - 论文详情 - 文献检索` : "论文详情 - 文献检索";
  }, [paper]);

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
    <main className="wrap detail">
      <Link to={back}>返回检索</Link>
      {loading ? (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin tip="正在加载论文" />
        </div>
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
        <>
          <div className="tag-row" style={{ marginTop: 16 }}>
            {paper.sources.map((source) => (
              <Tag key={source} color={source === "arxiv" ? "blue" : "geekblue"}>
                {SOURCE_LABEL[source] ?? source}
              </Tag>
            ))}
          </div>
          <h1>{paper.title}</h1>
          <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
            <Descriptions.Item label="作者">{formatAuthors(paper.authors)}</Descriptions.Item>
            <Descriptions.Item label="年份">{paper.year ?? "未知"}</Descriptions.Item>
            <Descriptions.Item label="期刊/会议">{paper.venue ?? "未知"}</Descriptions.Item>
            {paper.citation_count != null ? <Descriptions.Item label="引用量">{paper.citation_count.toLocaleString("zh-CN")}</Descriptions.Item> : null}
            {paper.ccf_rank ? <Descriptions.Item label="CCF">{paper.ccf_rank}</Descriptions.Item> : null}
            {paper.cas_partition ? <Descriptions.Item label="中科院分区">{paper.cas_partition}</Descriptions.Item> : null}
            <Descriptions.Item label="arXiv">{paper.arxiv_id ?? "无"}</Descriptions.Item>
            <Descriptions.Item label="DOI">{paper.doi ?? "无"}</Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5}>摘要</Typography.Title>
          <Typography.Paragraph>{paper.abstract || "摘要缺失"}</Typography.Paragraph>
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
        </>
      ) : null}
    </main>
  );
}

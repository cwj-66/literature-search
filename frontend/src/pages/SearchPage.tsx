import { useEffect, useState } from "react";
import { Alert, Button, Empty, Input, InputNumber, Pagination, Select, Spin } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { searchPapers } from "../api";
import { PaperCard } from "../components/PaperCard";
import type { SearchResponse } from "../types";

const SAMPLES = [{ label: "深度学习", query: "deep learning" }, { label: "知识图谱", query: "knowledge graph" }, { label: "大语言模型", query: "large language model" }];
const SOURCE_LABEL: Record<string, string> = {
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
};

function SourceRetry({ seconds, loading, onRetry }: { seconds: number; loading: boolean; onRetry: () => void }) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (seconds <= 0) return;
    const timer = window.setInterval(() => setLeft((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [seconds]);
  return <Button size="small" disabled={left > 0} loading={loading} onClick={onRetry}>
    {left > 0 ? `${left} 秒后重试` : "重试"}
  </Button>;
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get("q")?.trim() ?? "";
  const sourceParam = params.get("source");
  const source = sourceParam === "arxiv" || sourceParam === "semantic_scholar" ? sourceParam : "all";
  const sort = params.get("sort") === "date" ? "date" : "relevance";
  const yearFrom = params.get("year_from") ?? "";
  const yearTo = params.get("year_to") ?? "";
  const page = Math.max(1, Number(params.get("page") || "1") || 1);
  const pageSize = params.get("page_size") === "20" ? 20 : 10;

  const [draft, setDraft] = useState(q);
  const [fromDraft, setFromDraft] = useState<number | null>(yearFrom ? Number(yearFrom) : null);
  const [toDraft, setToDraft] = useState<number | null>(yearTo ? Number(yearTo) : null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [retry, setRetry] = useState(0);

  const [sourceStatus, setSourceStatus] = useState<{ enabled: boolean; message: string }>({ enabled: false, message: "正在检查数据源状态" });
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/sources", { signal: controller.signal }).then((r) => {
      if (!r.ok) throw new Error();
      return r.json();
    }).then((data) => setSourceStatus(data.semantic_scholar)).catch(() => {
      if (!controller.signal.aborted) setSourceStatus({ enabled: false, message: "Semantic Scholar 状态暂不可确认，请使用 arXiv" });
    });
    return () => controller.abort();
  }, []);
  useEffect(() => { document.title = q ? `${q} - 检索结果 - 文献检索` : "文献检索"; }, [q]);

  useEffect(() => {
    setDraft(q);
    setFromDraft(yearFrom ? Number(yearFrom) : null);
    setToDraft(yearTo ? Number(yearTo) : null);
  }, [q, yearFrom, yearTo]);

  useEffect(() => {
    if (!q) {
      setResult(null);
      setError("");
      setLoading(false);
      return;
    }
    setResult(null);
    if (yearFrom && yearTo && Number(yearFrom) > Number(yearTo)) {
      setError("起始年份不能晚于结束年份");
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError("");
    searchPapers(
      { q, page, pageSize, sort, source, yearFrom: yearFrom || undefined, yearTo: yearTo || undefined },
      controller.signal,
    )
      .then((data) => setResult(data))
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setResult(null);
        setError(err.message || "检索失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [q, page, pageSize, sort, source, yearFrom, yearTo, retry]);

  function patch(values: Record<string, string | null>, resetPage = false) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      if (!value) next.delete(key);
      else next.set(key, value);
    }
    if (resetPage) next.delete("page");
    setParams(next);
  }

  function submit() {
    patch(
      {
        q: draft.trim() || null,
        year_from: fromDraft ? String(fromDraft) : null,
        year_to: toDraft ? String(toDraft) : null,
      },
      true,
    );
  }

  function applyYears() {
    if (!q) return;
    if (String(fromDraft ?? "") === yearFrom && String(toDraft ?? "") === yearTo) return;
    patch(
      {
        year_from: fromDraft ? String(fromDraft) : null,
        year_to: toDraft ? String(toDraft) : null,
      },
      true,
    );
  }

  const form = (
    <div className="search-form">
      <Input.Search
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onSearch={submit}
        placeholder="建议用英文关键词，例如 deep learning"
        aria-label="论文关键词"
        enterButton="检索"
        size={q ? "middle" : "large"}
        maxLength={300}
        suffix={draft ? <Button type="text" size="small" aria-label="清除搜索关键词" onClick={() => { setDraft(""); patch({ q: null }, true); }}>清除</Button> : null}
        loading={loading}
      />
      <p className="search-hint">建议用英文关键词；支持“深度学习、机器学习、大语言模型、知识图谱、检索增强生成、人工智能”的精确中英映射。</p>
      <p className="search-hint" role="status">{sourceStatus.message}</p>
      <div className="filters">
        <Select
          aria-label="数据来源"
          value={source}
          style={{ width: 180 }}
          onChange={(value) => patch({ source: value === "all" ? null : value }, true)}
          options={[
            { value: "all", label: "全部可用来源" },
            { value: "arxiv", label: "arXiv" },
            { value: "semantic_scholar", label: sourceStatus.enabled ? "Semantic Scholar" : "Semantic Scholar（暂不可用）", disabled: !sourceStatus.enabled },
          ]}
        />
        <Select
          aria-label="排序方式"
          value={sort}
          style={{ width: 140 }}
          onChange={(value) => patch({ sort: value === "relevance" ? null : value }, true)}
          options={[
            { value: "relevance", label: "按相关性" },
            { value: "date", label: "按发表时间" },
          ]}
        />
        <InputNumber
          value={fromDraft}
          placeholder="起始年份"
          aria-label="起始年份"
          controls={false}
          min={1900}
          max={2100}
          onChange={(value) => setFromDraft(typeof value === "number" ? value : null)}
          onBlur={applyYears}
          onPressEnter={applyYears}
        />
        <InputNumber
          value={toDraft}
          placeholder="结束年份"
          aria-label="结束年份"
          controls={false}
          min={1900}
          max={2100}
          onChange={(value) => setToDraft(typeof value === "number" ? value : null)}
          onBlur={applyYears}
          onPressEnter={applyYears}
        />
        <Select
          aria-label="每页篇数"
          value={String(pageSize)}
          style={{ width: 120 }}
          onChange={(value) => patch({ page_size: value === "10" ? null : value }, true)}
          options={[
            { value: "10", label: "每页 10 篇" },
            { value: "20", label: "每页 20 篇" },
          ]}
        />
      </div>
    </div>
  );

  if (!q) {
    return (
      <div className="hero">
        <h1>文献检索</h1>
        <p className="hero-lead">检索学术论文，实时标注可用数据源</p>
        {form}
        <p><Link to="/library">本地分析库 · 通过 ID / 链接导入</Link></p>
        {error ? <Alert style={{ width: "min(720px, 100%)", marginTop: 16 }} type="error" showIcon message={error} /> : null}
        <div className="samples">
          {SAMPLES.map((item) => (
            <Button key={item.query} onClick={() => patch({ q: item.query }, true)}>
              {item.label}
            </Button>
          ))}
        </div>
      </div>
    );
  }

  const errors = Object.values(result?.source_errors ?? {});
  return (
    <>
      <header className="topbar">
        <div className="wrap topbar-inner">
          <a className="brand" href="/">
            文献检索
          </a>
          <div style={{ flex: 1 }}>{form}</div>
          <Link to="/library">本地分析库</Link>
        </div>
      </header>
      <main className="wrap results">
        {error ? (
          <Alert
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
        {Object.entries(result?.source_errors ?? {}).map(([sourceName, message]) => (
          <Alert
            key={sourceName}
            style={{ marginBottom: 12 }}
            type={result?.papers.length ? "warning" : "error"}
            showIcon
            message={message}
            action={<SourceRetry seconds={result?.source_retry_after?.[sourceName] ?? 0} loading={loading} onRetry={() => setRetry((value) => value + 1)} />}
          />
        ))}
        {Object.entries(result?.source_warnings ?? {}).map(([sourceName, message]) => (
          <Alert key={sourceName} style={{ marginBottom: 12 }} type="warning" showIcon message={message}
            description={result?.source_cached_at?.[sourceName] ? `缓存时间：${new Date(result.source_cached_at[sourceName]).toLocaleString("zh-CN")}` : undefined}
            action={<SourceRetry seconds={result?.source_retry_after?.[sourceName] ?? 0} loading={loading} onRetry={() => setRetry((value) => value + 1)} />} />
        ))}
        {loading ? (
          <div style={{ padding: 48, textAlign: "center" }}>
            <Spin tip="正在检索可用数据源" />
          </div>
        ) : null}
        {!loading && result ? (
          <>
            <div className="summary">
              {result.query !== q ? <span>实际检索词：{result.query}</span> : null}
              {errors.length && !result.papers.length ? <span>数据源暂不可用，暂无可展示结果</span> : <span>{result.total_estimated ? `约 ${result.total.toLocaleString("zh-CN")} 篇` : `共 ${result.total.toLocaleString("zh-CN")} 篇`}</span>}
              <span>本页 {result.papers.length} 篇</span>
              {Object.entries(result.source_counts).map(([key, count]) => (
                <span key={key}>
                  {SOURCE_LABEL[key] ?? key} {count.toLocaleString("zh-CN")} 篇
                </span>
              ))}
            </div>
            {result.papers.length === 0 && errors.length === 0 ? <Empty description="没有找到相关论文，请尝试英文关键词或放宽年份范围" /> : null}
            {result.papers.map((paper) => (
              <PaperCard key={paper.id} paper={paper} />
            ))}
            {result.total > pageSize ? (
              <div className="pager">
                <Pagination
                  current={page}
                  pageSize={pageSize}
                  total={Math.min(result.total, source === "semantic_scholar" ? 1000 : 100 * pageSize)}
                  showSizeChanger={false}
                  onChange={(next) => patch({ page: next === 1 ? null : String(next) })}
                />
              </div>
            ) : null}
          </>
        ) : null}
      </main>
    </>
  );
}

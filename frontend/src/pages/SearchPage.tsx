import { useEffect, useState } from "react";
import { Alert, Button, Empty, Input, InputNumber, Pagination, Select, Spin, Tag } from "antd";
import { useSearchParams } from "react-router-dom";
import { searchPapers } from "../api";
import { PaperCard } from "../components/PaperCard";
import type { SearchResponse } from "../types";

const SAMPLES = ["Retrieval-Augmented Generation", "knowledge graph", "large language model"];
const SOURCE_LABEL: Record<string, string> = {
  arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar",
};

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

  function yearsInvalid() {
    return Boolean(fromDraft && toDraft && fromDraft > toDraft);
  }

  function submit() {
    if (yearsInvalid()) {
      setError("起始年份不能晚于结束年份");
      return;
    }
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
    if (!q || yearsInvalid()) return;
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
        placeholder="输入关键词，例如 Retrieval-Augmented Generation"
        enterButton="检索"
        size={q ? "middle" : "large"}
        maxLength={300}
        allowClear
        loading={loading}
      />
      <div className="filters">
        <Select
          value={source}
          style={{ width: 180 }}
          onChange={(value) => patch({ source: value === "all" ? null : value }, true)}
          options={[
            { value: "all", label: "全部来源" },
            { value: "arxiv", label: "arXiv" },
            { value: "semantic_scholar", label: "Semantic Scholar" },
          ]}
        />
        <Select
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
          min={1900}
          max={2100}
          onChange={(value) => setFromDraft(typeof value === "number" ? value : null)}
          onBlur={applyYears}
          onPressEnter={applyYears}
        />
        <InputNumber
          value={toDraft}
          placeholder="结束年份"
          min={1900}
          max={2100}
          onChange={(value) => setToDraft(typeof value === "number" ? value : null)}
          onBlur={applyYears}
          onPressEnter={applyYears}
        />
        <Select
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
        <p className="hero-lead">从 arXiv 和 Semantic Scholar 查找论文</p>
        {form}
        {error ? <Alert style={{ width: "min(720px, 100%)", marginTop: 16 }} type="error" showIcon message={error} /> : null}
        <div className="samples">
          {SAMPLES.map((item) => (
            <Tag key={item} style={{ cursor: "pointer" }} onClick={() => patch({ q: item }, true)}>
              {item}
            </Tag>
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
        {errors.map((message) => (
          <Alert
            key={message}
            style={{ marginBottom: 12 }}
            type={result?.papers.length ? "warning" : "error"}
            showIcon
            message={message}
          />
        ))}
        {loading ? (
          <div style={{ padding: 48, textAlign: "center" }}>
            <Spin tip="正在检索 arXiv 和 Semantic Scholar" />
          </div>
        ) : null}
        {!loading && result ? (
          <>
            <div className="summary">
              <span>{result.total_estimated ? `约 ${result.total} 篇` : `共 ${result.total} 篇`}</span>
              <span>本页 {result.papers.length} 篇</span>
              {Object.entries(result.source_counts).map(([key, count]) => (
                <span key={key}>
                  {SOURCE_LABEL[key] ?? key} {count.toLocaleString()} 篇
                </span>
              ))}
            </div>
            {result.papers.length === 0 && errors.length === 0 ? <Empty description="没有找到相关论文" /> : null}
            {result.papers.map((paper) => (
              <PaperCard key={paper.id} paper={paper} />
            ))}
            {result.total > pageSize ? (
              <div className="pager">
                <Pagination
                  current={page}
                  pageSize={pageSize}
                  total={result.total}
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

import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Input, InputNumber, Pagination, Select, Spin } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { searchPapers } from "../api";
import { PaperCard } from "../components/PaperCard";
import type { SearchResponse } from "../types";

const SAMPLES = [{ label: "检索增强生成 RAG", query: "Retrieval-Augmented Generation" }, { label: "深度学习", query: "deep learning" }, { label: "知识图谱", query: "knowledge graph" }, { label: "大语言模型", query: "large language model" }];
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
  const previousRetry = useRef(0);

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
    let prefetchTimer = 0;
    const forced = previousRetry.current !== retry;
    previousRetry.current = retry;
    setLoading(true);
    setError("");
    searchPapers(
      { q, page, pageSize, sort, source, yearFrom: yearFrom || undefined, yearTo: yearTo || undefined },
      controller.signal,
      data => { if (!controller.signal.aborted) setResult(data); },
      forced,
    )
      .then((data) => {
        if (controller.signal.aborted) return;
        setResult(data);
        const maximum = Math.min(100, source === "semantic_scholar" ? Math.floor(1000 / pageSize) : 100);
        if (page < maximum && page * pageSize < data.total && !Object.keys(data.source_errors).length
            && !Object.keys(data.source_retry_after || {}).length && !Object.keys(data.source_cached_at || {}).length) {
          prefetchTimer = window.setTimeout(() => {
            void searchPapers({ q, page: page + 1, pageSize, sort, source, yearFrom: yearFrom || undefined, yearTo: yearTo || undefined }, controller.signal).catch(() => undefined);
          }, 350);
        }
      })
      .catch((err: Error) => {
        if (controller.signal.aborted) return;
        setError(err.message || "检索失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => { controller.abort(); window.clearTimeout(prefetchTimer); };
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
        loading={loading && draft.trim() === q}
      />
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
      <details className="search-help"><summary>检索提示与数据源状态</summary>
        <p className="search-hint">建议用英文关键词；支持“深度学习、机器学习、大语言模型、知识图谱、检索增强生成、人工智能”的精确中英映射。</p>
        <p className="search-hint" role="status">{sourceStatus.message}</p>
      </details>
    </div>
  );

  if (!q) {
    return (
      <main className="home-page wrap">
        <div className="page-kicker">RESEARCH WORKSPACE</div>
        <h1>检索论文，梳理研究思路。</h1>
        <p className="home-lead">发现相关文献，提炼研究内容与技术方案，探索论文之间的联系。</p>
        <div className="home-layout"><section className="surface home-search" aria-label="文献检索">
        <div className="section-heading"><div><h2>从一个研究问题开始</h2><p>输入关键词，设置来源与年份，找到值得阅读的论文。</p></div><span className="subtle-pill">arXiv / Semantic Scholar</span></div>
        {form}
        {error ? <Alert style={{ width: "min(720px, 100%)", marginTop: 16 }} type="error" showIcon message={error} /> : null}
        <div className="samples">
          <span className="meta">试试这些主题</span>
          {SAMPLES.map((item) => (
            <Button key={item.query} onClick={() => patch({ q: item.query }, true)}>
              {item.label}
            </Button>
          ))}
        </div>
        </section><aside className="surface research-path"><span className="page-kicker">研究路径</span><h2>把找到的论文，变成可用的知识。</h2>
          <ol><li><b>01</b><div><strong>检索与筛选</strong><p>按研究关键词、来源和年份缩小范围。</p></div></li>
          <li><b>02</b><div><Link to="/library">加入论文库</Link><p>收藏研究材料，也可以通过 ID 或链接导入。</p></div></li>
          <li><b>03</b><div><Link to="/library?view=analysis">阅读与分析</Link><p>提取研究内容、技术方案和创新点，核对原文依据。</p></div></li>
          <li><b>04</b><div><Link to="/graph">探索知识关系</Link><p>查看相似文献与实体关系，梳理论文时间脉络。</p></div></li></ol>
        </aside></div>
        <div className="home-shortcuts"><Link to="/library"><span>已有论文？</span><strong>通过链接或 ID 导入 →</strong></Link><Link to="/graph"><span>先看一个示例</span><strong>探索 RAG 知识图谱 →</strong></Link></div>
      </main>
    );
  }

  const errors = Object.values(result?.source_errors ?? {});
  return (
    <>
      <main className="wrap results">
        <div className="page-heading"><div><span className="page-kicker">文献检索</span><h1>检索结果</h1><p>当前关键词：<strong>{q}</strong></p></div><Link className="text-action" to="/library">前往论文库 →</Link></div>
        <section className="surface results-search" aria-label="调整检索条件">{form}</section>
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
        {loading && !result?.papers.length ? (
          <div style={{ padding: 48, textAlign: "center" }}>
            <Spin tip="正在检索可用数据源" />
          </div>
        ) : null}
        {result && (result.papers.length > 0 || !loading) ? (
          <>
            <div className="summary">
              {loading && result.papers.length ? <span role="status">已显示先返回的结果，仍在获取{(result.pending_sources || []).map(name => SOURCE_LABEL[name] || name).join("、")}；结果与排序会继续更新。</span> : null}
              {result.query !== q ? <span>实际检索词：{result.query}</span> : null}
              {errors.length && !result.papers.length ? <span>数据源暂不可用，暂无可展示结果</span> : <span>{result.total_is_limited ? `本批候选 ${result.total.toLocaleString("zh-CN")} 篇` : result.total_estimated ? `约 ${result.total.toLocaleString("zh-CN")} 篇` : `共 ${result.total.toLocaleString("zh-CN")} 篇`}</span>}
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
            {!loading && !result.pending_sources?.length && result.total > pageSize ? (
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

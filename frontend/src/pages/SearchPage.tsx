import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Input, InputNumber, Pagination, Select, Skeleton } from "antd";
import { LoadingOutlined, SearchOutlined } from "@ant-design/icons";
import type { InputRef } from "antd";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { fetchLibrary, searchPapers } from "../api";
import { ImportButton } from "../components/ImportPaper";
import { ExternalLinks, PaperRow } from "../components/PaperRow";
import type { LibraryResponse, SearchResponse } from "../types";
import { paperKey, SOURCE_LABEL } from "../types";
import { paperLocation } from "../workspace";

const SAMPLES = ["检索增强生成", "知识图谱", "大模型幻觉", "retrieval augmented generation", "knowledge graph", "large language model"];

function SourceRetry({ seconds, loading, onRetry }: { seconds: number; loading: boolean; onRetry: () => void }) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    setLeft(seconds);
    if (seconds <= 0) return;
    const timer = window.setInterval(() => setLeft(value => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [seconds]);
  return <Button size="small" disabled={left > 0} loading={loading} onClick={onRetry}>{left > 0 ? `${left} 秒后重试` : "重试"}</Button>;
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
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
  const [showFilters, setShowFilters] = useState(Boolean(yearFrom || yearTo || sourceParam || params.get("sort")));
  const previousRetry = useRef(0);
  const searchInput = useRef<InputRef>(null);

  const [sourceStatus, setSourceStatus] = useState<{ enabled: boolean; message: string; checking?: boolean }>({ enabled: false, message: "正在检查数据源状态", checking: true });
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/sources", { signal: controller.signal }).then(r => {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(data => setSourceStatus(data.semantic_scholar)).catch(() => {
      if (!controller.signal.aborted) setSourceStatus({ enabled: false, message: "Semantic Scholar 状态暂不可确认，可先使用 arXiv" });
    });
    return () => controller.abort();
  }, []);
  useEffect(() => { document.title = q ? `${q} - 检索 - 文献研读` : "检索 - 文献研读"; }, [q]);
  const [recent, setRecent] = useState<LibraryResponse | null>(null);
  useEffect(() => {
    if (q) return;
    const controller = new AbortController();
    fetchLibrary(1, controller.signal, 4).then(setRecent).catch(() => undefined);
    return () => controller.abort();
  }, [q]);

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
      .then(data => {
        if (controller.signal.aborted) return;
        setResult(data);
        if (page * pageSize < data.total && !Object.keys(data.source_errors).length
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
    patch({ q: draft.trim() || null, year_from: fromDraft ? String(fromDraft) : null, year_to: toDraft ? String(toDraft) : null }, true);
  }

  function applyYears() {
    if (!q) return;
    if (String(fromDraft ?? "") === yearFrom && String(toDraft ?? "") === yearTo) return;
    patch({ year_from: fromDraft ? String(fromDraft) : null, year_to: toDraft ? String(toDraft) : null }, true);
  }

  const searchBox = <Input.Search
    ref={searchInput}
    className="search-input"
    value={draft}
    onChange={event => setDraft(event.target.value)}
    onSearch={submit}
    placeholder="输入研究主题、关键词或论文标题"
    aria-label="论文关键词"
    enterButton={<><SearchOutlined aria-hidden />检索</>}
    size="large"
    maxLength={300}
    allowClear
    loading={loading && draft.trim() === q}
  />;

  const filters = <div className="filter-group" role="group" aria-label="检索条件">
    <label className="filter-field"><span>来源</span>
      <Select aria-label="数据来源" value={source} onChange={value => patch({ source: value === "all" ? null : value }, true)} options={[
        { value: "all", label: "全部可用来源" },
        { value: "arxiv", label: "arXiv" },
        { value: "semantic_scholar", label: sourceStatus.enabled ? "Semantic Scholar" : "Semantic Scholar（不可用）", disabled: !sourceStatus.enabled },
      ]} />
    </label>
    <label className="filter-field"><span>排序</span>
      <Select aria-label="排序方式" value={sort} onChange={value => patch({ sort: value === "relevance" ? null : value }, true)} options={[
        { value: "relevance", label: "相关性" },
        { value: "date", label: "发表时间" },
      ]} />
    </label>
    <div className="filter-field"><span id="year-range-label">发表年份</span>
      <div className="year-range" role="group" aria-labelledby="year-range-label">
        <InputNumber value={fromDraft} placeholder="起" aria-label="起始年份" controls={false} min={1900} max={2100}
          onChange={value => setFromDraft(typeof value === "number" ? value : null)} onBlur={applyYears} onPressEnter={q ? applyYears : submit} />
        <span aria-hidden>–</span>
        <InputNumber value={toDraft} placeholder="止" aria-label="结束年份" controls={false} min={1900} max={2100}
          onChange={value => setToDraft(typeof value === "number" ? value : null)} onBlur={applyYears} onPressEnter={q ? applyYears : submit} />
      </div>
    </div>
    <label className="filter-field"><span>每页</span>
      <Select aria-label="每页篇数" value={String(pageSize)} onChange={value => patch({ page_size: value === "10" ? null : value }, true)} options={[
        { value: "10", label: "10 篇" },
        { value: "20", label: "20 篇" },
      ]} />
    </label>
  </div>;

  if (!q) {
    return <main className="page search-home">
      <div className="search-sources" aria-label="检索来源"><span>arXiv</span><span>Semantic Scholar</span><span className="search-source-caption">学术文献检索</span></div>
      <h1 className="search-home-title">检索论文</h1>
      <p className="search-home-lead">查找相关研究，收藏值得读的论文，带着证据继续探索。</p>
      <div className="search-home-box">{searchBox}</div>
      <div className="search-home-meta">
        <button type="button" className="link-button" aria-expanded={showFilters} onClick={() => setShowFilters(v => !v)}>{showFilters ? "收起筛选" : "来源与年份"}</button>
        <span className="small-print">支持中英文关键词</span>
      </div>
      {showFilters ? <div className="search-home-filters">{filters}</div> : null}
      <div className="search-examples"><span>试试这些主题</span><div className="sample-list" aria-label="示例关键词">{SAMPLES.map(word => <button type="button" className="sample-button" key={word} onClick={() => { setDraft(word); searchInput.current?.focus(); }}>{word}</button>)}</div></div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {recent?.papers.length ? <section className="recent" aria-labelledby="recent-title">
        <div className="recent-head"><h2 id="recent-title">最近加入文献库</h2><Link to="/library">全部 {recent.total} 篇</Link></div>
        <ul>{recent.papers.map(paper => <li key={paperKey(paper)}>
          <Link to={paperLocation(paperKey(paper), "/library")}>{paper.title}</Link>
          <span className="muted">{[paper.year, paper.authors[0]].filter(Boolean).join(" · ")}</span>
        </li>)}</ul>
      </section> : null}
      <div className="search-home-note"><p>已有论文 ID 或链接？<Link to="/library?import=1">直接导入文献库</Link></p><Link to="/graph?mode=example">探索 RAG 示例图谱 <span className="muted">（演示）</span></Link></div>
      {!sourceStatus.enabled && !sourceStatus.checking ? <p className="field-help" role="status">{sourceStatus.message}</p> : null}
    </main>;
  }

  const sourceErrors = Object.entries(result?.source_errors ?? {});
  const sourceNotes = [
    ...Object.entries(result?.source_warnings ?? {}).map(([key, text]) => `${SOURCE_LABEL[key] ?? key}：${text}`),
    ...Object.entries(result?.source_cached_at ?? {}).map(([key, at]) => `${SOURCE_LABEL[key] ?? key} 暂用 ${new Date(at).toLocaleString("zh-CN")} 的缓存结果`),
  ];
  const retrySeconds = Math.max(0, ...Object.values(result?.source_retry_after ?? {}));
  const pending = result?.pending_sources ?? [];
  const papers = result?.papers ?? [];
  const here = location.pathname + location.search;
  return <main className="page page-wide search-results">
    <div className="results-layout">
      <aside className="results-filters" aria-label="筛选">{filters}</aside>
      <section className="results-main" aria-label="检索结果" aria-busy={loading}>
        <div className="results-search">{searchBox}</div>
        <div className="results-summary" aria-live="polite">
          {result ? <>
            <strong>{result.total_estimated || result.total_is_limited ? "约 " : ""}{result.total.toLocaleString("zh-CN")} 篇</strong>
            <span>第 {page} 页</span>
            {Object.entries(result.source_counts).map(([key, count]) => <span key={key}>{SOURCE_LABEL[key] ?? key} {count.toLocaleString("zh-CN")}</span>)}
            {pending.length ? <span className="pending"><LoadingOutlined aria-hidden />仍在等待 {pending.map(key => SOURCE_LABEL[key] ?? key).join("、")}</span> : null}
          </> : loading ? <span className="pending"><LoadingOutlined aria-hidden />正在检索“{q}”</span> : null}
        </div>
        {error && !papers.length ? <Alert type="error" showIcon message={error} action={<SourceRetry seconds={retrySeconds} loading={loading} onRetry={() => setRetry(v => v + 1)} />} /> : null}
        {error && papers.length ? <Alert type="warning" showIcon message={`${error}；已收到的结果保留如下`} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
        {sourceErrors.length && !loading ? <Alert type="warning" showIcon message={sourceErrors.map(([key, text]) => `${SOURCE_LABEL[key] ?? key} 暂不可用：${text}`).join("；")}
          action={<SourceRetry seconds={retrySeconds} loading={loading} onRetry={() => setRetry(v => v + 1)} />} /> : null}
        {sourceNotes.length ? <p className="results-note">{sourceNotes.join("；")}</p> : null}
        {loading && !papers.length ? <div className="row-skeletons" aria-hidden>{[0, 1, 2].map(i => <Skeleton key={i} active title={{ width: "70%" }} paragraph={{ rows: 2 }} />)}</div> : null}
        {result && !papers.length && !loading && !error && !sourceErrors.length ? <Empty description="没有找到相关论文。可以换用英文关键词，或放宽年份范围。" /> : null}
        <div className="paper-list">
          {papers.map(paper => <PaperRow key={paper.id} paper={paper} from={here}
            side={<><ImportButton paper={paper} /><ExternalLinks paper={paper} /></>} />)}
        </div>
        {result && (page > 1 || result.total > pageSize) ? <div className="pager">
          <Pagination aria-label="检索结果分页" current={page} pageSize={pageSize}
            total={Math.max(result.total, (page - 1) * pageSize + papers.length)}
            showSizeChanger={false} showLessItems responsive
            onChange={next => patch({ page: next === 1 ? null : String(next) })} />
        </div> : null}
      </section>
    </div>
  </main>;
}

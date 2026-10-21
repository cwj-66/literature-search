import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Input, InputNumber, Pagination, Segmented, Select, Skeleton } from "antd";
import { LoadingOutlined, SearchOutlined } from "@ant-design/icons";
import type { InputRef } from "antd";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { searchPapers } from "../api";
import { CollectButton } from "../components/CollectButton";
import { ExternalLinks, PaperRow } from "../components/PaperRow";
import { useSessionState } from "../hooks";
import type { SearchResponse } from "../types";
import { SOURCE_LABEL } from "../types";

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
  const [homeSource, setHomeSource] = useState<"arxiv" | "semantic_scholar">(source === "semantic_scholar" ? "semantic_scholar" : "arxiv");
  const previousRetry = useRef(0);
  const searchInput = useRef<InputRef>(null);
  const heroInput = useRef<HTMLInputElement>(null);
  const [excluded, setExcluded] = useSessionState<string[]>("search-excluded", []);
  const [citeSort, setCiteSort] = useState(false);

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
  useEffect(() => {
    if (source === "arxiv" || source === "semantic_scholar") setHomeSource(source);
  }, [source]);
  useEffect(() => {
    if (!sourceStatus.checking && !sourceStatus.enabled && homeSource === "semantic_scholar") setHomeSource("arxiv");
  }, [sourceStatus.checking, sourceStatus.enabled, homeSource]);

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

  function startSearch(query: string) {
    const next = query.trim();
    if (!next) {
      heroInput.current?.focus();
      return;
    }
    setDraft(next);
    patch({ q: next, source: homeSource, year_from: fromDraft ? String(fromDraft) : null, year_to: toDraft ? String(toDraft) : null }, true);
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
    placeholder="输入研究问题、关键词或论文标题，例如：降低 RAG 回答幻觉的方法"
    aria-label="论文关键词"
    enterButton={<><SearchOutlined aria-hidden />检索</>}
    size="large"
    maxLength={300}
    allowClear
    loading={loading && draft.trim() === q}
  />;

  const thisYear = new Date().getFullYear();
  const yearChips = [3, 5, 10].map(n => ({ n, from: String(thisYear - n + 1) }));
  const filters = <div className="filter-group" role="group" aria-label="检索条件">
    <div className="filter-field"><span>时间</span>
      <div className="chip-row">{yearChips.map(({ n, from }) => <button type="button" key={n} className={`chip${yearFrom === from && !yearTo ? " is-on" : ""}`}
        onClick={() => patch({ year_from: yearFrom === from && !yearTo ? null : from, year_to: null }, true)}>近 {n} 年</button>)}</div>
    </div>
    <label className="filter-field"><span>来源</span>
      <Select aria-label="数据来源" value={source} onChange={value => patch({ source: value === "all" ? null : value }, true)} options={[
        { value: "all", label: "全部可用来源" },
        { value: "arxiv", label: "arXiv" },
        { value: "semantic_scholar", label: sourceStatus.enabled ? "Semantic Scholar" : "Semantic Scholar（不可用）", disabled: !sourceStatus.enabled },
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
    const scholarOff = !sourceStatus.checking && !sourceStatus.enabled;
    return <main className="page search-home">
      <div className="search-hero">
        <div className="search-hero-copy">
          <h1>检索论文</h1>
          <div className="source-pills" role="group" aria-label="数据源">
            <button type="button" className={homeSource === "arxiv" ? "source-pill is-on" : "source-pill"} aria-pressed={homeSource === "arxiv"} onClick={() => setHomeSource("arxiv")}>
              <ArxivMark />arXiv
            </button>
            <button type="button" className={homeSource === "semantic_scholar" ? "source-pill is-on" : "source-pill"} aria-pressed={homeSource === "semantic_scholar"} disabled={scholarOff} title={scholarOff ? sourceStatus.message : undefined} onClick={() => setHomeSource("semantic_scholar")}>
              <ScholarMark />Semantic Scholar
            </button>
          </div>
          <form className="search-hero-bar" role="search" onSubmit={event => { event.preventDefault(); startSearch(draft); }}>
            <SearchOutlined aria-hidden />
            <input ref={heroInput} value={draft} maxLength={300} aria-label="论文关键词" placeholder="输入研究问题、关键词或论文标题，例如：降低 RAG 回答幻觉的方法" onChange={event => setDraft(event.target.value)} />
            <button type="submit">搜索</button>
          </form>
          <div className="search-topics">
            <span>推荐主题</span>
            <div className="sample-list" aria-label="推荐主题">{SAMPLES.map(word => <button type="button" className="sample-button" key={word} onClick={() => startSearch(word)}>{word}</button>)}</div>
          </div>
          {scholarOff ? <p className="search-hero-status" role="status">{sourceStatus.message}</p> : null}
        </div>
        <SearchArt />
      </div>
    </main>;
  }

  const sourceErrors = Object.entries(result?.source_errors ?? {});
  const sourceNotes = [
    ...Object.entries(result?.source_warnings ?? {}).map(([key, text]) => `${SOURCE_LABEL[key] ?? key}：${text}`),
    ...Object.entries(result?.source_cached_at ?? {}).map(([key, at]) => `${SOURCE_LABEL[key] ?? key} 暂用 ${new Date(at).toLocaleString("zh-CN")} 的缓存结果`),
  ];
  const retrySeconds = Math.max(0, ...Object.values(result?.source_retry_after ?? {}));
  const pending = result?.pending_sources ?? [];
  const received = result?.papers ?? [];
  const hidden = new Set(excluded);
  const visibleAll = received.filter(p => !hidden.has(p.id));
  const excludedCount = received.length - visibleAll.length;
  const papers = citeSort ? [...visibleAll].sort((a, b) => (b.citation_count ?? -1) - (a.citation_count ?? -1)) : visibleAll;
  const here = location.pathname + location.search;
  return <main className="page page-wide search-results">
    <div className="results-layout">
      <aside className="results-filters" aria-label="筛选">
        {filters}
        <p className="field-help">需要中文问题生成检索计划？<Link to="/">进入工作区的智能调研</Link></p>
      </aside>
      <section className="results-main" aria-label="检索结果" aria-busy={loading}>
        <div className="results-search">{searchBox}</div>
        <div className="results-tabs">
          <Segmented aria-label="排序方式" value={citeSort ? "cite" : sort} onChange={value => {
            if (value === "cite") { setCiteSort(true); return; }
            setCiteSort(false); patch({ sort: value === "relevance" ? null : String(value) }, true);
          }} options={[{ value: "relevance", label: "综合" }, { value: "date", label: "最新" }, { value: "cite", label: "引用数（本页）" }]} />
          {excludedCount ? <span className="small-print">已排除 {excludedCount} 篇 <button type="button" className="link-button" onClick={() => setExcluded(list => list.filter(id => !received.some(p => p.id === id)))}>恢复</button></span> : null}
        </div>
        <div className="results-summary" aria-live="polite">
          {result ? <>
            <strong>{result.total_estimated || result.total_is_limited ? "约 " : ""}{result.total.toLocaleString("zh-CN")} 篇</strong>
            <span>第 {page} 页</span>
            {Object.entries(result.source_counts).map(([key, count]) => <span key={key}>{SOURCE_LABEL[key] ?? key} {count.toLocaleString("zh-CN")}</span>)}
            {pending.length ? <span className="pending"><LoadingOutlined aria-hidden />仍在等待 {pending.map(key => SOURCE_LABEL[key] ?? key).join("、")}</span> : null}
            {received.length ? sourceErrors.map(([key, text]) => <span key={key}>{`${SOURCE_LABEL[key] ?? key} 暂不可用：${text}`}</span>) : null}
          </> : loading ? <span className="pending"><LoadingOutlined aria-hidden />正在检索“{q}”</span> : null}
        </div>
        {error && !papers.length ? <Alert type="error" showIcon message={error} action={<SourceRetry seconds={retrySeconds} loading={loading} onRetry={() => setRetry(v => v + 1)} />} /> : null}
        {error && papers.length ? <Alert type="warning" showIcon message={`${error}；已收到的结果保留如下`} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
        {sourceErrors.length && !received.length && !loading ? <Alert type="warning" showIcon message={sourceErrors.map(([key, text]) => `${SOURCE_LABEL[key] ?? key} 暂不可用：${text}`).join("；")}
          action={<SourceRetry seconds={retrySeconds} loading={loading} onRetry={() => setRetry(v => v + 1)} />} /> : null}
        {sourceNotes.length ? <p className="results-note">{sourceNotes.join("；")}</p> : null}
        {loading && !papers.length ? <div className="row-skeletons" aria-hidden>{[0, 1, 2].map(i => <Skeleton key={i} active title={{ width: "70%" }} paragraph={{ rows: 2 }} />)}</div> : null}
        {result && !papers.length && !loading && !error && !sourceErrors.length ? <Empty description={received.length ? "当前筛选或排除后没有论文，可恢复已排除论文。" : "没有找到相关论文。可以换用英文关键词，或放宽年份范围。"} /> : null}
        <div className="paper-list">
          {papers.map(paper => <PaperRow key={paper.id} paper={paper} from={here}
            side={<><CollectButton paper={paper} /><ExternalLinks paper={paper} />
              <button type="button" className="link-button quiet-link" onClick={() => setExcluded(list => [...list, paper.id])}>排除</button></>} />)}
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

function ArxivMark() {
  return <svg className="mark-arxiv" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.2 12 6.4 4h1.4l3.2 8M4.6 9.2h4.2" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function ScholarMark() {
  return <svg className="mark-s2" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.8 13.6 8 8 14.2 2.4 8Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>;
}

function SearchArt() {
  return <div className="search-hero-art" aria-hidden="true">
    <svg viewBox="0 0 480 360">
      <ellipse cx="250" cy="188" rx="168" ry="128" fill="none" stroke="#d5e3fb" strokeDasharray="4 7" />
      <rect x="214" y="28" width="214" height="148" rx="18" fill="#f4f7ff" stroke="#e1e9f8" />
      <rect x="234" y="50" width="74" height="8" rx="4" fill="#d5e3fb" />
      <rect x="234" y="70" width="154" height="6" rx="3" fill="#e6eefb" />
      <rect x="234" y="86" width="128" height="6" rx="3" fill="#e6eefb" />
      <rect x="234" y="102" width="142" height="6" rx="3" fill="#e6eefb" />
      <rect x="56" y="108" width="268" height="176" rx="20" fill="#f7f9ff" stroke="#d7e4f8" />
      <rect x="80" y="132" width="96" height="10" rx="5" fill="#b9d0fb" />
      <rect x="80" y="156" width="196" height="7" rx="3.5" fill="#dce7fa" />
      <rect x="80" y="174" width="168" height="7" rx="3.5" fill="#dce7fa" />
      <rect x="80" y="192" width="140" height="7" rx="3.5" fill="#dce7fa" />
      <rect x="80" y="222" width="72" height="28" rx="8" fill="#e7f0ff" />
      <rect x="162" y="222" width="72" height="28" rx="8" fill="#f3eeff" />
      <rect x="248" y="196" width="156" height="104" rx="16" fill="#fff" stroke="#e4eaf6" />
      <rect x="266" y="214" width="52" height="6" rx="3" fill="#c9d8f8" />
      <rect x="266" y="230" width="104" height="5" rx="2.5" fill="#e7eefb" />
      <rect x="266" y="244" width="84" height="5" rx="2.5" fill="#e7eefb" />
      <rect x="266" y="258" width="96" height="5" rx="2.5" fill="#e7eefb" />
      <circle cx="132" cy="62" r="5" fill="#d6e4ff" />
      <circle cx="408" cy="156" r="4" fill="#e4d9fb" />
      <circle cx="78" cy="286" r="6" fill="#e7eeff" />
    </svg>
  </div>;
}

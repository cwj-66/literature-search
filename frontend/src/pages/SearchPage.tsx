import { useEffect, useRef, useState } from "react";
import { Alert, Button, Empty, Input, InputNumber, Pagination, Select, Spin } from "antd";
import type { InputRef } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { searchPapers } from "../api";
import { PaperCard } from "../components/PaperCard";
import type { SearchResponse } from "../types";

const SAMPLES = ["深度学习", "知识图谱", "大语言模型", "RAG", "Transformer", "machine learning"];
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
  const searchInput = useRef<InputRef>(null);

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
        ref={searchInput}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onSearch={submit}
        placeholder="建议用英文关键词，例如 deep learning"
        aria-label="论文关键词"
        enterButton="检索"
        size={q ? "middle" : "large"}
        maxLength={300}
        suffix={<span><Button type="text" size="small" aria-label="清除搜索关键词" style={{ visibility: draft ? "visible" : "hidden" }} tabIndex={draft ? 0 : -1} onMouseDown={event => event.preventDefault()} onClick={() => { setDraft(""); patch({ q: null }, true); }}>清除</Button></span>}
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
      <div className="search-examples" aria-label="示例关键词">
        <span>试试：</span>{SAMPLES.map(word => <Button type="link" size="small" key={word} onClick={() => { setDraft(word); searchInput.current?.focus(); }}>{word}</Button>)}
      </div>
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

        </section><aside className="surface research-path"><span className="page-kicker">研究路径</span><h2>把找到的论文，变成可用的知识。</h2>
          <ol><li><b>01</b><div><strong>检索与筛选</strong><p>按研究关键词、来源和年份缩小范围。</p></div></li>
          <li><b>02</b><div><Link to="/library">加入我的文献</Link><p>收藏研究材料，也可以通过 ID 或链接导入。</p></div></li>
          <li><b>03</b><div><Link to="/library">阅读与分析</Link><p>提取研究内容、技术方案和创新点，核对原文依据。</p></div></li>
          <li><b>04</b><div><Link to="/graph">探索知识关系</Link><p>查看相似文献与实体关系，梳理论文时间脉络。</p></div></li></ol>
        </aside></div>
        <div className="home-shortcuts"><Link to="/library"><span>已有论文？</span><strong>通过链接或 ID 导入 →</strong></Link><Link to="/graph?mode=example"><span>先看一个示例</span><strong>探索 RAG 知识图谱 →</strong></Link></div>
      </main>
    );
  }

  const hasFailure = Boolean(error || Object.keys(result?.source_errors ?? {}).length);
  const retrySeconds = Math.max(0, ...Object.values(result?.source_retry_after ?? {}));
  return (
    <>
      <main className="wrap results">
        <div className="page-heading"><div><span className="page-kicker">文献检索</span><h1>检索结果</h1><p>当前关键词：<strong>{q}</strong></p></div><Link className="text-action" to="/library">前往论文库 →</Link></div>
        <section className="surface results-search" aria-label="调整检索条件">{form}</section>
        {hasFailure && !loading && !result?.papers.length ? (
          <Alert type="error" showIcon message={error || "暂时无法加载论文，请稍后重试"}
            action={<SourceRetry seconds={retrySeconds} loading={loading} onRetry={() => setRetry(value => value + 1)} />} />
        ) : null}
        {loading && !result?.papers.length ? (
          <div style={{ padding: 48, textAlign: "center" }}>
            <Spin tip="正在检索论文" />
          </div>
        ) : null}
        {result && (result.papers.length > 0 || (!loading && !hasFailure)) ? (
          <>
            <div className="summary">
              <span>{result.total_estimated || result.total_is_limited ? "约" : "共"} {result.total.toLocaleString("zh-CN")} 篇</span>
              <span>本页 {result.papers.length} 篇</span>
              {Object.entries(result.source_counts).map(([key, count]) => (
                <span key={key}>
                  {SOURCE_LABEL[key] ?? key} {count.toLocaleString("zh-CN")} 篇
                </span>
              ))}
            </div>
            {result.papers.length === 0 && !hasFailure ? <Empty description="没有找到相关论文，请尝试英文关键词或放宽年份范围" /> : null}
            {result.papers.map((paper) => (
              <PaperCard key={paper.id} paper={paper} />
            ))}
            {(page > 1 || result.total > pageSize) ? (
              <div className="pager">
                <Pagination
                  aria-label="检索结果分页"
                  current={page}
                  pageSize={pageSize}
                  total={Math.max(result.total, (page - 1) * pageSize + result.papers.length)}
                  showSizeChanger={false}
                  showQuickJumper
                  showLessItems
                  responsive
                  onChange={next => patch({ page: next === 1 ? null : String(next) })}
                />
              </div>
            ) : null}
          </>
        ) : null}
      </main>
    </>
  );
}

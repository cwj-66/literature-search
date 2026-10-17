import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Skeleton } from "antd";
import { DownOutlined, SearchOutlined, UpOutlined } from "@ant-design/icons";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { fetchLibrary } from "../api";
import { AnalysisBadge, useAnalysisStates } from "../components/AnalysisBadge";
import { AskView, QA_LIBRARY_LIMIT } from "../components/studio/AskView";
import { CompareView } from "../components/studio/CompareView";
import { ResearchView } from "../components/studio/ResearchView";
import { MAX_SELECTION, selection, useSelection } from "../selection";
import type { LibraryResponse } from "../types";
import { paperKey } from "../types";

const TABS = [
  { key: "ask", label: "文献问答" },
  { key: "compare", label: "横向对比" },
  { key: "research", label: "智能调研" },
] as const;

export default function StudioPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const here = location.pathname + location.search;
  const tab = TABS.find(item => item.key === params.get("tab"))?.key ?? "ask";
  const scope = params.get("scope") === "library" ? "library" : "selected";
  const picked = useSelection();
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [onlyPicked, setOnlyPicked] = useState(false);
  const set = (values: Record<string, string | null>) => setParams(current => {
    const next = new URLSearchParams(current);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    return next;
  }, { replace: true });

  useEffect(() => { document.title = `${TABS.find(item => item.key === tab)!.label} - 研究台 - 文献研读`; }, [tab]);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    fetchLibrary(1, controller.signal, QA_LIBRARY_LIMIT).then(setData)
      .catch(err => { if (!controller.signal.aborted) setError(err.message || "文献库加载失败"); });
    return () => controller.abort();
  }, [retry]);
  const papers = data?.papers ?? [];
  const listed = new Set(papers.map(paperKey));
  const scopeItems = [
    ...papers.map(paper => ({ id: paperKey(paper), title: paper.title, year: paper.year })),
    ...picked.filter(item => !listed.has(item.id)).map(item => ({ ...item, year: null })),
  ];
  const visible = scopeItems.filter(item => (!onlyPicked || picked.some(p => p.id === item.id)) && (!filter.trim() || `${item.title} ${item.year ?? ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase())));
  const states = useAnalysisStates(tab === "research" ? [] : papers.map(paperKey));
  const titles = useMemo(() => {
    const map = new Map<string, string>();
    papers.forEach(paper => { map.set(paper.id, paper.title); if (paper.local_id) map.set(paper.local_id, paper.title); });
    picked.forEach(item => map.set(item.id, item.title));
    return map;
  }, [papers, picked]);

  const scopePanel = <aside className={`scope-panel${scopeOpen ? " is-open" : ""}`} aria-label="论文范围">
    <div className="scope-head">
      <h2>论文范围</h2>
      <span className="muted">已选 {picked.length}</span>
      {picked.length ? <Button type="link" size="small" onClick={() => selection.clear()}>清除</Button> : null}
      <Button className="scope-expand" size="small" type="text" aria-label={scopeOpen ? "收起论文范围" : "展开论文范围"} aria-expanded={scopeOpen} aria-controls="scope-content" icon={scopeOpen ? <UpOutlined /> : <DownOutlined />} onClick={() => setScopeOpen(value => !value)} />
    </div>
    <div className="scope-panel-body" id="scope-content">
    {data && papers.length ? <div className="scope-tools"><Input allowClear prefix={<SearchOutlined aria-hidden />} value={filter} onChange={event => setFilter(event.target.value)} placeholder="查找标题或年份" aria-label="查找范围内论文" /><Button size="small" type={onlyPicked ? "primary" : "text"} aria-pressed={onlyPicked} onClick={() => setOnlyPicked(value => !value)}>仅看已选</Button></div> : null}
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : null}
    {!data && !error ? <Skeleton active paragraph={{ rows: 5 }} /> : null}
    {data && !papers.length ? <Empty description={<>文献库为空，<Link to="/">先检索并加入论文</Link></>} /> : null}
    <ul className="scope-list">{visible.map(paper => {
      const id = paper.id;
      const checked = picked.some(item => item.id === id);
      return <li key={id} className={checked ? "is-selected" : undefined}>
        <Checkbox disabled={!checked && picked.length >= MAX_SELECTION} checked={checked} onChange={() => selection.toggle({ id, title: paper.title })}>
          <span className="scope-title">{paper.title}</span>
          <span className="scope-meta">{listed.has(id) ? paper.year ?? "年份未知" : "从文献库选入"}{listed.has(id) ? <AnalysisBadge state={states[id]} /> : null}</span>
        </Checkbox>
      </li>;
    })}</ul>
    {data && scopeItems.length > 0 && !visible.length ? <p className="scope-empty">{onlyPicked && !picked.length ? "还没有选择论文。" : "没有匹配的论文。"}<Button type="link" size="small" onClick={() => { setFilter(""); setOnlyPicked(false); }}>显示全部</Button></p> : null}
    {picked.length >= MAX_SELECTION ? <p className="small-print">已达到 {MAX_SELECTION} 篇上限，可取消部分论文后继续选择。</p> : null}
    {data && data.total > papers.length ? <p className="muted small-print">仅列出最近导入的 {papers.length} 篇（共 {data.total} 篇）。</p> : null}
    </div>
  </aside>;

  return <main className="page page-wide studio-page">
    <header className="page-head">
      <div>
        <h1 className="page-title">研究台</h1>
        <p className="page-sub">基于文献库内容提问、比较已分析的论文，或发起一次调研任务。</p>
      </div>
    </header>
    <nav className="view-tabs studio-tabs" aria-label="研究台功能">
      {TABS.map(item => <Link key={item.key} to={`/studio?${new URLSearchParams({ ...Object.fromEntries(params), tab: item.key })}`} replace aria-current={tab === item.key ? "page" : undefined}>{item.label}</Link>)}
    </nav>
    <div className={`studio-layout${tab === "research" ? " is-single" : ""}`}>
      {tab === "research" ? null : scopePanel}
      <section className="studio-main">
        {tab === "ask" ? <AskView picked={picked} libraryTotal={data?.total ?? null} titles={titles} scope={scope} onScope={value => set({ scope: value })} from={here} />
          : tab === "compare" ? <CompareView picked={picked} states={states} from={here} />
          : <ResearchView from={here} />}
      </section>
    </div>
  </main>;
}

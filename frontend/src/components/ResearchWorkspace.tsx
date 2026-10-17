import { useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Empty, Pagination, Spin } from "antd";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { fetchLibrary } from "../api";
import type { LibraryResponse } from "../types";
import { graphLocation, libraryLocation, paperLocation } from "../workspace";

export function ResearchWorkspace({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const library = libraryLocation(params);
  const page = Math.max(1, Number(new URL(library, window.location.origin).searchParams.get("page")) || 1);
  const selected = params.get("selected") || "";
  const id = params.get("id") || (selected.startsWith("paper:") ? selected.slice(6) : "") || params.get("focus") || "";
  const graph = location.pathname === "/graph";
  const [data, setData] = useState<LibraryResponse | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const refresh = () => setRetry(value => value + 1);
    window.addEventListener("library-changed", refresh);
    return () => window.removeEventListener("library-changed", refresh);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError("");
    fetchLibrary(page, controller.signal).then(result => {
      if (!controller.signal.aborted) setData(result);
    }).catch(err => { if (!controller.signal.aborted) setError(err.message || "文献列表加载失败"); });
    return () => controller.abort();
  }, [page, retry]);
  return <div className="research-workspace">
    <aside className="research-library" aria-label="研读文献列表">
      <div className="research-library-heading"><div><span className="page-kicker">我的文献</span><h2>研读工作空间</h2></div><Link to={library}>全部文献 ↗</Link></div>
      <p className="meta">切换论文，继续阅读与探索。</p>
      <nav className="collection-tools" aria-label="文献集合视图"><Link to={library}>文献列表</Link><Link className={graph ? "active" : ""} to={graphLocation(library, id)}>集合图谱</Link></nav>
      {error ? <Alert type="error" message={error} action={<Button size="small" onClick={() => setRetry(v => v + 1)}>重试</Button>} /> : !data ? <Spin aria-label="正在加载文献列表" /> : <>
        <p className="meta">共 {data.total} 篇 · 第 {page} 页</p>
        {!data.papers.length ? <Empty description="还没有收藏文献"><Link to="/">去检索文献</Link></Empty> : null}
        <div className="research-paper-list">{data.papers.map(paper => {
          const paperId = paper.local_id || paper.id;
          const selected = paperId === id || paper.id === id;
          return <Link key={paperId} aria-current={selected ? "page" : undefined} className={selected ? "active" : ""} to={paperLocation(paperId, library)}><small>{paper.year || "年份未知"}{selected ? " · 当前论文" : ""}</small><strong>{paper.title}</strong><span>{paper.authors.slice(0, 2).join(" · ")}</span></Link>;
        })}</div>
        {data.total > 20 ? <Pagination simple current={page} pageSize={20} total={data.total} showSizeChanger={false} onChange={next => {
          const nextLibrary = new URL(library, window.location.origin);
          nextLibrary.searchParams.set("page", String(next));
          navigate(`${nextLibrary.pathname}${nextLibrary.search}`);
        }} /> : null}
      </>}
    </aside>
    <div className="research-main"><div className="research-context"><Link to={library}>我的文献</Link><span> / </span><span>{graph ? "集合关系探索" : "论文研读"}</span><Link to={library} className="research-return">返回文献列表</Link></div>{children}</div>
  </div>;
}

export function PaperViews({ id, from, title }: { id: string; from: string; title?: string }) {
  const location = useLocation();
  return <section className="paper-view-header" aria-label="当前论文研读视图">
    <span className="page-kicker">当前论文</span>
    {title ? <h2>{title}</h2> : <p className="meta">围绕这篇论文继续研读与发现。</p>}
    <nav className="paper-view-tabs" aria-label="论文视图">
      <Link aria-current={location.pathname === "/paper" ? "page" : undefined} to={paperLocation(id, from)}>阅读与分析</Link>
      <Link aria-current={location.pathname === "/recommend" ? "page" : undefined} to={`/recommend?${new URLSearchParams({ id, from })}`}>相关推荐</Link>
      <Link to={graphLocation(location.pathname + location.search, id)}>在集合图谱中定位 ↗</Link>
    </nav>
  </section>;
}

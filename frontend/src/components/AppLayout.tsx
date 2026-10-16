import { useEffect, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

const navigation = [
  { label: "文献检索", href: "/", key: "search" },
  { label: "论文库", href: "/library", key: "library" },
  { label: "论文分析", href: "/library?view=analysis", key: "analysis" },
  { label: "相似推荐", href: "/recommend", key: "recommend" },
  { label: "知识图谱", href: "/graph", key: "graph" },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const active = location.pathname === "/" ? "search" : location.pathname === "/paper" ? "analysis"
    : location.pathname === "/library" ? new URLSearchParams(location.search).get("view") === "analysis" ? "analysis" : "library"
      : location.pathname.slice(1);
  useEffect(() => { window.scrollTo({ top: 0, behavior: "instant" }); }, [location.pathname, location.search]);
  return <div className="app-layout">
    <a href="#workspace-content" className="skip-link">跳到主要内容</a>
    <header className="app-header"><div className="app-header-inner">
      <Link to="/" className="app-brand" aria-label="文献研读，返回首页">
        <span className="app-logo" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M7 7h8v19H7zM18 7h7v19h-7z" fill="none" stroke="currentColor" strokeWidth="2" /><path d="M10 11h2M21 11h1M10 21h2M21 21h1" stroke="currentColor" strokeWidth="2" /></svg></span>
        <span>文献研读<small>检索与分析工作台</small></span>
      </Link>
      <nav aria-label="主要导航">{navigation.map(item => <Link key={item.key} to={item.href} aria-current={active === item.key ? "page" : undefined} className={active === item.key ? "active" : ""}>{item.label}</Link>)}</nav>
      <span className="app-header-note">从检索到知识探索</span>
    </div></header>
    <div id="workspace-content" tabIndex={-1} className="workspace-content">{children}</div>
    <footer className="app-footer"><span>文献检索与分析平台</span><span>检索 · 阅读 · 分析 · 探索</span></footer>
  </div>;
}

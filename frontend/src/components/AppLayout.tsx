import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ResearchWorkspace } from "./ResearchWorkspace";

const scrollPositions = new Map<string, number>();

const navigation = [
  { label: "文献检索", href: "/", key: "search" },
  { label: "我的文献", href: "/library", key: "library" },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const previousPath = useRef("");
  const leaving = useRef(false);
  const active = location.pathname === "/" ? "search" : "library";
  const inWorkspace = ["/paper", "/recommend", "/graph"].includes(location.pathname);
  useEffect(() => {
    const previous = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    return () => { window.history.scrollRestoration = previous; };
  }, []);
  useLayoutEffect(() => {
    leaving.current = false;
    const key = location.pathname + location.search;
    const sameGraph = previousPath.current === "/graph" && location.pathname === "/graph";
    previousPath.current = location.pathname;
    if (sameGraph) {
      const save = () => { if (!leaving.current) scrollPositions.set(key, window.scrollY); };
      window.addEventListener("scroll", save, { passive: true });
      return () => { window.removeEventListener("scroll", save); };
    }
    const target = scrollPositions.get(key) || 0;
    let restoring = true;
    const restore = () => { if (restoring && !location.hash) window.scrollTo({ top: target, behavior: "instant" }); };
    restore();
    const observer = new ResizeObserver(restore);
    observer.observe(document.body);
    const stop = () => { restoring = false; observer.disconnect(); };
    const timer = window.setTimeout(stop, 4000);
    const save = () => { if (!restoring && !leaving.current) scrollPositions.set(key, window.scrollY); };
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchstart", stop, { passive: true });
    window.addEventListener("keydown", stop);
    window.addEventListener("scroll", save, { passive: true });
    return () => {
      window.clearTimeout(timer); observer.disconnect();
      window.removeEventListener("wheel", stop); window.removeEventListener("touchstart", stop);
      window.removeEventListener("keydown", stop); window.removeEventListener("scroll", save);
    };
  }, [location.pathname, location.search, location.hash]);
  return <div className="app-layout" onClickCapture={event => {
    const anchor = event.target instanceof Element ? event.target.closest("a") : null;
    if (!anchor || anchor.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const next = new URL(anchor.href, window.location.origin);
    const key = location.pathname + location.search;
    if (next.origin === window.location.origin && next.pathname + next.search !== key) {
      scrollPositions.set(key, window.scrollY);
      leaving.current = true;
    }
  }}>
    <a href="#workspace-content" className="skip-link">跳到主要内容</a>
    <header className="app-header"><div className="app-header-inner">
      <Link to="/" className="app-brand" aria-label="文献研读，返回首页">
        <span className="app-logo" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M7 7h8v19H7zM18 7h7v19h-7z" fill="none" stroke="currentColor" strokeWidth="2" /><path d="M10 11h2M21 11h1M10 21h2M21 21h1" stroke="currentColor" strokeWidth="2" /></svg></span>
        <span>文献研读<small>检索与分析工作台</small></span>
      </Link>
      <nav aria-label="主要导航">{navigation.map(item => <Link key={item.key} to={item.href} aria-current={active === item.key ? "page" : undefined} className={active === item.key ? "active" : ""}>{item.label}</Link>)}</nav>
      <span className="app-header-note">找到文献，接续研读</span>
    </div></header>
    <div id="workspace-content" tabIndex={-1} className="workspace-content">{inWorkspace ? <ResearchWorkspace>{children}</ResearchWorkspace> : children}</div>
    <footer className="app-footer"><span>文献检索与分析平台</span><span>检索 · 阅读 · 分析 · 探索</span></footer>
  </div>;
}

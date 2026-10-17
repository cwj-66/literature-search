import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { sectionOf } from "../workspace";

const scrollPositions = new Map<string, number>();

const navigation = [
  { label: "工作台", href: "/", key: "home" },
  { label: "检索", href: "/search", key: "search" },
  { label: "文献库", href: "/library", key: "library" },
  { label: "研究台", href: "/studio", key: "studio" },
  { label: "图谱", href: "/graph", key: "graph" },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const previousPath = useRef("");
  const leaving = useRef(false);
  const active = sectionOf(location.pathname, new URLSearchParams(location.search));
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
  return <div className="app" onClickCapture={event => {
    const anchor = event.target instanceof Element ? event.target.closest("a") : null;
    if (!anchor || anchor.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const next = new URL(anchor.href, window.location.origin);
    const key = location.pathname + location.search;
    if (next.origin === window.location.origin && next.pathname + next.search !== key) {
      scrollPositions.set(key, window.scrollY);
      leaving.current = true;
    }
  }}>
    <a href="#main" className="skip-link">跳到主要内容</a>
    <header className="app-header">
      <div className="app-header-inner">
        <Link to="/" className="app-brand" aria-label="文献研读，返回工作台">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5C4 4.7 4.7 4 5.5 4H11v16H5.5c-.8 0-1.5-.7-1.5-1.5v-13ZM13 4h5.5c.8 0 1.5.7 1.5 1.5v13c0 .8-.7 1.5-1.5 1.5H13V4Z" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
          <span>文献研读</span>
        </Link>
        <nav aria-label="主要导航" className="app-nav">
          {navigation.map(item => <Link key={item.key} to={item.href} aria-current={active === item.key ? "page" : undefined}>{item.label}</Link>)}
        </nav>
      </div>
    </header>
    <div id="main" tabIndex={-1} className="app-main">{children}</div>
  </div>;
}

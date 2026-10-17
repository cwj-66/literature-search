import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { AppstoreOutlined, SearchOutlined, SettingOutlined } from "@ant-design/icons";
import { Link, useLocation } from "react-router-dom";
import { projects, useProjects } from "../projects";
import { sectionOf, workspaceContext } from "../workspace";
import { WorkspaceFrame } from "./WorkspaceFrame";

const scrollPositions = new Map<string, number>();

const navigation = [
  { label: "研究工作区", href: "/", key: "home", icon: <AppstoreOutlined aria-hidden /> },
  { label: "论文搜索", href: "/search", key: "search", icon: <SearchOutlined aria-hidden /> },
  { label: "设置", href: "/settings", key: "settings", icon: <SettingOutlined aria-hidden /> },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
  const previousPath = useRef("");
  const leaving = useRef(false);
  const active = sectionOf(location.pathname, new URLSearchParams(location.search));
  const ctx = workspaceContext(location.pathname, location.search);
  const { projects: list } = useProjects();
  const project = ctx ? list.find(item => item.id === ctx.id) : undefined;
  const projectId = project?.id;
  useEffect(() => {
    if (!projectId) return;
    projects.select(projectId);
    projects.touch(projectId);
  }, [projectId]);
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
  const inside = Boolean(project && ctx);
  useLayoutEffect(() => {
    if (!inside) return;
    document.querySelector(".ws-main")?.scrollTo({ top: 0 });
  }, [inside, location.pathname, location.search]);
  return <div className={inside ? "app ws-app" : "app"} onClickCapture={event => {
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
    {inside && project && ctx ? <WorkspaceFrame project={project} section={ctx.section}>{children}</WorkspaceFrame> : <>
    <aside className="shell-side">
      <Link to="/" className="shell-brand" aria-label="文献研究助手，返回工作区">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4.5h10.2c.4 0 .8.2 1.1.5l2.2 2.2c.3.3.5.7.5 1.1V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V6A1.5 1.5 0 0 1 5 4.5Z" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M8 4.5V8h8" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
        <span>文献研究助手</span>
      </Link>
      <nav className="shell-nav" aria-label="主要导航">
        {navigation.map(item => <Link key={item.key} to={item.href} aria-current={active === item.key ? "page" : undefined}>{item.icon}{item.label}</Link>)}
      </nav>
    </aside>
    <div id="main" tabIndex={-1} className="app-main">{children}</div>
    </>}
  </div>;
}

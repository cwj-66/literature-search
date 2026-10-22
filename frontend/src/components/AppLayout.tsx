import { useEffect, type ReactNode } from "react";
import { AppstoreOutlined, BookOutlined, MenuFoldOutlined, MenuUnfoldOutlined, SearchOutlined, SettingOutlined } from "@ant-design/icons";
import { Link, useLocation } from "react-router-dom";
import { projects, useProjects } from "../projects";
import { sectionOf, workspaceContext } from "../workspace";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { isDemo } from "../demo";
import { useCollapsed } from "../hooks";
import { useRouteScroll } from "../routeScroll";

const navigation = [
  { label: "研究工作区", href: "/", key: "home", icon: <AppstoreOutlined aria-hidden /> },
  { label: "论文搜索", href: "/search", key: "search", icon: <SearchOutlined aria-hidden /> },
  { label: "文献库", href: "/library", key: "library", icon: <BookOutlined aria-hidden /> },
  { label: "设置", href: "/settings", key: "settings", icon: <SettingOutlined aria-hidden /> },
];

export function AppLayout({ children }: { children: ReactNode }) {
  const location = useLocation();
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
  const inside = Boolean(project && ctx);
  const captureScroll = useRouteScroll(location.pathname, location.search, location.hash, inside);
  const [shellCollapsed, toggleShell] = useCollapsed("sg-shell-collapsed");
  return <div className={inside ? "app ws-app" : "app scholar-desktop"} onClickCapture={event => {
    const anchor = event.target instanceof Element ? event.target.closest("a") : null;
    if (!anchor || anchor.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const next = new URL(anchor.href, window.location.origin);
    if (next.origin === window.location.origin) captureScroll();
  }}>
    {isDemo && <div className="demo-banner"><span>面试体验 · 已配置演示模型 · 工作区与对话仅属于当前访客，闲置 7 天后清理</span><a href="/projects/paper-search/">返回作品集 ↗</a></div>}
    <a href="#main" className="skip-link">跳到主要内容</a>
    {inside && project && ctx
      ? <div className="route-view is-shell" key={location.pathname}><WorkspaceFrame project={project} section={ctx.section}>{children}</WorkspaceFrame></div>
      : <>
        <aside className={`shell-side${shellCollapsed ? " is-collapsed" : ""}`}>
          <button type="button" className="side-collapse" aria-expanded={!shellCollapsed} aria-label={shellCollapsed ? "展开导航" : "收起导航"} title={shellCollapsed ? "展开导航" : "收起导航"} onClick={toggleShell}>{shellCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</button>
          <Link to="/" className="shell-brand" aria-label="文献研究助手，返回工作区">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4.5h10.2c.4 0 .8.2 1.1.5l2.2 2.2c.3.3.5.7.5 1.1V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V6A1.5 1.5 0 0 1 5 4.5Z" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M8 4.5V8h8" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
            <span className="shell-label">文献研究助手</span>
          </Link>
          <nav className="shell-nav" aria-label="主要导航">
            {navigation.map(item => <Link key={item.key} to={item.href} title={item.label} aria-current={active === item.key ? "page" : undefined}>{item.icon}<span className="shell-label">{item.label}</span></Link>)}
          </nav>
        </aside>
        <div id="main" tabIndex={-1} className="app-main"><div className="route-view" key={location.pathname}>{children}</div></div>
      </>}
  </div>;
}

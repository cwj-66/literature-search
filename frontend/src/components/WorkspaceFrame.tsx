import type { ReactNode } from "react";
import { Dropdown } from "antd";
import { Link } from "react-router-dom";
import { useProjects, type Project } from "../projects";
import type { WorkspaceSection } from "../workspace";

const NAV: { section: WorkspaceSection; label: string; href: (id: string) => string; icon: ReactNode }[] = [
  { section: "studio", label: "研究台", href: id => `/w/${id}/studio`, icon: <circle cx="12" cy="12" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.6" /> },
  { section: "assistant", label: "智能调研", href: id => `/w/${id}/research`, icon: <path d="m4 17 5-5 4 3 7-10M15 5h5v5" fill="none" stroke="currentColor" strokeWidth="1.6" /> },
  { section: "library", label: "论文库", href: id => `/w/${id}/library`, icon: <><path d="M7 3.8h6.4L18 8.2V20H7z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M13.2 3.8V8.2H18" fill="none" stroke="currentColor" strokeWidth="1.6" /></> },
  { section: "compare", label: "论文对比", href: id => `/w/${id}/compare`, icon: <><rect x="4.5" y="7" width="10" height="12" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M9.5 5h9a1.2 1.2 0 0 1 1.2 1.2V16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></> },
  { section: "graph", label: "知识图谱", href: id => `/w/${id}/graph`, icon: <><circle cx="7" cy="14" r="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><circle cx="16" cy="7" r="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><circle cx="17" cy="17" r="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M8.8 12.8 14.2 8.4M8.7 15.2l6.4 1.2" fill="none" stroke="currentColor" strokeWidth="1.6" /></> },
];

function Glyph({ children }: { children: ReactNode }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true">{children}</svg>;
}

function sectionHref(section: WorkspaceSection, id: string) {
  if (section === "studio") return `/w/${id}/studio`;
  if (section === "assistant") return `/w/${id}/research`;
  if (section === "library") return `/w/${id}/library`;
  if (section === "compare") return `/w/${id}/compare`;
  if (section === "graph") return `/w/${id}/graph`;
  return `/w/${id}`;
}

export function WorkspaceFrame({ project, section, children }: { project: Project; section: WorkspaceSection; children: ReactNode }) {
  const { projects: list } = useProjects();
  const count = Object.keys(project.papers).length;
  if (section === "reading") return <div className="ws-frame"><div id="main" tabIndex={-1} className="ws-main">{children}</div></div>;
  return <div className="ws-frame">
    <header className="ws-top">
      <div className="ws-heading">
        <Link to="/" className="ws-mark" aria-label="返回全部工作区" title="全部工作区">
          <Glyph><path d="M7 5.2h6.2L17.5 9.2V18a1.2 1.2 0 0 1-1.2 1.2H7A1.2 1.2 0 0 1 5.8 18V6.4A1.2 1.2 0 0 1 7 5.2Z" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M13 5.2V9.2h4.4" fill="none" stroke="currentColor" strokeWidth="1.6" /></Glyph>
        </Link>
        <Dropdown trigger={["click"]} placement="bottomLeft" popupRender={() => <div className="ws-switch-menu">
          {list.map(item => <Link key={item.id} to={sectionHref(section, item.id)} aria-current={item.id === project.id ? "true" : undefined}>{item.name}</Link>)}
          <Link to="/">全部工作区</Link>
        </div>}>
          <button type="button" className="ws-switch" aria-label={`当前工作区：${project.name}`}>
            <span className="ws-name">{project.name}</span>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
        </Dropdown>
      </div>
      <span className="ws-count"><Glyph><path d="M7 4.5h7.2L18.5 8.6V19a1.2 1.2 0 0 1-1.2 1.2H7A1.2 1.2 0 0 1 5.8 19V5.7A1.2 1.2 0 0 1 7 4.5Z" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M14 4.6V8.7h4.3" fill="none" stroke="currentColor" strokeWidth="1.6" /></Glyph>{count} 篇论文</span>
    </header>
    <div className="ws-body">
      <nav className="ws-nav" aria-label="研究资料">
        <p className="ws-nav-label">研究资料</p>
        {NAV.map(item => <Link key={item.section} to={item.href(project.id)} aria-current={section === item.section ? "page" : undefined}><Glyph>{item.icon}</Glyph>{item.label}</Link>)}
      </nav>
      <div id="main" tabIndex={-1} className="ws-main">{children}</div>
    </div>
  </div>;
}

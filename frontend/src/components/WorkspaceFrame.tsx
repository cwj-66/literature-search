import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Project } from "../projects";
import type { WorkspaceSection } from "../workspace";

const NAV: { section: WorkspaceSection; label: string; href: (id: string) => string; icon: ReactNode }[] = [
  { section: "assistant", label: "AI 研究助手", href: id => `/w/${id}`, icon: <circle cx="12" cy="12" r="7.25" fill="none" stroke="currentColor" strokeWidth="1.6" /> },
  { section: "library", label: "论文库", href: id => `/w/${id}/library`, icon: <><path d="M7 3.8h6.4L18 8.2V20H7z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M13.2 3.8V8.2H18" fill="none" stroke="currentColor" strokeWidth="1.6" /></> },
  { section: "compare", label: "论文对比", href: id => `/w/${id}/compare`, icon: <><rect x="4.5" y="7" width="10" height="12" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M9.5 5h9a1.2 1.2 0 0 1 1.2 1.2V16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></> },
  { section: "graph", label: "知识图谱", href: id => `/w/${id}/graph`, icon: <><circle cx="7" cy="14" r="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><circle cx="16" cy="7" r="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><circle cx="17" cy="17" r="2" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M8.8 12.8 14.2 8.4M8.7 15.2l6.4 1.2" fill="none" stroke="currentColor" strokeWidth="1.6" /></> },
];

function Glyph({ children }: { children: ReactNode }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true">{children}</svg>;
}

export function WorkspaceFrame({ project, section, children }: { project: Project; section: WorkspaceSection; children: ReactNode }) {
  const count = Object.keys(project.papers).length;
  if (section === "reading") return <div className="ws-frame"><div id="main" tabIndex={-1} className="ws-main">{children}</div></div>;
  return <div className="ws-frame">
    <header className="ws-top">
      <div className="ws-heading">
        <Link to="/" className="ws-back" aria-label="返回研究工作区" title="返回全部工作区">
          <Glyph><path d="M3.8 8.2V18a1.4 1.4 0 0 0 1.4 1.4h13.6a1.4 1.4 0 0 0 1.4-1.4V9.4a1.4 1.4 0 0 0-1.4-1.4h-6.4L10.2 5.6H5.2A1.4 1.4 0 0 0 3.8 7Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></Glyph>
        </Link>
        <p className="ws-name">{project.name}</p>
      </div>
      <span className="ws-count">{count} 篇论文</span>
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

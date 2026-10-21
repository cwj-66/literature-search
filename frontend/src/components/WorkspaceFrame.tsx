import { useState, type ReactNode } from "react";
import { Dropdown } from "antd";
import { ArrowLeftOutlined, ApartmentOutlined, BookOutlined, EditOutlined, FileSearchOutlined, HomeOutlined, MenuFoldOutlined, MenuUnfoldOutlined, MessageOutlined, ReadOutlined, SearchOutlined, SettingOutlined, ShareAltOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { WorkspaceRenameDialog } from "./WorkspaceRenameDialog";
import { useCollapsed } from "../hooks";
import { useProjects, type Project } from "../projects";
import type { WorkspaceSection } from "../workspace";
const NAV = [
  { section: "home", label: "首页", path: "home", icon: <HomeOutlined /> },
  { section: "assistant", label: "检索助手", path: "research", icon: <FileSearchOutlined /> },
  { section: "search", label: "论文搜索", path: "search", icon: <SearchOutlined /> },
  { section: "library", label: "文献管理", path: "library", icon: <BookOutlined /> },
  { section: "graph", label: "知识图谱", path: "graph", icon: <ApartmentOutlined /> },
  { section: "studio", label: "智能问答", path: "studio", icon: <MessageOutlined /> },
  { section: "compare", label: "文献推荐", path: "compare", icon: <ShareAltOutlined /> },
];
export function WorkspaceFrame({ project, section, children }: { project: Project; section: WorkspaceSection; children: ReactNode }) {
  const { projects: list } = useProjects();
  const [renaming, setRenaming] = useState(false);
  const [collapsed, toggleCollapsed] = useCollapsed("sg-nav-collapsed");
  const path = NAV.find(item => item.section === section)?.path ?? "home";
  return <div data-section={section} className={`ws-frame scholar-frame${section === "reading" ? " scholar-reading" : ""}`}>
    <aside className={`scholar-sidebar${collapsed ? " is-collapsed" : ""}`}>
      <button type="button" className="side-collapse" aria-expanded={!collapsed} aria-label={collapsed ? "展开导航" : "收起导航"} title={collapsed ? "展开导航" : "收起导航"} onClick={toggleCollapsed}>{collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</button>
      <Link to={`/w/${project.id}/home`} className="scholar-brand" title="ScholarGraph"><span className="scholar-symbol"><ShareAltOutlined /></span><span><b>ScholarGraph</b><small>学术知识图谱平台</small></span></Link>
      <nav className="ws-nav" aria-label="工作台导航">{NAV.map(item => <Link key={item.section} to={`/w/${project.id}/${item.path}`} title={item.label} aria-current={section === item.section ? "page" : undefined}><span aria-hidden="true">{item.icon}</span><span>{item.label}</span></Link>)}</nav>
      <Link className="scholar-side-card" to={`/w/${project.id}/research`}><span className="scholar-book"><ReadOutlined /></span><strong>让科研探索<br />更高效</strong><p>基于大语言模型的<br />学术知识发现平台</p><svg viewBox="0 0 180 48" aria-hidden="true"><path d="M0 48Q30 0 65 30T130 20T180 8V48Z" fill="#dbe6ff"/><path d="M0 48Q40 22 70 40T140 25T180 20V48Z" fill="#bfcfff" opacity=".7"/></svg></Link>
      <div className="scholar-side-footer"><Link to="/">返回桌面</Link><Link to="/settings" aria-label="设置"><SettingOutlined /></Link></div>
    </aside>
    <div className="scholar-content"><header className="ws-top">
      <div className="ws-heading"><Link to="/" className="scholar-back-desktop"><ArrowLeftOutlined aria-hidden />返回桌面</Link><Dropdown trigger={["click"]} menu={{ items: [...list.map(item => ({ key: item.id, label: <Link to={`/w/${item.id}/${path}`}>{item.name}</Link> })), { key: "all", label: <Link to="/">全部工作区</Link> }] }}><button className="ws-switch" aria-label={`当前工作区：${project.name}`}><span className="ws-name">{project.name}</span><span>⌄</span></button></Dropdown><button className="ws-rename" aria-label="重命名当前工作区" onClick={() => setRenaming(true)}><EditOutlined /></button></div>
      <div id="research-header-slot" /><div className="scholar-user"><span className="ws-count">{Object.keys(project.papers).length} 篇文献</span><span className="scholar-avatar">研</span><span>Hi, Researcher</span></div>
    </header><div id="main" tabIndex={-1} className="ws-main">{children}</div></div>
    <WorkspaceRenameDialog project={renaming ? project : null} onClose={() => setRenaming(false)} />
  </div>;
}

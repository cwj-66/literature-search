import { useEffect } from "react";
import { pageTitle } from "../../presentation";
import { ApartmentOutlined, BookOutlined, FileSearchOutlined, SearchOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

export default function WorkspaceHome() {
  const { id, project } = useWorkspaceProject();
  useEffect(() => { document.title = pageTitle(`${project?.name ?? "工作区"} · 首页`); }, [project?.name]);
  if (!project) return <MissingWorkspace />;
  const papers = Object.entries(project.papers);
  return <main className="ws-page scholar-home">
    <span className="scholar-eyebrow">当前工作区</span><h1>每一次探索，都有新的连接。</h1><p>在「{project.name}」中发现论文、梳理知识，与研究持续对话。</p>
    <div className="scholar-quick-links">{[
      { path: "research", icon: <FileSearchOutlined />, title: "检索助手", text: "描述研究需求，让助手帮你找论文" },
      { path: "search", icon: <SearchOutlined />, title: "论文搜索", text: "按关键词、作者和年份检索论文" },
      { path: "library", icon: <BookOutlined />, title: "文献库", text: `已收藏 ${papers.length} 篇论文` },
      { path: "graph", icon: <ApartmentOutlined />, title: "知识图谱", text: "探索论文、关键词与作者的连接" },
    ].map(item => <Link key={item.path} to={`/w/${id}/${item.path}`}><i>{item.icon}</i><h2>{item.title}</h2><p>{item.text}</p><span>开始探索 →</span></Link>)}</div>
    <div className="scholar-section-heading"><h2>最近加入的论文</h2><Link to={`/w/${id}/library`}>查看全部 →</Link></div>
    <div className="scholar-recent">{papers.slice().sort((a,b) => b[1].addedAt.localeCompare(a[1].addedAt)).slice(0,5).map(([key,{paper}]) => <Link key={key} to={`/w/${id}/p/${encodeURIComponent(key)}`}><BookOutlined /><span><b>{paper.title}</b><small>{paper.year ?? "年份未知"} · {paper.authors.slice(0,3).join(", ") || "作者未知"}</small></span><span>↗</span></Link>)}{!papers.length ? <p>还没有论文，从检索助手或论文搜索开始。</p> : null}</div>
  </main>;
}

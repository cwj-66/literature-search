import { useEffect, useState } from "react";
import { Button, Empty, Input, Popconfirm } from "antd";
import { DeleteOutlined, SearchOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { PaperRow } from "../../components/PaperRow";
import { STATUS_LABEL, projects } from "../../projects";
import { workspacePaperPath } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

export default function PapersPage() {
  const { id, project } = useWorkspaceProject();
  const [filter, setFilter] = useState("");
  const here = `/w/${id}/library`;
  useEffect(() => { document.title = project ? `${project.name} - 论文库` : "工作区"; }, [project]);
  if (!project) return <MissingWorkspace />;
  const entries = Object.entries(project.papers).sort((a, b) => (b[1].openedAt ?? b[1].addedAt).localeCompare(a[1].openedAt ?? a[1].addedAt));
  const needle = filter.trim().toLocaleLowerCase();
  const shown = entries.filter(([, item]) => !needle || [item.paper.title, ...item.paper.authors, item.paper.year, item.paper.venue].filter(Boolean).join(" ").toLocaleLowerCase().includes(needle));
  return <main className="ws-page ws-library">
    <header className="ws-page-head">
      <div>
        <h1>论文库</h1>
        <p>这个工作区里的论文。打开后可以精读，移出后文献库里的记录仍会保留。</p>
      </div>
      <Link to="/search">去搜索论文</Link>
    </header>
    {!entries.length ? <Empty description={<>还没有论文。可以在 <Link to={`/w/${id}`}>AI 研究助手</Link> 里描述题目，或 <Link to="/search">去搜索</Link> 后收藏。</>} /> : <>
      <div className="ws-tools">
        <Input allowClear prefix={<SearchOutlined aria-hidden />} value={filter} onChange={event => setFilter(event.target.value)} aria-label="筛选工作区论文" placeholder="筛选标题、作者或年份" />
        <span className="small-print">{needle ? `匹配 ${shown.length} / ${entries.length} 篇` : `共 ${entries.length} 篇`}</span>
      </div>
      {!shown.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的论文"><Button onClick={() => setFilter("")}>清除筛选</Button></Empty> : <div className="paper-list">
        {shown.map(([key, item]) => <PaperRow key={key} paper={item.paper} from={here} href={workspacePaperPath(id, key)} onOpen={() => { projects.select(id); projects.open(item.paper); }} side={<>
          <span className={`status status-${item.status === "read" ? "success" : item.status === "reading" ? "accent" : "neutral"}`}>{STATUS_LABEL[item.status]}</span>
          <Popconfirm title="从当前工作区移出这篇论文？" description="文献库中的记录和分析会保留。" okText="移出" cancelText="取消" onConfirm={() => { projects.select(id); projects.removePaper(key); }}>
            <Button size="small" type="text" icon={<DeleteOutlined />} aria-label={`移出：${item.paper.title}`}>移出</Button>
          </Popconfirm>
        </>} />)}
      </div>}
    </>}
  </main>;
}

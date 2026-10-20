import { useState } from "react";
import { Checkbox, Input, Drawer, Button } from "antd";
import { BookOutlined, PlusOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { useProjects } from "../projects";
import { CollectButton } from "./CollectButton";
import { paperLocation } from "../workspace";

export function ResearchSources({ selected, onChange, disabled, currentId }: {
  selected: string[]; onChange: (ids: string[]) => void; disabled: boolean; currentId: string;
}) {
  const { projects } = useProjects();
  const [active, setActive] = useState(currentId);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const project = projects.find(p => p.id === active) ?? projects[0];
  const picked = new Set(selected);
  const count = projects.filter(p => Object.keys(p.papers).some(id => picked.has(id))).length;
  const toggle = (ids: string[], checked: boolean) => onChange(checked
    ? [...new Set([...selected, ...ids])].slice(0, 500) : selected.filter(id => !ids.includes(id)));
  const content = <div className="research-picker">
    <div className="research-picker-title"><strong>本对话可访问的资料</strong><span>{selected.length} 篇已选</span></div>
    <p>助手按需检索和阅读所选资料。可跨工作区选择。</p>
    <div className="research-picker-columns">
      <div className="research-picker-workspaces" aria-label="选择资料工作区">
        {projects.map(item => {
          const ids = Object.keys(item.papers), n = ids.filter(id => picked.has(id)).length;
          return <div key={item.id} className={item.id === project?.id ? "is-active" : ""}>
            <Checkbox aria-label={`全选工作区 ${item.name}`} disabled={disabled || !ids.length} checked={!!ids.length && n === ids.length} indeterminate={n > 0 && n < ids.length} onChange={e => toggle(ids, e.target.checked)} />
            <button type="button" onClick={() => { setActive(item.id); setQuery(""); }} aria-pressed={item.id === project?.id}><strong>{item.name}</strong><small>{n} / {ids.length} 篇</small></button>
          </div>;
        })}
      </div>
      <div className="research-picker-papers">
        <strong>{project?.name ?? "还没有工作区"}</strong>
        <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="筛选论文标题" aria-label="筛选研究资料" allowClear />
        <div className="research-picker-list">
          {Object.entries(project?.papers ?? {}).filter(([, item]) => item.paper.title.toLowerCase().includes(query.toLowerCase())).map(([id, item]) =>
            <Checkbox key={id} disabled={disabled} checked={picked.has(id)} onChange={e => toggle([id], e.target.checked)}>{item.paper.title}<small>{item.paper.year ?? "年份未知"}</small></Checkbox>)}
          {!Object.keys(project?.papers ?? {}).length && <p>这个工作区还没有论文，可先让助手搜索或上传 PDF。</p>}
        </div>
      </div>
    </div>
    <div className="research-picker-footer"><Button disabled={disabled || !selected.length} onClick={() => onChange([])}>清空选择</Button><span>最多 500 篇；全选仅包含当前已有论文</span><Button type="primary" onClick={() => setOpen(false)}>完成</Button></div>
  </div>;
  const papers = [...new Map(projects.flatMap(p => Object.entries(p.papers))).entries()].filter(([id]) => picked.has(id));
  const panel = <div className="research-source-panel-inner">
    <div className="research-source-panel-head">
      <div className="research-source-icon"><BookOutlined /></div>
      <div className="research-source-panel-title">
        <strong>本对话的研究资料</strong>
        <span>{selected.length} 篇{count ? ` · 来自 ${count} 个工作区` : ""}</span>
      </div>
      <span className="research-source-badge">{selected.length}</span>
    </div>
    <div className="research-source-panel-tools">
      <Button size="small" type="primary" ghost aria-label={selected.length ? "管理研究资料" : "添加研究资料"} icon={<PlusOutlined />} disabled={disabled} onClick={() => setOpen(true)}>{selected.length ? "管理研究资料" : "添加研究资料"}</Button>
      {papers.length ? <button type="button" className="research-source-collapse" aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>{collapsed ? "展开列表" : "收起列表"}</button> : null}
    </div>
    {!collapsed ? <ul className="research-source-list">
      {papers.map(([key, item]) => <li key={key} className="research-source-row">
        <Link to={paperLocation(key, `/w/${currentId}/research`)} title={item.paper.title} className="research-source-row-title">{item.paper.title}</Link>
        <CollectButton iconOnly paper={item.paper} />
      </li>)}
      {!papers.length ? <li className="small-print">尚未选择本地资料，可以直接搜索新论文。</li> : null}
    </ul> : null}
  </div>;
  return <>
    <aside className="research-source-panel" aria-label="本对话研究资料">{panel}</aside>
    <button type="button" className="research-source-fab" aria-label={`本对话的研究资料 ${selected.length} 篇`} onClick={() => setPanelOpen(true)}>
      <BookOutlined /><span>研究资料 · {selected.length} 篇</span>
    </button>
    <Drawer className="research-source-mobile-drawer" title="本对话的研究资料" open={panelOpen} onClose={() => setPanelOpen(false)} width="min(320px, 92vw)">{panel}</Drawer>
    <Drawer title="选择研究资料" open={open} onClose={() => setOpen(false)} width="min(720px, 100vw)">{content}</Drawer>
  </>;
}

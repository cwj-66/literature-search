import { useEffect, useState } from "react";
import { Checkbox, Input, Popover, Button } from "antd";
import { createPortal } from "react-dom";
import { useProjects } from "../projects";

export function ResearchSources({ selected, onChange, disabled, currentId }: {
  selected: string[]; onChange: (ids: string[]) => void; disabled: boolean; currentId: string;
}) {
  const { projects } = useProjects();
  const [active, setActive] = useState(currentId);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useEffect(() => { setTarget(document.getElementById("research-header-slot")); }, []);
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
  const control = <Popover trigger="click" placement="bottom" open={open} onOpenChange={setOpen} content={content}>
    <Button className="research-source-trigger" disabled={disabled}>研究资料 · {count} 个工作区 · {selected.length} 篇⌄</Button>
  </Popover>;
  return target ? createPortal(control, target) : control;
}

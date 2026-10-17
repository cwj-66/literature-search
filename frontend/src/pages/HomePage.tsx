import { useEffect, useState } from "react";
import { Button, Empty, Input, Modal, Popconfirm, Segmented, Select } from "antd";
import { CheckCircleOutlined, ClockCircleOutlined, DeleteOutlined, FileTextOutlined, PlusOutlined, ReadOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import { AnalysisBadge, isAnalyzed, useAnalysisStates } from "../components/AnalysisBadge";
import { Abstract, formatAuthors, paperFacts } from "../components/PaperRow";
import { projects, STATUS_LABEL, useProjects, type ProjectPaper, type ReadStatus } from "../projects";
import { paperLocation } from "../workspace";

const SUGGESTIONS = ["RAG 幻觉研究", "大模型微调技术", "AI Agent 应用研究"];
type Filter = "all" | ReadStatus;

function CreateProject({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const submit = () => { if (!name.trim()) return; projects.create(name); setName(""); onClose(); };
  return <Modal open={open} title="新建研究项目" okText="创建" cancelText="取消" onOk={submit} onCancel={onClose} okButtonProps={{ disabled: !name.trim() }} destroyOnClose>
    <Input autoFocus value={name} maxLength={40} placeholder="例如：RAG 幻觉研究" onChange={e => setName(e.target.value)} onPressEnter={submit} />
    <div className="sample-list home-suggest">{SUGGESTIONS.map(item => <button type="button" className="sample-button" key={item} onClick={() => setName(item)}>{item}</button>)}</div>
  </Modal>;
}

function PaperCard({ itemKey, item, analysis }: { itemKey: string; item: ProjectPaper; analysis: Parameters<typeof AnalysisBadge>[0]["state"] }) {
  const [noteOpen, setNoteOpen] = useState(Boolean(item.note));
  const { paper } = item;
  const next: ReadStatus = item.status === "read" ? "unread" : "read";
  return <article className="home-paper">
    <div className="home-paper-head">
      <h3 className="home-paper-title"><Link to={paperLocation(itemKey, "/")} onClick={() => projects.open(paper)}>{paper.title}</Link></h3>
      <span className={`status status-${item.status === "read" ? "success" : item.status === "reading" ? "accent" : "neutral"}`}>{STATUS_LABEL[item.status]}</span>
    </div>
    <p className="paper-byline">{formatAuthors(paper.authors)}</p>
    <p className="paper-facts">{paperFacts(paper).map(f => <span key={f}>{f}</span>)}</p>
    <Abstract text={paper.abstract} lines={3} />
    <div className="home-paper-analysis"><AnalysisBadge state={analysis} /><span className="small-print">{isAnalyzed(analysis) ? "已有分析记录，可回到原文核验" : "尚未分析，打开论文后可发起精读"}</span></div>
    <div className="home-note">
      {noteOpen
        ? <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} maxLength={2000} placeholder="添加笔记…" value={item.note} onChange={e => projects.setNote(itemKey, e.target.value)} />
        : <button type="button" className="link-button" onClick={() => setNoteOpen(true)}>添加笔记…</button>}
    </div>
    <div className="home-paper-actions">
      <Link to={paperLocation(itemKey, "/")} onClick={() => projects.open(paper)}><Button type="primary" size="small" icon={<ReadOutlined />}>{item.status === "unread" ? "开始精读" : "继续精读"}</Button></Link>
      <Button size="small" icon={<CheckCircleOutlined />} onClick={() => projects.setStatus(itemKey, next)}>{next === "read" ? "标记已读" : "标为未读"}</Button>
      <Popconfirm title="从当前项目移出这篇论文？" description="文献库中的记录和分析会保留。" okText="移出" cancelText="取消" onConfirm={() => projects.removePaper(itemKey)}>
        <Button size="small" type="text" icon={<DeleteOutlined />}>移出</Button>
      </Popconfirm>
    </div>
  </article>;
}

export default function HomePage() {
  const { projects: list, current } = useProjects();
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => { document.title = "研究项目工作台 - 文献研读"; }, []);
  const entries = Object.entries(current?.papers ?? {}).sort((a, b) => (b[1].openedAt ?? b[1].addedAt).localeCompare(a[1].openedAt ?? a[1].addedAt));
  const ids = entries.map(([key]) => key);
  const states = useAnalysisStates(ids);
  const count = (s: ReadStatus) => entries.filter(([, item]) => item.status === s).length;
  const analyzed = ids.filter(id => isAnalyzed(states[id])).length;
  const shown = entries.filter(([, item]) => filter === "all" || item.status === filter);
  const resume = entries.find(([, item]) => item.openedAt && item.status !== "read");

  if (!list.length) return <main className="page page-medium home-empty">
    <h1 className="page-title">研究项目工作台</h1>
    <p className="page-sub">为每个研究方向建一个项目。论文、阅读进度、笔记和分析记录都保存在项目里，下次打开可以接着研究。</p>
    <div className="panel home-create">
      <label className="field-label" htmlFor="first-project">项目名称</label>
      <Input.Search id="first-project" size="large" maxLength={40} placeholder="例如：RAG 幻觉研究" enterButton="创建项目"
        onSearch={value => { if (value.trim()) projects.create(value); }} />
      <div className="sample-list home-suggest">{SUGGESTIONS.map(item => <button type="button" className="sample-button" key={item} onClick={() => projects.create(item)}>{item}</button>)}</div>
      <p className="field-help">项目保存在当前浏览器中，更换浏览器或清除站点数据后不会保留；论文本身和分析结果存放在文献库。</p>
    </div>
  </main>;

  return <main className="page page-wide home-page">
    <header className="home-head">
      <div>
        <h1 className="page-title">研究项目工作台</h1>
        <p className="page-sub">当前项目的论文、阅读进度和笔记</p>
      </div>
      <div className="home-project-pick">
        <Select aria-label="当前研究项目" value={current?.id} onChange={projects.select} style={{ minWidth: 220 }} options={list.map(p => ({ value: p.id, label: `${p.name}（${Object.keys(p.papers).length}）` }))} />
        <Button icon={<PlusOutlined />} onClick={() => setCreating(true)}>新建项目</Button>
        <Popconfirm title={`删除项目「${current?.name}」？`} description="只删除项目内的阅读记录和笔记，文献库不受影响。" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={projects.removeProject}>
          <Button type="text" icon={<DeleteOutlined />} aria-label="删除当前项目" />
        </Popconfirm>
      </div>
    </header>
    <section className="home-stats" aria-label="项目统计">
      <div className="stat-card tone-blue"><span>项目论文</span><strong>{entries.length}</strong><FileTextOutlined aria-hidden /></div>
      <div className="stat-card tone-violet"><span>未读</span><strong>{count("unread")}</strong><ClockCircleOutlined aria-hidden /></div>
      <div className="stat-card tone-amber"><span>在读</span><strong>{count("reading")}</strong><ReadOutlined aria-hidden /></div>
      <div className="stat-card tone-green"><span>已读 / 已分析</span><strong>{count("read")} / {analyzed}</strong><CheckCircleOutlined aria-hidden /></div>
    </section>
    {resume ? <section className="home-resume">
      <span className="small-print">上次读到</span>
      <Link to={paperLocation(resume[0], "/")} onClick={() => projects.open(resume[1].paper)}>{resume[1].paper.title}</Link>
      <Link to={paperLocation(resume[0], "/")} onClick={() => projects.open(resume[1].paper)}><Button size="small" type="primary">继续阅读</Button></Link>
    </section> : null}
    <div className="home-bar">
      <Segmented value={filter} onChange={v => setFilter(v as Filter)} options={[
        { value: "all", label: `全部 ${entries.length}` }, { value: "unread", label: `未读 ${count("unread")}` },
        { value: "reading", label: `在读 ${count("reading")}` }, { value: "read", label: `已读 ${count("read")}` }]} />
      <div className="page-actions"><Link to="/search"><Button icon={<PlusOutlined />}>检索新论文</Button></Link><Link to="/graph" className="quiet-link">项目图谱</Link></div>
    </div>
    {!entries.length ? <Empty description={<>项目里还没有论文。<Link to="/search">去检索</Link>并收藏。</>} />
      : !shown.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该状态下没有论文" />
      : <div className="home-list">{shown.map(([key, item]) => <PaperCard key={key} itemKey={key} item={item} analysis={states[key]} />)}</div>}
    <CreateProject open={creating} onClose={() => setCreating(false)} />
  </main>;
}

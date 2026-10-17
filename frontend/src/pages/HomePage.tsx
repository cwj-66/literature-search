import { useEffect, useState } from "react";
import { Button, Input, Modal, Popconfirm } from "antd";
import { DeleteOutlined, FolderOpenOutlined, PlusOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { projectUpdated, projects, useProjects } from "../projects";

const SUGGESTIONS = ["RAG 幻觉研究", "大模型微调技术", "AI Agent 应用研究"];

function CreateWorkspace({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const submit = () => {
    if (!name.trim()) return;
    const id = projects.create(name, description);
    setName(""); setDescription(""); onClose();
    navigate(`/w/${id}`);
  };
  return <Modal open={open} title="创建工作区" okText="创建" cancelText="取消" onOk={submit} onCancel={onClose} okButtonProps={{ disabled: !name.trim() }} destroyOnClose>
    <label className="field-label" htmlFor="ws-name">名称</label>
    <Input id="ws-name" autoFocus value={name} maxLength={40} placeholder="例如：机器学习" onChange={e => setName(e.target.value)} onPressEnter={submit} />
    <label className="field-label" htmlFor="ws-desc">描述（选填）</label>
    <Input.TextArea id="ws-desc" value={description} maxLength={120} autoSize={{ minRows: 2, maxRows: 4 }} placeholder="例如：关注小样本学习的方法与实验" onChange={e => setDescription(e.target.value)} />
    <div className="sample-list home-suggest">{SUGGESTIONS.map(item => <button type="button" className="sample-button" key={item} onClick={() => setName(item)}>{item}</button>)}</div>
  </Modal>;
}

export default function HomePage() {
  const { projects: list } = useProjects();
  const [creating, setCreating] = useState(false);
  useEffect(() => { document.title = "研究工作区 - 文献研究助手"; }, []);
  const ordered = [...list].sort((a, b) => projectUpdated(b).localeCompare(projectUpdated(a)));
  return <main className="page page-wide workspace-home">
    <header className="workspace-head">
      <div>
        <h1 className="page-title">研究工作区</h1>
        <p className="page-sub">管理你的研究项目、论文和调研进度。</p>
      </div>
      {ordered.length ? <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>创建工作区</Button> : null}
    </header>
    {ordered.length ? <div className="workspace-grid">
      {ordered.map(item => {
        const count = Object.keys(item.papers).length;
        const updated = new Date(projectUpdated(item)).toLocaleDateString("zh-CN");
        return <article key={item.id} className="workspace-card">
          <Link className="workspace-card-link" to={`/w/${item.id}`}>
            <FolderOpenOutlined className="workspace-card-icon" aria-hidden />
            <h2>{item.name}</h2>
            <p>{item.description || "暂无描述"}</p>
            <span>{count} 篇论文 · 更新于 {updated}</span>
          </Link>
          <Popconfirm title={`删除工作区「${item.name}」？`} description="只删除工作区内的阅读记录和笔记，文献库不受影响。" okText="删除" okButtonProps={{ danger: true }} cancelText="取消" onConfirm={() => { projects.select(item.id); projects.removeProject(); }}>
            <Button className="workspace-card-delete" type="text" icon={<DeleteOutlined />} aria-label={`删除工作区「${item.name}」`} />
          </Popconfirm>
        </article>;
      })}
    </div> : <section className="workspace-empty">
      <FolderOpenOutlined aria-hidden />
      <h2>开始你的第一次研究</h2>
      <p>还没有论文工作区。创建一个研究主题，把搜索到的论文放在一起，开始阅读和讨论。</p>
      <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => setCreating(true)}>创建研究工作区</Button>
      <Link to="/search">去搜索论文</Link>
    </section>}
    <CreateWorkspace open={creating} onClose={() => setCreating(false)} />
  </main>;
}

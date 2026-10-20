import { useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Modal, Popconfirm, Skeleton } from "antd";
import { DeleteOutlined, EditOutlined, ExportOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { askConversation, createConversation, deleteConversation, fetchConversation, listConversations, prepareWorkspace, updateConversation } from "../api";
import { Answer } from "../components/studio/AskView";
import type { Project } from "../projects";
import type { Conversation, ConversationSummary } from "../types";
import { workspacePaperPath } from "../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./workspace/shared";

const LIMIT = 40;
const errorText = (err: unknown) => err instanceof Error ? err.message : "操作失败，请重试";

export default function StudioPage() {
  const { id, project } = useWorkspaceProject();
  return project ? <Studio key={id} project={project} /> : <MissingWorkspace />;
}

function Studio({ project }: { project: Project }) {
  const id = project.id;
  const [params, setParams] = useSearchParams();
  const currentId = params.get("chat") ?? "";
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [thread, setThread] = useState<Conversation | null>(null);
  const [ready, setReady] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const activeId = useRef(currentId);
  activeId.current = currentId;
  const open = (chat: string) => setParams(chat ? { chat } : {});
  const papersKey = Object.keys(project.papers).sort().join("|");
  useEffect(() => { document.title = `${project.name} - 研究台`; }, [project.name]);
  useEffect(() => {
    const controller = new AbortController();
    setReady(false); setError("");
    prepareWorkspace(id, project.name, Object.keys(project.papers), controller.signal)
      .then(() => listConversations(id, controller.signal))
      .then(list => {
        if (controller.signal.aborted) return;
        setItems(list); setReady(true);
        if (!activeId.current && list.length) setParams({ chat: list[0].id }, { replace: true });
      }).catch(err => { if (!controller.signal.aborted) setError(errorText(err)); });
    return () => controller.abort();
  }, [id, project.name, papersKey, retry]);
  useEffect(() => {
    setThread(null);
    if (!currentId || !ready) return;
    const controller = new AbortController();
    fetchConversation(id, currentId, controller.signal).then(value => { if (!controller.signal.aborted) setThread(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorText(err)); });
    return () => controller.abort();
  }, [id, currentId, ready]);

  function changed(value: Conversation) {
    if (activeId.current === value.id) setThread(value);
    setItems(list => [value, ...list.filter(item => item.id !== value.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
  }
  async function create() {
    if (creating || !ready) return;
    setCreating(true); setError("");
    try { const value = await createConversation(id); setItems(list => [value, ...list]); open(value.id); }
    catch (err) { setError(errorText(err)); }
    finally { setCreating(false); }
  }
  async function remove(value: ConversationSummary) {
    try {
      await deleteConversation(id, value.id);
      const rest = items.filter(item => item.id !== value.id);
      setItems(rest);
      if (currentId === value.id) open(rest[0]?.id ?? "");
    } catch (err) { setError(errorText(err)); }
  }
  return <main className="ws-page workspace-studio">
    <header className="ws-page-head"><div><h1>研究台</h1><p>范围：「{project.name}」中的论文</p></div>
      <Button type="primary" icon={<PlusOutlined />} disabled={!ready} loading={creating} onClick={create}>新建对话</Button>
    </header>
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => { setError(""); setRetry(v => v + 1); }}>重试</Button>} /> : null}
    <div className="workspace-studio-layout">
      <aside className="conversation-list" aria-label="已保存的对话"><h2>我的对话 <span>{items.length}</span></h2>
        {!ready && !error ? <Skeleton active /> : null}
        {ready && !items.length ? <p className="muted">还没有对话。新建一个，选论文后开始提问。</p> : null}
        {items.map(item => <div key={item.id} className={`conversation-row${item.id === currentId ? " is-active" : ""}`}>
          <button type="button" className="conversation-open" aria-current={item.id === currentId ? "true" : undefined} onClick={() => open(item.id)}><strong>{item.title}</strong><small>{item.paper_ids.length} 篇论文 · {new Date(item.updated_at).toLocaleDateString("zh-CN")}</small></button>
          <Popconfirm title="删除这个对话及其问答记录？" okText="删除" cancelText="取消" onConfirm={() => remove(item)}><Button type="text" size="small" icon={<DeleteOutlined />} aria-label={`删除对话：${item.title}`} /></Popconfirm>
        </div>)}
      </aside>
      <section className="conversation-main" aria-label="研究对话">
        {!ready ? null : currentId ? thread?.id === currentId ? <ConversationView key={currentId} project={project} thread={thread} onChange={changed} /> : !error ? <Skeleton active /> : null
          : <Empty description="为这个研究主题新建一个对话"><Button type="primary" onClick={create} loading={creating}>开始第一个对话</Button></Empty>}
      </section>
    </div>
  </main>;
}

function ConversationView({ project, thread, onChange }: { project: Project; thread: Conversation; onChange: (value: Conversation) => void }) {
  const [question, setQuestion] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(thread.title);
  const controller = useRef<AbortController | null>(null);
  const location = useLocation();
  const here = location.pathname + location.search;
  const entries = Object.entries(project.papers);
  const picked = thread.paper_ids.filter(id => Boolean(project.papers[id]));
  const pending = thread.messages.some(message => message.status === "pending");
  const disabled = busy || pending;
  const visible = entries.filter(([, item]) => `${item.paper.title} ${item.paper.year ?? ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!pending) return;
    const abort = new AbortController();
    let timer = window.setTimeout(poll, 2000);
    async function poll() {
      try { const value = await fetchConversation(project.id, thread.id, abort.signal); if (!abort.signal.aborted) onChange(value); }
      catch (err) { if (!abort.signal.aborted) setError(errorText(err)); }
      if (!abort.signal.aborted) timer = window.setTimeout(poll, 3000);
    }
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [project.id, thread.id, pending]);

  async function save(changes: { title?: string; paper_ids?: string[] }) {
    if (disabled) return;
    setBusy(true); setError("");
    try { onChange(await updateConversation(project.id, thread.id, changes)); setRenaming(false); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  async function ask() {
    const text = question.trim();
    if (!text || disabled || !picked.length) return;
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setError("");
    try {
      await askConversation(project.id, thread.id, text, picked, crypto.randomUUID(), abort.signal);
      const value = await fetchConversation(project.id, thread.id, abort.signal);
      if (!abort.signal.aborted) { onChange(value); setQuestion(""); }
    } catch (err) { if (!abort.signal.aborted) setError(`${errorText(err)}。可重新打开对话确认保存结果。`); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  function exportChat() {
    const lines = [`# ${thread.title}`, "", `工作区：${project.name}`, ""];
    for (const message of thread.messages) {
      lines.push(`## ${message.question}`, "", `论文范围：${message.papers.map(p => p.title).join("；")}`, "", message.response?.answer ?? message.error ?? "回答生成中", "");
      for (const evidence of message.response?.evidence ?? []) lines.push(`> [${evidence.evidence_id}] ${message.papers.find(p => p.paper_id === evidence.paper_id)?.title ?? evidence.paper_id}：${evidence.quote}`, "");
    }
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `${thread.title.replace(/[<>:"/\\|?*]/g, "_").slice(0, 50)}.md`; a.click(); URL.revokeObjectURL(url);
  }
  return <div className="saved-conversation">
    <header className="conversation-head"><div><h2>{thread.title}</h2><span className="small-print">{disabled ? pending ? "回答生成中，完成后自动保存" : "正在提交…" : "已自动保存"}</span></div><div className="conversation-actions">
      <Button icon={<EditOutlined />} disabled={disabled} onClick={() => { setTitle(thread.title); setRenaming(true); }}>重命名</Button>
      <Button icon={<ExportOutlined />} disabled={!thread.messages.length} onClick={exportChat}>导出对话</Button>
    </div></header>
    <Modal title="重命名对话" open={renaming} onCancel={() => setRenaming(false)} onOk={() => save({ title: title.trim() })} okText="保存" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !title.trim() }}><Input autoFocus aria-label="对话名称" value={title} maxLength={120} onChange={event => setTitle(event.target.value)} /></Modal>
    {error ? <Alert type="error" showIcon message={error} /> : null}
    <details className="conversation-scope" open><summary>本对话的论文范围 · 已选 {picked.length} 篇</summary>
      {!entries.length ? <p>当前工作区还没有论文。<Link to={`/w/${project.id}/library`}>先添加论文</Link></p> : <>
        <Input allowClear prefix={<SearchOutlined aria-hidden />} aria-label="查找对话可选论文" placeholder="查找标题或年份" value={filter} onChange={event => setFilter(event.target.value)} />
        <ul className="conversation-papers">{visible.map(([id, item]) => <li key={id}><Checkbox checked={picked.includes(id)} disabled={disabled || (!picked.includes(id) && picked.length >= LIMIT)} onChange={() => save({ paper_ids: picked.includes(id) ? picked.filter(ref => ref !== id) : [...picked, id] })}>{item.paper.title}</Checkbox></li>)}</ul>
        {!visible.length ? <p className="muted">没有匹配的论文。</p> : null}
        <p className="small-print">最多选择 {LIMIT} 篇。切换对话会恢复各自的范围；修改范围只影响之后的提问。</p>
      </>}
      {thread.paper_ids.length !== picked.length ? <Alert type="warning" showIcon message="部分论文已移出工作区，不会再参与检索；历史回答及其引用仍保留。" /> : null}
    </details>
    <div className="composer">
      <p className="field-help">{picked.length ? `只检索所选 ${picked.length} 篇论文的摘要与已有全文索引，不联网补充；证据不足会明确提示。` : "先选择一篇或多篇论文，再开始提问。"}</p>
      <Input.TextArea aria-label="问题" value={question} disabled={disabled || !picked.length} maxLength={2000} autoSize={{ minRows: 3, maxRows: 8 }} placeholder={picked.length ? "输入问题，或继续追问…" : "请先选择论文"} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void ask(); } }} />
      <div className="composer-actions"><span className="muted">Ctrl + Enter 发送</span><Button type="primary" loading={busy} disabled={disabled || !question.trim() || !picked.length} onClick={ask}>发送问题</Button></div>
    </div>
    {busy && !pending ? <p className="pending" role="status">正在提交，生成回答后会自动保存…</p> : null}
    <div className="conversation-messages">
      {!thread.messages.length ? <p className="muted">可以解读单篇论文，也可以比较多篇论文的研究方法、实验和局限。</p> : null}
      {[...thread.messages].reverse().map(message => {
        const titles = new Map(message.papers.map(paper => [paper.paper_id, paper.title]));
        const paths = new Map(message.papers.map(paper => [paper.paper_id, workspacePaperPath(project.id, paper.id, here)]));
        return <article key={message.id} className="qa-turn"><header><h3 className="qa-question">{message.question}</h3><p className="small-print">本次范围：{message.papers.map(p => p.title).join("；")}</p></header>
          {message.status === "pending" ? <p className="pending">正在检索所选论文并生成回答，可离开页面后再回来查看。</p> : message.error ? <Alert type="error" showIcon message={message.error} /> : message.response ? <Answer turn={{ id: message.id, question: message.question, scope: "", response: message.response }} titles={titles} paths={paths} from={here} /> : null}
        </article>;
      })}
    </div>
  </div>;
}

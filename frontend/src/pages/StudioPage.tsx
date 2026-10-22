import { pageTitle, formatMessageTime } from "../presentation";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Modal, Popconfirm, Skeleton } from "antd";
import { DeleteOutlined, DoubleLeftOutlined, DoubleRightOutlined, EditOutlined, ExportOutlined, MenuFoldOutlined, MenuUnfoldOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { askConversation, createConversation, deleteConversation, fetchConversation, listConversations, prepareWorkspace, updateConversation } from "../api";
import { Answer } from "../components/studio/AskView";
import { isDemo } from "../demo";
import { useCollapsed } from "../hooks";
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
  const [chatsCollapsed, toggleChats] = useCollapsed("sg-studio-chats");
  const activeId = useRef(currentId);
  const seedPapers = useRef(params.get("papers"));
  activeId.current = currentId;
  const open = (chat: string) => setParams(chat ? { chat } : {});
  const papersKey = Object.keys(project.papers).sort().join("|");
  const viewing = Boolean(currentId && thread?.id === currentId);
  useEffect(() => { document.title = pageTitle(`${project.name} - 智能问答`); }, [project.name]);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setReady(false); setError("");
    (async () => {
      try {
        await prepareWorkspace(id, project.name, Object.keys(project.papers), controller.signal);
        const list = await listConversations(id, controller.signal);
        if (!alive) return;
        const raw = seedPapers.current;
        if (raw) {
          const ids = raw.split(",").map(item => item.trim()).filter(Boolean).slice(0, LIMIT);
          const value = await createConversation(id, ids, controller.signal);
          if (!alive) return;
          seedPapers.current = "";
          setItems([value, ...list.filter(item => item.id !== value.id)]);
          setParams({ chat: value.id }, { replace: true });
          setReady(true);
          return;
        }
        setItems(list);
        setReady(true);
        if (!activeId.current && list.length) setParams({ chat: list[0].id }, { replace: true });
      } catch (err) { if (alive) setError(errorText(err)); }
    })();
    return () => { alive = false; controller.abort(); };
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
  return <div className="studio-layout">
    <aside className={`studio-chats${chatsCollapsed ? " is-collapsed" : ""}`} aria-label="已保存的对话">
      <div className="rail-head">
        {chatsCollapsed ? null : <h2>智能问答 <span>{items.length}</span></h2>}
        <button type="button" className="side-collapse" aria-expanded={!chatsCollapsed} aria-label={chatsCollapsed ? "展开对话列表" : "收起对话列表"} title={chatsCollapsed ? "展开对话列表" : "收起对话列表"} onClick={toggleChats}>{chatsCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}</button>
      </div>
      <Button type="primary" aria-label="新建对话" icon={<PlusOutlined />} block={!chatsCollapsed} disabled={!ready} loading={creating} onClick={create}>{chatsCollapsed ? null : "新建对话"}</Button>
      {chatsCollapsed ? null : <div className="studio-chat-list">
        {!ready && !error ? <Skeleton active /> : null}
        {ready && !items.length ? <p className="muted">还没有对话。新建一个，在右侧勾选论文后提问。</p> : null}
        {items.map(item => <div key={item.id} className={`conversation-row${item.id === currentId ? " is-active" : ""}`}>
          <button type="button" className="conversation-open" aria-current={item.id === currentId ? "true" : undefined} onClick={() => open(item.id)}><strong>{item.title}</strong><small>{item.paper_ids.length} 篇论文 · {formatMessageTime(item.updated_at)}</small></button>
          <Popconfirm title="删除这个对话及其问答记录？" okText="删除" cancelText="取消" onConfirm={() => remove(item)}><Button type="text" size="small" icon={<DeleteOutlined />} aria-label={`删除对话：${item.title}`} /></Popconfirm>
        </div>)}
      </div>}
    </aside>
    {viewing && thread ? <ConversationView key={currentId} project={project} thread={thread} onChange={changed} /> : <>
      <section className="studio-chat" aria-label="研究对话">
        {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => { setError(""); setRetry(v => v + 1); }}>重试</Button>} /> : null}
        <div className="studio-chat-empty">{(!ready && !error) || (Boolean(currentId) && !error && thread?.id !== currentId) ? <Skeleton active /> : currentId ? null : <Empty description="新建一个对话，在右侧勾选论文后开始提问"><Button type="primary" onClick={create} loading={creating} disabled={!ready}>开始第一个对话</Button></Empty>}</div>
      </section>
      <PaperRail locked picked={[]} disabled entries={Object.entries(project.papers)} filter="" onFilter={() => undefined} onToggle={() => undefined} note="打开或新建对话后，在这里勾选论文。回答只会使用勾选的论文。" />
    </>}
  </div>;
}

function ConversationView({ project, thread, onChange }: { project: Project; thread: Conversation; onChange: (value: Conversation) => void }) {
  const [question, setQuestion] = useState("");
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(thread.title);
  const controller = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const here = location.pathname + location.search;
  const entries = Object.entries(project.papers);
  const picked = thread.paper_ids.filter(id => Boolean(project.papers[id]));
  const pending = thread.messages.some(message => message.status === "pending");
  const disabled = busy || pending;
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
  useLayoutEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [thread.messages, busy]);

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
  return <div className="studio-thread">
    <section className="studio-chat" aria-label="研究对话">
      <header className="conversation-head"><div><h2>{thread.title}</h2><span className="small-print">{disabled ? pending ? "回答生成中，完成后自动保存" : "正在提交…" : "已自动保存"}</span></div><div className="conversation-actions">
        <Button icon={<EditOutlined />} disabled={disabled} onClick={() => { setTitle(thread.title); setRenaming(true); }}>重命名</Button>
        <Button icon={<ExportOutlined />} disabled={!thread.messages.length} onClick={exportChat}>导出对话</Button>
      </div></header>
      <Modal title="重命名对话" open={renaming} onCancel={() => setRenaming(false)} onOk={() => save({ title: title.trim() })} okText="保存" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !title.trim() }}><Input autoFocus aria-label="对话名称" value={title} maxLength={120} onChange={event => setTitle(event.target.value)} /></Modal>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      <div className="studio-log" ref={logRef}>
        {isDemo && thread.id.startsWith("demo-reviewed-") ? <p className="demo-example-notice">预置演示 · 依据公开论文摘要整理，非真实用户记录或现场模型输出。右侧展示逐字摘要依据，不代表全文分析。</p> : null}
        <div className="studio-log-inner">
          {!thread.messages.length ? <p className="muted">可以解读单篇论文，也可以比较多篇论文的研究方法、实验和局限。回答只使用右侧勾选的论文。</p> : null}
          {thread.messages.map(message => {
            const titles = new Map(message.papers.map(paper => [paper.paper_id, paper.title]));
            const paths = new Map(message.papers.map(paper => [paper.paper_id, workspacePaperPath(project.id, paper.id, here)]));
            return <article key={message.id} className="qa-turn"><header><time className="small-print" dateTime={message.created_at}>{formatMessageTime(message.created_at)}</time><h3 className="qa-question">{message.question}</h3><p className="small-print">本次范围：{message.papers.map(p => p.title).join("；")}</p></header>
              {message.status === "pending" ? <p className="pending">正在检索所选论文并生成回答，可离开页面后再回来查看。</p> : message.error ? <Alert type="error" showIcon message={message.error} /> : message.response ? <Answer turn={{ id: message.id, question: message.question, scope: "", response: message.response }} titles={titles} paths={paths} from={here} /> : null}
            </article>;
          })}
          {busy && !pending ? <p className="pending" role="status">正在提交，生成回答后会自动保存…</p> : null}
        </div>
      </div>
      <form className="studio-composer" onSubmit={event => { event.preventDefault(); void ask(); }}>
        <div className="studio-composer-inner">
          <p className="field-help">{picked.length ? `只检索所选 ${picked.length} 篇论文，不使用未勾选论文，也不联网补充。` : "先在右侧选择一篇或多篇论文，再开始提问。"}</p>
          <Input.TextArea aria-label="问题" value={question} disabled={disabled || !picked.length} maxLength={2000} autoSize={{ minRows: 2, maxRows: 6 }} placeholder={picked.length ? "输入问题，或继续追问…" : "请先在右侧选择论文"} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void ask(); } }} />
          <div className="studio-composer-actions"><span className="muted">Ctrl + Enter 发送</span><Button type="primary" htmlType="submit" loading={busy} disabled={disabled || !question.trim() || !picked.length}>发送问题</Button></div>
        </div>
      </form>
    </section>
    <PaperRail picked={picked} disabled={disabled} entries={entries} filter={filter} onFilter={setFilter} note="" missing={thread.paper_ids.length !== picked.length} onToggle={id => save({ paper_ids: picked.includes(id) ? picked.filter(ref => ref !== id) : [...picked, id] })} />
  </div>;
}

function PaperRail({ picked, disabled, locked, entries, filter, onFilter, onToggle, note, missing }: {
  picked: string[]; disabled: boolean; locked?: boolean; entries: [string, Project["papers"][string]][]; filter: string; onFilter: (value: string) => void;
  onToggle: (id: string) => void; note: string; missing?: boolean;
}) {
  const [collapsed, toggle] = useCollapsed("sg-studio-papers");
  const frozen = disabled || locked;
  const visible = entries.filter(([, item]) => `${item.paper.title} ${item.paper.year ?? ""}`.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));
  return <aside className={`studio-papers${collapsed ? " is-collapsed" : ""}`} aria-label="回答所用论文">
    <div className="rail-head">
      {collapsed ? null : <div><strong>回答范围</strong><span>已选 {picked.length} 篇</span></div>}
      <button type="button" className="side-collapse" aria-expanded={!collapsed} aria-label={collapsed ? "展开论文列表" : "收起论文列表"} title={collapsed ? "展开论文列表" : "收起论文列表"} onClick={toggle}>{collapsed ? <DoubleLeftOutlined /> : <DoubleRightOutlined />}</button>
    </div>
    {collapsed ? <span className="studio-paper-count">{picked.length}</span> : <>
      <p>{note || "勾选后，这次对话只能检索这些论文。"}</p>
      {!entries.length ? <p>当前工作区还没有论文。<Link to="../library">先添加论文</Link></p> : <>
        <Input allowClear prefix={<SearchOutlined aria-hidden />} aria-label="查找对话可选论文" placeholder="查找标题或年份" value={filter} disabled={locked} onChange={event => onFilter(event.target.value)} />
        <ul className="studio-paper-list">{visible.map(([id, item]) => <li key={id} className={picked.includes(id) ? "is-picked" : ""}><Checkbox checked={picked.includes(id)} disabled={frozen || (!picked.includes(id) && picked.length >= LIMIT)} onChange={() => onToggle(id)}>{item.paper.title}</Checkbox></li>)}</ul>
        {!visible.length ? <p className="muted">没有匹配的论文。</p> : null}
        <p className="small-print">最多选择 {LIMIT} 篇。未勾选的论文不会进入检索。</p>
      </>}
      {missing ? <Alert type="warning" showIcon message="部分论文已移出工作区，不会再参与检索；历史回答及其引用仍保留。" /> : null}
    </>}
  </aside>;
}

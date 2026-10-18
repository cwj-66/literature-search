import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { prepareWorkspace, researchDecision, startResearch, uploadPaper, workspacePapers } from "../../api";
import { projects, useProjects } from "../../projects";
import { ResearchSources } from "../../components/ResearchSources";
import { RenameDialog } from "../../components/RenameDialog";
import { EditOutlined } from "@ant-design/icons";
import { Button } from "antd";
import { AnalyzeResult } from "../../components/AnalyzeResult";
import { CollectButton } from "../../components/CollectButton";
import { formatAuthors, paperFacts } from "../../components/PaperRow";
import type { AnalyzePaperResult, Paper, ResearchTask } from "../../types";
import { paperKey } from "../../types";
import { paperLocation } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

interface Turn { id: number; question: string; answeredAt?: number; pending?: boolean; tool?: "search" | "analyze" | "agent"; error?: string; papers?: Paper[]; total?: number; analysis?: AnalyzePaperResult; task?: ResearchTask; note?: string }

const WELCOME = ["你好！我是你的 AI 研究助手。", "我可以帮你搜索和阅读学术论文、梳理研究方向、提炼关键信息，并将有价值的论文加入当前工作区。你想研究什么主题呢？"];

function formatClock(time: number) {
  const date = new Date(time);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function Spark() {
  return <span className="assistant-spark" aria-hidden="true"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2.4c.5 3.6 1.8 5.2 5.6 5.8-3.8.6-5.1 2.2-5.6 5.8-.5-3.6-1.8-5.2-5.6-5.8 3.8-.6 5.1-2.2 5.6-5.8Z" /><path fill="currentColor" d="M17.4 13.2c.2 1.5.8 2.2 2.3 2.4-1.5.2-2.1.8-2.3 2.3-.2-1.5-.8-2.1-2.3-2.3 1.5-.2 2.1-.9 2.3-2.4Z" /></svg></span>;
}

function AssistantArt() {
  return <svg className="assistant-art" viewBox="0 0 360 200" aria-hidden="true">
    <ellipse cx="250" cy="118" rx="92" ry="28" fill="#e7f1ff" />
    <path d="M78 132c46-62 150-78 230-18" fill="none" stroke="#d5e4f8" strokeWidth="8" strokeLinecap="round" />
    <g transform="translate(168 28) rotate(8)">
      <rect width="118" height="92" rx="14" fill="#f3f7ff" stroke="#d7e4f8" />
      <rect x="16" y="18" width="52" height="6" rx="3" fill="#c9daf6" />
      <rect x="16" y="32" width="78" height="5" rx="2.5" fill="#e3ecf8" />
      <rect x="16" y="44" width="64" height="5" rx="2.5" fill="#e3ecf8" />
    </g>
    <g transform="translate(126 58) rotate(-6)">
      <rect width="132" height="104" rx="16" fill="#fff" stroke="#d5e3f7" />
      <circle cx="28" cy="28" r="8" fill="#dce8fb" />
      <rect x="46" y="22" width="58" height="6" rx="3" fill="#c5d8f5" />
      <rect x="18" y="48" width="96" height="5" rx="2.5" fill="#e6eef8" />
      <rect x="18" y="60" width="80" height="5" rx="2.5" fill="#e6eef8" />
      <rect x="18" y="72" width="68" height="5" rx="2.5" fill="#e6eef8" />
    </g>
    <g transform="translate(214 46)">
      <path fill="#4c7dff" d="M18 2c.7 5.2 2.6 7.4 8 8.2-5.4.8-7.3 3-8 8.2-.7-5.2-2.6-7.4-8-8.2 5.4-.8 7.3-3 8-8.2Z" />
      <path fill="#8eb0ff" d="M30 16c.3 2.2 1.1 3.2 3.4 3.6-2.3.4-3.1 1.4-3.4 3.6-.3-2.2-1.1-3.2-3.4-3.6 2.3-.4 3.1-1.4 3.4-3.6Z" />
    </g>
  </svg>;
}

function Person() {
  return <span className="assistant-avatar" aria-hidden="true"><svg viewBox="0 0 24 24"><circle cx="12" cy="8.6" r="3.1" fill="currentColor" /><path fill="currentColor" d="M6.3 18.6c.7-2.8 2.7-4.2 5.7-4.2s5 1.4 5.7 4.2" /></svg></span>;
}

function AssistantBubble({ time, children }: { time: number; children: ReactNode }) {
  return <div className="assistant-msg is-assistant">
    <Spark />
    <div className="assistant-msg-body">
      <div className="assistant-bubble">{children}</div>
      <time dateTime={new Date(time).toISOString()}>{formatClock(time)}</time>
    </div>
  </div>;
}

function UserBubble({ time, children }: { time: number; children: ReactNode }) {
  return <div className="assistant-msg is-user">
    <div className="assistant-msg-body">
      <div className="assistant-bubble">{children}</div>
      <time dateTime={new Date(time).toISOString()}>{formatClock(time)}</time>
    </div>
    <Person />
  </div>;
}

function loadTurns(id: string): Turn[] {
  const key = `ws-ask:${id}`;
  try {
    const raw = localStorage.getItem(key) || sessionStorage.getItem(key);
    const value = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function Assistant({ id, chatId, onTitle, onBusy }: { id: string; chatId: string; onTitle: (title: string) => void; onBusy: (busy: boolean) => void }) {
  const storageId = `${id}:${chatId}`;
  const { projects: allProjects } = useProjects();
  const { project } = useWorkspaceProject();
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>(() => loadTurns(storageId));
  const logRef = useRef<HTMLDivElement>(null);
  const [greetAt] = useState(() => {
    const key = `ws-ask-greet:${storageId}`;
    const saved = Number(localStorage.getItem(key));
    if (Number.isFinite(saved) && saved > 0) return saved;
    const now = Date.now();
    try { localStorage.setItem(key, String(now)); } catch { /* the greeting still renders */ }
    return now;
  });
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"deep" | "standard">("deep");
  const [modeOpen, setModeOpen] = useState(false);
  const scope = "selected" as const;
  const [selectedIds, setSelectedIds] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(`research-sources:${storageId}`) || "[]"); } catch { return []; }
  });
  useEffect(() => { try { localStorage.setItem(`research-sources:${storageId}`, JSON.stringify(selectedIds)); } catch { /* retain in memory */ } }, [storageId, selectedIds]);
  useEffect(() => { onBusy(busy); }, [busy, onBusy]);
  const [attachNote, setAttachNote] = useState("");
  const [attaching, setAttaching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const here = `/w/${id}/research`;
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    try { localStorage.setItem(`ws-ask:${storageId}`, JSON.stringify(turns)); } catch { /* keep the in-memory thread if storage is full */ }
  }, [id, turns]);
  useEffect(() => {
    setTurns(current => current.some(turn => turn.pending) ? current.map(turn => !turn.pending ? turn : turn.papers || turn.analysis ? { ...turn, pending: false } : { ...turn, pending: false, error: turn.error || "已中断，请再发一次。" }) : current);
  }, [id]);
  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns]);

  async function send() {
    const question = draft.trim();
    if (!question || busy) return;
    if (!turns.length) onTitle(question.slice(0, 28));
    const turnId = Date.now();
    const abort = new AbortController();
    abortRef.current?.abort();
    abortRef.current = abort;
    setBusy(true);
    setDraft("");
    setTurns(current => [...current, { id: turnId, question, pending: true, tool: "agent" }]);
    try {
      await prepareWorkspace(id, project?.name || "研究工作区", Object.keys(project?.papers ?? {}), abort.signal);
      for (const source of allProjects.filter(p => p.id !== id && Object.keys(p.papers).some(key => selectedIds.includes(key)))) {
        await prepareWorkspace(source.id, source.name, Object.keys(source.papers), abort.signal);
      }
      const history = turns.filter(t => t.task?.answer).slice(-6).map(t => `用户：${t.question}\n助手：${t.task?.answer}`).join("\n\n").slice(-16000);
      const task = await startResearch(question, { autoApprove: mode !== "deep", mode: "auto",
        workspaceId: id, scope, selectedPaperIds: selectedIds.slice(0, 50), accessiblePaperIds: selectedIds, conversationHistory: history }, abort.signal);
      setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, pending: false, answeredAt: Date.now(), task, papers: task.papers?.length ? task.papers : undefined } : turn));
      projects.syncPapers(id, await workspacePapers(id, abort.signal));
    } catch (err) {
      if (!abort.signal.aborted) setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, pending: false, error: err instanceof Error ? err.message : "研究任务失败" } : turn));
    } finally { if (abortRef.current === abort) setBusy(false); }
  }

  async function decide(turn: Turn, approved: boolean) {
    if (!turn.task || busy) return;
    setBusy(true);
    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const paused = turn.task.interrupt;
      const decision = paused?.type === "review_plan" ? { approved, queries: paused.queries }
        : paused?.type === "clarify" ? (approved ? { scope, selected_paper_ids: selectedIds.slice(0, 50), accessible_paper_ids: selectedIds } : { abort: true })
        : { approved_ids: approved ? paused?.paper_ids ?? [] : [] };
      const task = await researchDecision(turn.task.task_id, { ...decision, accessible_paper_ids: selectedIds }, abort.signal);
      setTurns(current => current.map(item => item.id === turn.id ? { ...item, task, papers: task.papers?.length ? task.papers : undefined, answeredAt: Date.now(), error: undefined } : item));
      projects.syncPapers(id, await workspacePapers(id, abort.signal));
    } catch (err) {
      if (!abort.signal.aborted) setTurns(current => current.map(item => item.id === turn.id ? { ...item, error: err instanceof Error ? err.message : "任务恢复失败" } : item));
    } finally { setBusy(false); }
  }

  async function onAttach(file: File) {
    if (attaching) return;
    setAttaching(true);
    setAttachNote("");
    try {
      const result = await uploadPaper(file, id);
      projects.select(id);
      projects.add(result.paper);
      setSelectedIds(ids => [...new Set([...ids, paperKey(result.paper)])]);
      setAttachNote(result.index.status === "completed" ? `已加入论文库：${result.paper.title}` : (result.index.message || "已加入论文库，但向量入库失败"));
    } catch (err) {
      setAttachNote(err instanceof Error ? err.message : "上传失败");
    } finally {
      setAttaching(false);
    }
  }

  return <div className="assistant">
    <ResearchSources currentId={id} selected={selectedIds} onChange={setSelectedIds} disabled={busy} />
    <AssistantArt />
    <div className="assistant-log" ref={logRef} aria-live="polite">
      <div className="assistant-thread">
        <AssistantBubble time={greetAt}>{WELCOME.map(line => <p key={line}>{line}</p>)}</AssistantBubble>
        {turns.map(turn => <div className="assistant-pair" key={turn.id}>
          <UserBubble time={turn.id}>{turn.question}</UserBubble>
          <AssistantBubble time={turn.answeredAt ?? turn.id}>
            {turn.pending ? <p className="pending">正在研究并收集资料…</p> : null}
            {turn.task?.answer ? <p style={{ whiteSpace: "pre-wrap" }}>{turn.task.answer}</p> : null}
            {turn.task?.evidence?.length ? <details className="research-evidence"><summary>查看引用依据 · {turn.task.evidence.length} 条</summary>{turn.task.evidence.map(e => <p key={e.evidence_id}><strong>[{e.evidence_id}]</strong> {e.paper_id ? <Link to={paperLocation(e.paper_id, here)}>查看论文</Link> : null}<br />{e.quote}</p>)}</details> : null}
            {turn.task?.status === "waiting" ? <div>
              <p>{turn.task.interrupt?.hint || "请确认研究计划后继续。"}</p>
              {turn.task.interrupt?.queries?.map(query => <p key={query}>{query}</p>)}
              {turn.task.interrupt?.paper_ids?.map(ref => <p key={ref}>{project?.papers[ref]?.paper.title || ref}</p>)}
              {turn.task.interrupt?.type === "clarify" ? <p className="small-print">请在顶部“研究资料”中选择论文，再点击确认继续。</p> : null}
              <button type="button" disabled={busy} onClick={() => void decide(turn, true)}>确认并继续</button>{" "}
              <button type="button" disabled={busy} onClick={() => void decide(turn, false)}>拒绝本次操作</button>
            </div> : null}
            {turn.note ? <p className="small-print">{turn.note}</p> : null}
            {turn.analysis ? <><p className="small-print">已使用论文分析，提取研究内容、技术方案、创新点、实验结果和局限性。</p><AnalyzeResult result={turn.analysis} /></> : null}
            {turn.error ? <p className="inline-error" role="alert">{turn.error}</p> : null}
            {turn.papers ? <>
              <p className="small-print">本次涉及的论文{typeof turn.total === "number" ? `，找到 ${turn.total.toLocaleString("zh-CN")} 篇` : ""}。下面是前 {turn.papers.length} 篇，可以收藏到这个工作区。</p>
              {turn.papers.map(paper => <article className="assistant-hit" key={paperKey(paper)}>
                <h3><Link to={paperLocation(paperKey(paper), here)}>{paper.title}</Link></h3>
                <p className="paper-byline">{formatAuthors(paper.authors)}</p>
                <p className="paper-facts">{paperFacts(paper).map(item => <span key={item}>{item}</span>)}</p>
                <CollectButton paper={paper} onSaved={saved => setSelectedIds(ids => [...new Set([...ids, paperKey(saved)])])} />
              </article>)}
              {!turn.papers.length ? <p className="empty-hint">没有找到相关论文。</p> : null}
              <p className="assistant-more"><Link to={`/search?q=${encodeURIComponent(turn.question)}`}>在论文搜索中查看全部</Link></p>
            </> : null}
          </AssistantBubble>
        </div>)}
      </div>
    </div>
    {attachNote ? <p className="assistant-attach-note" role="status">{attachNote}</p> : null}
    <p className="research-access-hint">{selectedIds.length ? `本对话可访问 ${selectedIds.length} 篇论文，助手按需检索和阅读。` : "尚未选择本地资料，可以直接搜索新论文。"}</p>
    <form className="assistant-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      <button type="button" className="assistant-clip" aria-label="上传 PDF" disabled={attaching} onClick={() => fileRef.current?.click()}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.2 12.2 14.6 6a3.2 3.2 0 0 1 4.5 4.5l-8.2 8.2a4.4 4.4 0 0 1-6.2-6.2l7.6-7.6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      </button>
      <textarea value={draft} rows={1} maxLength={2000} placeholder="输入一个研究问题..." aria-label="研究问题" onChange={event => setDraft(event.target.value)} onKeyDown={event => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
      }} />
      <div className="assistant-mode">
        <button type="button" className="assistant-mode-btn" aria-expanded={modeOpen} onClick={() => setModeOpen(open => !open)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M4 12h16M12 4c2.2 2.4 3.3 5.1 3.3 8S14.2 17.6 12 20c-2.2-2.4-3.3-5.1-3.3-8S9.8 6.4 12 4Z" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
          {mode === "deep" ? "确认研究计划" : "自动执行计划"}
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>
        </button>
        {modeOpen ? <div className="assistant-mode-menu" role="menu">
          <button type="button" role="menuitem" onClick={() => { setMode("deep"); setModeOpen(false); }}>确认研究计划</button>
          <button type="button" role="menuitem" onClick={() => { setMode("standard"); setModeOpen(false); }}>自动执行计划</button>
        </div> : null}
      </div>
      <button className="assistant-send" type="submit" disabled={!draft.trim() || busy} aria-label="发送">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M13 6.5 18.5 12 13 17.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      <input ref={fileRef} hidden type="file" accept=".pdf,application/pdf" onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void onAttach(file); }} />
    </form>
  </div>;
}

interface SavedChat { id: string; title: string; titleEdited?: boolean }
function AssistantSessions({ id }: { id: string }) {
  const [params, setParams] = useSearchParams();
  const [chats, setChats] = useState<SavedChat[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(`research-chats:${id}`) || "null");
      if (Array.isArray(saved) && saved.length) return saved;
      const previous = localStorage.getItem(`ws-ask:${id}`);
      if (previous) localStorage.setItem(`ws-ask:${id}:first`, previous);
    } catch { /* create initial conversation */ }
    return [{ id: "first", title: "研究对话" }];
  });
  const [busy, setBusy] = useState(false);
  const [renameId, setRenameId] = useState<string | null>(null);
  const renameChat = chats.find(chat => chat.id === renameId);
  const seeded = useRef(false);
  const active = chats.find(c => c.id === params.get("conversation")) ?? chats[0];
  useEffect(() => { try { localStorage.setItem(`research-chats:${id}`, JSON.stringify(chats)); } catch { /* retain in memory */ } }, [id, chats]);
  const create = () => {
    const chat = { id: crypto.randomUUID(), title: "新对话" };
    const selection = params.get("papers");
    if (selection) {
      try { localStorage.setItem(`research-sources:${id}:${chat.id}`, JSON.stringify(selection.split(",").filter(Boolean))); } catch { /* optional initial selection */ }
    }
    setChats(list => [...list, chat]); setParams({ conversation: chat.id });
  };
  useEffect(() => { if (params.get("papers") && !seeded.current) { seeded.current = true; create(); } }, []);
  return <div className="research-layout">
    <aside className="research-chats" aria-label="研究对话列表">
      <h2>研究助手</h2><Button type="primary" block disabled={busy} onClick={create}>＋ 新建对话</Button>
      <p className="small-print">对话与资料选择保存在此浏览器</p>
      <div className="research-chat-list">{chats.map(chat => <div className={`research-chat-row${active.id === chat.id ? " is-active" : ""}`} key={chat.id}>
        <button type="button" className="research-chat-open" disabled={busy} aria-current={active.id === chat.id ? "true" : undefined} title={chat.title} onClick={() => setParams({ conversation: chat.id })}>{chat.title}</button>
        <button type="button" className="research-chat-rename" aria-label={`重命名对话：${chat.title}`} title="重命名对话" onClick={() => setRenameId(chat.id)}><EditOutlined /></button>
      </div>)}</div>
      <Link to={`/w/${id}/studio`}>查看原研究台历史对话</Link>
    </aside>
    <Assistant key={active.id} id={id} chatId={active.id} onBusy={setBusy} onTitle={title => setChats(list => list.map(c => c.id === active.id && !c.titleEdited ? { ...c, title } : c))} />
    <RenameDialog open={!!renameChat} title="重命名对话" label="对话名称" value={renameChat?.title ?? ""} maxLength={120}
      onClose={() => setRenameId(null)} onSave={title => setChats(list => list.map(chat => chat.id === renameId ? { ...chat, title, titleEdited: true } : chat))} />
  </div>;
}

export default function AssistantPage() {
  const { id, project } = useWorkspaceProject();
  useEffect(() => { document.title = project ? `${project.name} - 研究助手` : "工作区"; }, [project]);
  if (!project) return <MissingWorkspace />;
  return <AssistantSessions key={id} id={id} />;
}

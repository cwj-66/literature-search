import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { prepareWorkspace, researchDecision, startResearch, uploadPaper, workspacePapers, fetchResearchTask, cancelResearchTask } from "../../api";
import { projects, useProjects } from "../../projects";
import type { ResearchChat, ResearchTurn as Turn } from "../../researchChats";
import { CopyOutlined, ReloadOutlined, StopOutlined } from "@ant-design/icons";
import { Button, message } from "antd";
import { AnalyzeResult } from "../../components/AnalyzeResult";
import { paperKey } from "../../types";
import { ResearchPaperResults } from "../../components/ResearchPaperResults";
import { researchHasActivity, researchHistory, researchSummary } from "../../researchPresentation";
import { isDemo } from "../../demo";
import { paperLocation } from "../../workspace";
import { useWorkspaceProject } from "./shared";



const WELCOME = ["你好！我是检索助手。", "告诉我研究主题和要求，我可以帮你搜索、筛选学术论文，也可以回答一般问题。针对已收录论文提问，请到智能问答并勾选论文。"];
const RESEARCH_STEP: Record<string, string> = {
  route: "理解研究目标", plan: "制定研究计划", prepare: "理解论文需求", search: "寻找相关论文",
  finding: "判断下一步检索方向", detail: "查看候选论文详情",
  select: "筛选推荐论文", read: "阅读论文", quotes: "核对原文依据", assess: "检查资料是否充分", expand: "补充检索",
  synthesize: "整理研究结论", claims: "检查结论依据", judge: "复核回答", revise: "修订回答",
  finalize: "完成复核", stop: "研究结束",
};

function formatClock(time: number) {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true }).format(time);
}

function Spark({ thinking }: { thinking?: boolean }) {
  return <span className={`assistant-spark${thinking ? " is-thinking" : ""}`} aria-hidden="true"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2.4c.5 3.6 1.8 5.2 5.6 5.8-3.8.6-5.1 2.2-5.6 5.8-.5-3.6-1.8-5.2-5.6-5.8 3.8-.6 5.1-2.2 5.6-5.8Z" /><path fill="currentColor" d="M17.4 13.2c.2 1.5.8 2.2 2.3 2.4-1.5.2-2.1.8-2.3 2.3-.2-1.5-.8-2.1-2.3-2.3 1.5-.2 2.1-.9 2.3-2.4Z" /></svg></span>;
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

function MessageMeta({ time, onCopy, copyLabel, actions }: { time: number; onCopy?: () => void; copyLabel?: string; actions?: ReactNode }) {
  return <div className="assistant-msg-meta">
    <time dateTime={new Date(time).toISOString()}>{formatClock(time)}</time>
    {onCopy ? <Button type="text" size="small" className="assistant-copy" aria-label={copyLabel} title={copyLabel} icon={<CopyOutlined />} onClick={onCopy} /> : null}
    {actions}
  </div>;
}
function AssistantBubble({ time, thinking, children, onCopy, actions }: {
  time: number; thinking?: boolean; children: ReactNode; onCopy?: () => void; actions?: ReactNode;
}) {
  return <div className="assistant-msg is-assistant">
    <Spark thinking={thinking} />
    <div className="assistant-msg-body"><div className="assistant-bubble">{children}</div>
      <MessageMeta time={time} onCopy={onCopy} copyLabel="复制回答" actions={actions} />
    </div>
  </div>;
}
function UserBubble({ time, children, onCopy }: { time: number; children: ReactNode; onCopy: () => void }) {
  return <div className="assistant-msg is-user"><div className="assistant-msg-body">
    <div className="assistant-bubble">{children}</div><MessageMeta time={time} onCopy={onCopy} copyLabel="复制问题" />
  </div></div>;
}

export function ResearchConversation({ id, chat, onChange, onBusy, disabled }: {
  id: string; chat: ResearchChat; onChange: (patch: Partial<ResearchChat>) => void; onBusy: (busy: boolean) => void; disabled: boolean;
}) {
  const chatId = chat.id;
  const changeRef = useRef(onChange); changeRef.current = onChange;
  const storageId = `${id}:${chatId}`;
  const { projects: allProjects } = useProjects();
  const { project } = useWorkspaceProject();
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>(() => chat.turns);
  const logRef = useRef<HTMLDivElement>(null);
  const followBottom = useRef(true);
  const [greetAt] = useState(() => {
    const key = `ws-ask-greet:${storageId}`;
    const saved = Number(localStorage.getItem(key));
    if (Number.isFinite(saved) && saved > 0) return saved;
    const now = Date.now();
    try { localStorage.setItem(key, String(now)); } catch { /* the greeting still renders */ }
    return now;
  });
  const scope = "selected" as const;
  const [selectedIds, setSelectedIds] = useState<string[]>(chat.selectedIds);
  const [preparing, setPreparing] = useState(false);
  const [pollError, setPollError] = useState("");
  const [messageApi, messageContext] = message.useMessage();
  const stopRequested = useRef(false);
  const busy = preparing || turns.some(turn => turn.pending);
  useEffect(() => { onBusy(busy); }, [busy, onBusy]);
  useEffect(() => { if (!disabled) changeRef.current({ turns, selectedIds }); }, [turns, selectedIds, disabled]);
  const [attachNote, setAttachNote] = useState("");
  const [attaching, setAttaching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const here = `/w/${id}/research`;
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    setTurns(current => current.some(turn => turn.pending && !turn.task?.task_id)
      ? current.map(turn => turn.pending && !turn.task?.task_id
        ? { ...turn, pending: false, error: "未取得任务编号，可以重试原问题。" } : turn) : current);
  }, []);
  const activeTask = turns.find(turn => turn.pending && turn.task?.task_id);
  useEffect(() => {
    if (!activeTask?.task?.task_id) return;
    let alive = true; const controller = new AbortController(); let timer: number;
    async function poll() {
      try {
        const task = await fetchResearchTask(activeTask!.task!.task_id, controller.signal);
        if (!alive) return;
        const pending = task.status === "running";
        setTurns(current => current.map(turn => turn.id === activeTask!.id ? { ...turn, task, pending,
          papers: task.recommended ?? task.papers, total: task.candidate_count, answeredAt: pending ? turn.answeredAt : Date.now(),
          error: task.status === "failed" ? "任务执行失败，可重试原问题。" : undefined } : turn));
        setPollError("");
        if (!pending) {
          void workspacePapers(id).then(papers => projects.syncPapers(id, papers)).catch(() => {});
          return;
        }
      } catch (err) { if (alive) setPollError("暂时无法获取进度，正在重连。刷新页面后仍可继续查询。"); }
      if (alive) timer = window.setTimeout(poll, 2000);
    }
    void poll();
    return () => { alive = false; controller.abort(); window.clearTimeout(timer); };
  }, [activeTask?.id, activeTask?.task?.task_id, id]);
  useLayoutEffect(() => {
    const node = logRef.current;
    if (node && followBottom.current) node.scrollTop = node.scrollHeight;
  }, [turns]);

  async function send(retry?: Turn) {
    const question = retry?.question ?? draft.trim();
    if (!question || busy || disabled) return;
    const turnId = retry?.id ?? Date.now();
    const request = retry?.request ?? { selectedIds: [...selectedIds],
      history: turns.filter(t => t.id !== turnId && t.task?.answer).slice(-6).map(researchHistory).join("\n\n").slice(-16000) };
    const turn: Turn = { id: turnId, question, pending: true, request };
    followBottom.current = true;
    stopRequested.current = false; setPreparing(true); setDraft(""); setPollError("");
    setTurns(current => retry ? current.map(t => t.id === turnId ? turn : t) : [...current, turn]);
    if (!turns.length && !chat.titleEdited) changeRef.current({ title: question.slice(0, 28) });
    try {
      await prepareWorkspace(id, project?.name || "研究工作区", Object.keys(project?.papers ?? {}));
      for (const source of allProjects.filter(p => p.id !== id && Object.keys(p.papers).some(key => request.selectedIds.includes(key)))) {
        await prepareWorkspace(source.id, source.name, Object.keys(source.papers));
      }
      if (stopRequested.current) {
        setTurns(current => current.map(t => t.id === turnId ? { ...t, pending: false, note: "已停止，任务尚未开始。" } : t)); return;
      }
      const task = await startResearch(question, { autoApprove: true, mode: "auto", background: true,
        workspaceId: id, scope, selectedPaperIds: request.selectedIds.slice(0, 50), accessiblePaperIds: request.selectedIds, conversationHistory: request.history });
      if (stopRequested.current) await cancelResearchTask(task.task_id);
      setTurns(current => current.map(t => t.id === turnId ? { ...t, pending: task.status === "running", task,
        answeredAt: task.status === "running" ? undefined : Date.now(), papers: task.recommended ?? task.papers, total: task.candidate_count } : t));
    } catch (err) {
      setTurns(current => current.map(t => t.id === turnId ? { ...t, pending: false, error: err instanceof Error ? err.message : "研究任务失败" } : t));
    } finally { setPreparing(false); }
  }
  async function stop() {
    stopRequested.current = true;
    if (!activeTask?.task) return;
    try { await cancelResearchTask(activeTask.task.task_id); setPollError("已请求停止，正在保存已取得的结果。"); }
    catch { setPollError("停止请求未送达，请重试；后台任务仍可能运行。"); }
  }
  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); messageApi.success("已复制"); }
    catch { messageApi.error("复制失败，请选中文字手动复制"); }
  }
  async function decide(turn: Turn, approved: boolean) {
    if (!turn.task || busy || disabled) return;
    setPreparing(true);
    const abort = new AbortController();
    abortRef.current = abort;
    try {
      const paused = turn.task.interrupt;
      const decision = paused?.type === "review_plan" ? { approved, queries: paused.queries }
        : paused?.type === "clarify" ? (approved ? { scope, selected_paper_ids: selectedIds.slice(0, 50), accessible_paper_ids: selectedIds } : { abort: true })
        : { approved_ids: approved ? paused?.paper_ids ?? [] : [] };
      const task = await researchDecision(turn.task.task_id, { ...decision, accessible_paper_ids: selectedIds, background: true }, abort.signal);
      setTurns(current => current.map(item => item.id === turn.id ? { ...item, pending: task.status === "running", task, papers: task.recommended ?? task.papers, total: task.candidate_count, answeredAt: Date.now(), error: undefined } : item));
      projects.syncPapers(id, await workspacePapers(id, abort.signal));
    } catch (err) {
      if (!abort.signal.aborted) setTurns(current => current.map(item => item.id === turn.id ? { ...item, error: err instanceof Error ? err.message : "任务恢复失败" } : item));
    } finally { setPreparing(false); }
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
    {isDemo && chat.id.startsWith("demo-reviewed-") ? <p className="demo-example-notice">预置演示 · 内容依据公开论文摘要核对，非真实用户记录或现场模型输出。可展开引用依据；继续提问将使用正常研究流程。</p> : null}
    {messageContext}
    <div className="assistant-body">
      <div className="assistant-main">
        <AssistantArt />
        <div className="assistant-log" ref={logRef} aria-live="polite" onScroll={event => {
          const node = event.currentTarget;
          followBottom.current = node.scrollHeight - node.clientHeight - node.scrollTop < 64;
        }}>
          <div className="assistant-thread">
            <AssistantBubble time={greetAt}>{WELCOME.map(line => <p key={line}>{line}</p>)}</AssistantBubble>
            {turns.map(turn => <div className="assistant-pair" key={turn.id}>
              <UserBubble time={turn.id} onCopy={() => void copy(turn.question)}>{turn.question}</UserBubble>
              <AssistantBubble time={turn.answeredAt ?? turn.id} thinking={turn.pending}
                onCopy={turn.task?.answer ? () => void copy(turn.task!.answer!) : undefined}
                actions={!turn.pending && (turn.error || turn.note || turn.task?.status === "failed" || turn.task?.stop_reason?.includes("取消"))
                  ? <Button size="small" icon={<ReloadOutlined />} aria-label="重试原问题" disabled={busy || disabled} onClick={() => void send(turn)}>重试原问题</Button> : null}>
                {turn.pending ? <p className="pending">{RESEARCH_STEP[turn.task?.events?.at(-1)?.step ?? ""] ?? "正在回复"}…</p> : null}
                {turn.task?.answer ? <p style={{ whiteSpace: "pre-wrap" }}>{turn.task.answer}</p> : null}
                <ResearchPaperResults turn={turn} from={here} />
                {turn.task && researchHasActivity(turn) ? <details className="research-evidence">
                  <summary>{researchSummary(turn)} · 查看研究过程</summary>
                  <ol>{Array.from(new Set((turn.task.events ?? []).filter(event => RESEARCH_STEP[event.step]).map(event => event.step))).map(step => {
                    const events = turn.task!.events!.filter(event => event.step === step);
                    return <li key={step}>{RESEARCH_STEP[step]}{events.length > 1 ? `（${events.length} 次）` : ""}{events.at(-1)?.reason ? `：${events.at(-1)!.reason}` : ""}</li>;
                  })}</ol>
                  {turn.task.reading_status && Object.keys(turn.task.reading_status).length ? <p className="small-print">
                    资料读取：{Object.values(turn.task.reading_status).filter(row => row.basis === "full_text").length} 篇提供正文依据，
                    {Object.values(turn.task.reading_status).filter(row => row.basis === "abstract").length} 篇提供摘要依据，
                    {Object.values(turn.task.reading_status).filter(row => row.basis === "unavailable").length} 篇未提取到可用引文（不代表论文无关）。
                  </p> : null}
                  {turn.task.gaps?.length ? <details><summary>依据与局限</summary><ul>{turn.task.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></details> : null}
                  {Object.entries(turn.task.source_errors ?? {}).map(([source, error]) => <p key={source} className="small-print">来源状态 · {source}：{error}</p>)}
                </details> : null}
                {(() => {
                  const referencedIds = turn.task?.referenced_evidence_ids ?? (turn.task?.answer?.match(/E\d{4,}/g) ?? []);
                  const evidence = (turn.task?.evidence ?? []).filter(e => referencedIds.includes(e.evidence_id) && e.material !== "metadata" && !e.quote.startsWith('{"id":'));
                  return evidence.length ? <details className="research-evidence"><summary>查看引用依据 · {evidence.length} 条</summary>{evidence.map(e => <p key={e.evidence_id}><strong>[{e.evidence_id}]</strong> {e.paper_id ? <Link to={paperLocation(e.paper_id, here)}>查看论文</Link> : null}<br />{e.quote}</p>)}</details> : null;
                })()}
                {turn.task?.status === "waiting" ? <div>
                  <p>{turn.task.interrupt?.hint || "请确认本次操作后继续。"}</p>
                  {turn.task.interrupt?.queries?.map(query => <p key={query}>{query}</p>)}
                  {turn.task.interrupt?.paper_ids?.map(ref => <p key={ref}>{project?.papers[ref]?.paper.title || ref}</p>)}
                  {turn.task.interrupt?.type === "clarify" ? <p className="small-print">可上传 PDF，或到智能问答勾选论文后再提问。</p> : null}
                  <button type="button" disabled={busy || disabled} onClick={() => void decide(turn, true)}>确认并继续</button>{" "}
                  <button type="button" disabled={busy || disabled} onClick={() => void decide(turn, false)}>拒绝本次操作</button>
                </div> : null}
                {turn.note ? <p className="small-print">{turn.note}</p> : null}
                {turn.analysis ? <><p className="small-print">基于论文分析结果</p><AnalyzeResult result={turn.analysis} /></> : null}
                {turn.error ? <p className="inline-error" role="alert">{turn.error}</p> : null}
                {turn.task?.stop_reason?.includes("取消") ? <p className="small-print">任务已停止，已取得的结果保留。</p> : null}

              </AssistantBubble>
            </div>)}
          </div>
        </div>
        {pollError ? <p className="assistant-attach-note" role="status">{pollError}</p> : null}
        {attachNote ? <p className="assistant-attach-note" role="status">{attachNote}</p> : null}
        <p className="research-access-hint">{selectedIds.length ? <>本对话还留有 {selectedIds.length} 篇已选资料。<button type="button" className="link-button" disabled={busy || disabled} onClick={() => setSelectedIds([])}>清除</button> 按论文提问请到智能问答勾选范围。</> : "可以直接输入需求，搜索和筛选论文。"}</p>
        <form className="assistant-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
          <button type="button" className="assistant-clip" aria-label="上传 PDF" disabled={attaching || busy || disabled} onClick={() => fileRef.current?.click()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8.2 12.2 14.6 6a3.2 3.2 0 0 1 4.5 4.5l-8.2 8.2a4.4 4.4 0 0 1-6.2-6.2l7.6-7.6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
          </button>
          <textarea value={draft} rows={1} maxLength={2000} placeholder="输入一个研究问题..." aria-label="研究问题" disabled={busy || disabled} onChange={event => setDraft(event.target.value)} onKeyDown={event => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
          }} />
          {busy ? <Button className="research-stop" aria-label="停止任务" icon={<StopOutlined />} onClick={() => void stop()}>停止任务</Button> : null}
          <button className="assistant-send" type="submit" disabled={!draft.trim() || busy || disabled} aria-label="发送">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M13 6.5 18.5 12 13 17.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </button>
          <input ref={fileRef} hidden type="file" accept=".pdf,application/pdf" onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void onAttach(file); }} />
        </form>
      </div>
    </div>
  </div>;
}


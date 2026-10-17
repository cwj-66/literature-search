import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { searchPapers } from "../../api";
import { CollectButton } from "../../components/CollectButton";
import { formatAuthors, paperFacts } from "../../components/PaperRow";
import { useSessionState } from "../../hooks";
import type { Paper } from "../../types";
import { paperKey } from "../../types";
import { paperLocation } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

interface Turn { id: number; question: string; pending?: boolean; error?: string; papers?: Paper[]; total?: number }

function Assistant({ id }: { id: string }) {
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useSessionState<Turn[]>(`ws-ask:${id}`, []);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const here = `/w/${id}`;
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    setTurns(current => current.some(turn => turn.pending) ? current.map(turn => turn.pending ? { ...turn, pending: false, error: turn.error || (turn.papers ? undefined : "搜索已中断，请再发一次。") } : turn) : current);
  }, [setTurns]);

  function send() {
    const question = draft.trim();
    if (!question || busy) return;
    const turnId = Date.now();
    const abort = new AbortController();
    abortRef.current?.abort();
    abortRef.current = abort;
    setBusy(true);
    setDraft("");
    setTurns(current => [...current, { id: turnId, question, pending: true }]);
    searchPapers({ q: question, page: 1, pageSize: 6, sort: "relevance", source: "all" }, abort.signal)
      .then(result => setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, pending: false, papers: result.papers, total: result.total } : turn)))
      .catch((err: Error) => setTurns(current => current.flatMap(turn => turn.id !== turnId ? [turn] : abort.signal.aborted ? [] : [{ ...turn, pending: false, error: err.message || "搜索失败，请重试" }])))
      .finally(() => { if (abortRef.current === abort) setBusy(false); });
  }

  return <div className="assistant">
    <h1>你想研究什么？</h1>
    <p className="assistant-lead">可以搜索论文、分析研究问题，或比较工作区中的论文。</p>
    <form className="assistant-box" onSubmit={event => { event.preventDefault(); send(); }}>
      <textarea value={draft} rows={3} maxLength={2000} placeholder="帮我找有关机器学习中模型泛化能力的研究论文。" aria-label="研究问题" onChange={event => setDraft(event.target.value)} onKeyDown={event => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
      }} />
      <button className="assistant-send" type="submit" disabled={!draft.trim() || busy} aria-label="发送">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17V7M7.5 11.5 12 7l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
    </form>
    <p className="assistant-note">Agent 将根据任务选择搜索、RAG 或其他工具。</p>
    {turns.length ? <div className="assistant-turns" aria-live="polite">
      {turns.map(turn => <section className="assistant-turn" key={turn.id}>
        <h2 className="assistant-question">{turn.question}</h2>
        {turn.pending ? <p className="pending">正在搜索论文…</p> : null}
        {turn.error ? <p className="inline-error" role="alert">{turn.error}</p> : null}
        {turn.papers ? <>
          <p className="small-print">已用论文搜索{typeof turn.total === "number" ? `，找到 ${turn.total.toLocaleString("zh-CN")} 篇` : ""}。下面是前 {turn.papers.length} 篇，可以收藏到这个工作区。</p>
          {turn.papers.map(paper => <article className="assistant-hit" key={paperKey(paper)}>
            <h3><Link to={paperLocation(paperKey(paper), here)}>{paper.title}</Link></h3>
            <p className="paper-byline">{formatAuthors(paper.authors)}</p>
            <p className="paper-facts">{paperFacts(paper).map(item => <span key={item}>{item}</span>)}</p>
            <CollectButton paper={paper} />
          </article>)}
          {!turn.papers.length ? <p className="empty-hint">没有找到相关论文。</p> : null}
          <p className="assistant-more"><Link to={`/search?q=${encodeURIComponent(turn.question)}`}>在论文搜索中查看全部</Link></p>
        </> : null}
      </section>)}
    </div> : null}
  </div>;
}

export default function AssistantPage() {
  const { id, project } = useWorkspaceProject();
  useEffect(() => { document.title = project ? `${project.name} - AI 研究助手` : "工作区"; }, [project]);
  if (!project) return <MissingWorkspace />;
  return <Assistant key={id} id={id} />;
}

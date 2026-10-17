import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { analyzePapers, searchPapers } from "../../api";
import { AnalyzeResult } from "../../components/AnalyzeResult";
import { CollectButton } from "../../components/CollectButton";
import { formatAuthors, paperFacts } from "../../components/PaperRow";
import { useSessionState } from "../../hooks";
import type { AnalyzePaperResult, Paper } from "../../types";
import { paperKey } from "../../types";
import { paperLocation } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

interface Turn { id: number; question: string; pending?: boolean; tool?: "search" | "analyze"; error?: string; papers?: Paper[]; total?: number; analysis?: AnalyzePaperResult; note?: string }

function wantsAnalysis(text: string) {
  return /分析|精读|创新点|局限|技术方案|实验结果|研究内容/.test(text);
}

function Assistant({ id }: { id: string }) {
  const { project } = useWorkspaceProject();
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useSessionState<Turn[]>(`ws-ask:${id}`, []);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const here = `/w/${id}`;
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    setTurns(current => current.some(turn => turn.pending) ? current.map(turn => !turn.pending ? turn : turn.papers || turn.analysis ? { ...turn, pending: false } : { ...turn, pending: false, error: turn.error || "已中断，请再发一次。" }) : current);
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
    const analyze = wantsAnalysis(question);
    const paperIds = Object.entries(project?.papers ?? {}).sort((a, b) => (b[1].openedAt ?? b[1].addedAt).localeCompare(a[1].openedAt ?? a[1].addedAt)).map(([key]) => key);
    if (analyze && !paperIds.length) {
      setTurns(current => [...current, { id: turnId, question, tool: "analyze", error: "工作区里还没有论文，无法分析。可以先搜索并收藏。" }]);
      setBusy(false);
      return;
    }
    const ids = paperIds.slice(0, 5);
    setTurns(current => [...current, { id: turnId, question, pending: true, tool: analyze ? "analyze" : "search", note: analyze && paperIds.length > 5 ? "工作区论文超过 5 篇，本次分析最近阅读的 5 篇。" : undefined }]);
    const request = analyze
      ? analyzePapers({ paper_ids: ids, workspace_id: id, depth: "standard" }, abort.signal).then(analysis => setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, pending: false, analysis } : turn)))
      : searchPapers({ q: question, page: 1, pageSize: 6, sort: "relevance", source: "all" }, abort.signal).then(result => setTurns(current => current.map(turn => turn.id === turnId ? { ...turn, pending: false, papers: result.papers, total: result.total } : turn)));
    request.catch((err: Error) => setTurns(current => current.flatMap(turn => turn.id !== turnId ? [turn] : abort.signal.aborted ? [] : [{ ...turn, pending: false, error: err.message || (analyze ? "分析失败，请重试" : "搜索失败，请重试") }])))
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
        {turn.pending ? <p className="pending">{turn.tool === "analyze" ? "正在分析论文…" : "正在搜索论文…"}</p> : null}
        {turn.note ? <p className="small-print">{turn.note}</p> : null}
        {turn.analysis ? <><p className="small-print">已使用论文分析，提取研究内容、技术方案、创新点、实验结果和局限性。</p><AnalyzeResult result={turn.analysis} /></> : null}
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

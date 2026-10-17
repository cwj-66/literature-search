import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import { ArrowLeftOutlined, ArrowUpOutlined, CommentOutlined } from "@ant-design/icons";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { askLibrary, fetchAnalysis, fetchPaper, startAnalysis } from "../../api";
import { useSessionState } from "../../hooks";
import { projects } from "../../projects";
import type { AnalysisClaim, AnalysisResult, AnalysisState, ClaimKind, Paper, QAResponse } from "../../types";
import { SOURCE_LABEL } from "../../types";
import { safeReturn } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

const ACTIVE = new Set(["pending", "preparing", "analyzing"]);
const GROUPS: { no: string; title: string; kinds: ClaimKind[] }[] = [
  { no: "01", title: "研究内容", kinds: ["research_problem", "main_content"] },
  { no: "02", title: "技术方案", kinds: ["technical_method", "method_step"] },
  { no: "03", title: "创新点与主要贡献", kinds: ["innovation"] },
  { no: "04", title: "实验结果与局限性", kinds: ["evaluation", "limitation"] },
];

interface Turn { id: number; question: string; response?: QAResponse; error?: string }

function claimsOf(result: AnalysisResult | null, kinds: ClaimKind[]) {
  return result?.claims?.filter(claim => kinds.includes(claim.kind) && claim.status === "supported" && claim.text && claim.text !== "论文未明确说明") ?? [];
}

function sourceLabel(claims: AnalysisClaim[]) {
  const names = [...new Set(claims.flatMap(claim => claim.evidence.map(item => item.paragraph_id)).slice(0, 3))];
  return names.length ? `段落 ${names.join("、")}` : "分析结论";
}

function sectionText(result: AnalysisResult | null, kinds: ClaimKind[], legacy: string[]) {
  const claims = claimsOf(result, kinds);
  if (claims.length) return claims.map(claim => claim.text).join("\n");
  return legacy.filter(item => item && item !== "论文未明确说明").join("\n");
}

function PaperChat({ paperId, title }: { paperId: string; title: string }) {
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useSessionState<Turn[]>(`paper-qa:${paperId}`, []);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [turns, busy]);

  function send() {
    const question = draft.trim();
    if (!question || busy) return;
    const id = Date.now();
    const abort = new AbortController();
    abortRef.current?.abort();
    abortRef.current = abort;
    setBusy(true);
    setDraft("");
    setTurns(current => [...current, { id, question }]);
    askLibrary(question, [paperId], abort.signal)
      .then(response => setTurns(current => current.map(turn => turn.id === id ? { ...turn, response } : turn)))
      .catch((err: Error) => setTurns(current => abort.signal.aborted ? current.filter(turn => turn.id !== id) : current.map(turn => turn.id === id ? { ...turn, error: err.message || "提问失败" } : turn)))
      .finally(() => { if (abortRef.current === abort) setBusy(false); });
  }

  return <aside className="read-chat" aria-label="论文学习助手">
    <header>
      <CommentOutlined aria-hidden />
      <div><strong>论文学习助手</strong><span>当前论文 · RAG 问答</span></div>
    </header>
    <div className="read-chat-log" ref={box}>
      {!turns.length ? <p className="read-chat-empty">针对《{title}》提问。回答只使用这篇论文已入库的摘要或全文索引，并附上原文片段。</p> : null}
      {turns.map(turn => <div key={turn.id} className="read-turn">
        <p className="read-q">{turn.question}</p>
        {turn.error ? <Alert type="error" showIcon message={turn.error} /> : turn.response ? <div className="read-a">
          <p>{turn.response.answer}</p>
          {turn.response.evidence.map(item => <blockquote key={item.evidence_id}><span>{item.evidence_id}{item.section ? ` · ${item.section}` : ""}</span>{item.quote}</blockquote>)}
          {turn.response.coverage_gap ? <span className="small-print">这篇论文里没有找到足够证据，回答可能不完整。</span> : null}
        </div> : <p className="read-a pending">正在检索这篇论文的证据…</p>}
      </div>)}
    </div>
    <form className="read-composer" onSubmit={event => { event.preventDefault(); send(); }}>
      <textarea rows={2} maxLength={2000} value={draft} placeholder="继续提问……" aria-label="向这篇论文提问" disabled={busy} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} />
      <button type="submit" aria-label="发送" disabled={busy || !draft.trim()}><ArrowUpOutlined /></button>
    </form>
  </aside>;
}

export default function PaperReadPage() {
  const navigate = useNavigate();
  const { paperKey: key = "" } = useParams();
  const { id, project } = useWorkspaceProject();
  const [params] = useSearchParams();
  const parent = `/w/${id}/library`;
  const requestedBack = safeReturn(params.get("from"), parent);
  const back = [`/w/${id}`, parent, `/w/${id}/compare`, `/w/${id}/graph`].includes(requestedBack) ? requestedBack : parent;
  const stored = project?.papers[key];
  const [paper, setPaper] = useState<Paper | null>(stored?.paper ?? null);
  const [state, setState] = useState<AnalysisState | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [tab, setTab] = useState<"analysis" | "source">("analysis");
  const [open, setOpen] = useState("");
  useEffect(() => { document.title = paper ? `${paper.title} - 论文详情` : "论文详情"; }, [paper]);
  useEffect(() => { if (stored) { projects.select(id); projects.open(stored.paper); } }, [id, key]);
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    fetchPaper(key, controller.signal).then(setPaper).catch(() => undefined);
    return () => controller.abort();
  }, [key]);
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    let timer = 0;
    const follow = (data: AnalysisState) => {
      if (data.task && ACTIVE.has(data.task.status)) {
        timer = window.setTimeout(() => {
          fetchAnalysis(key, controller.signal).then(next => { if (!controller.signal.aborted) { setState(next); follow(next); } }).catch((err: Error) => { if (!controller.signal.aborted) setError(err.message || "分析状态更新失败，请刷新重试"); });
        }, 2000);
      }
    };
    fetchAnalysis(key, controller.signal).then(data => { if (!controller.signal.aborted) { setState(data); follow(data); } }).catch((err: Error) => { if (!controller.signal.aborted) setError(err.message || "分析结果加载失败"); });
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [key, starting]);

  if (!project) return <MissingWorkspace />;
  if (!stored || !paper) return <main className="page page-medium"><h1 className="page-title">这篇论文不在当前工作区</h1><Link to={`/w/${id}/library`}>返回论文库</Link></main>;

  const result = state?.result ?? null;
  const running = Boolean(state?.task && ACTIVE.has(state.task.status));
  const summary = claimsOf(result, ["summary"]).map(claim => claim.text).join("") || (result?.summary && result.summary !== "论文未明确说明" ? result.summary : "");
  const legacy: Record<string, string[]> = {
    "01": [result?.research_problem ?? "", result?.main_content ?? ""],
    "02": [result?.technical_method ?? ""],
    "03": result?.innovations ?? [],
    "04": [result?.experimental_results ?? "", result?.limitations ?? ""],
  };

  async function rerun() {
    setStarting(true); setError("");
    try { setState(await startAnalysis(key, Boolean(result))); }
    catch (err) { setError(err instanceof Error ? err.message : "分析失败，请重试"); }
    finally { setStarting(false); }
  }
  function exportAnalysis() {
    const lines = [`# ${paper!.title}`, "", summary && `> ${summary}`, ""];
    for (const group of GROUPS) lines.push(`## ${group.no} ${group.title}`, "", sectionText(result, group.kinds, legacy[group.no]) || "未分析", "");
    const blob = new Blob([lines.filter(line => line !== undefined).join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${paper!.title.slice(0, 40)}.md`; anchor.click();
    URL.revokeObjectURL(url);
  }
  const meta = [
    paper.authors.slice(0, 4).join(", ") || "作者未知",
    paper.year ? String(paper.year) : "年份未知",
    paper.venue || paper.sources.map(source => SOURCE_LABEL[source] ?? source).join("、") || "来源未知",
    paper.doi ? `DOI ${paper.doi}` : null,
  ].filter(Boolean);

  return <div className="read-page">
    <main className="read-main">
      <div className="read-navigation">
        <Link className="back-link" to={back}><ArrowLeftOutlined aria-hidden />{back === parent ? "返回论文库" : "返回上一级"}</Link>
        <p className="read-crumb"><Link to={`/w/${id}`}>{project.name}</Link><span>/</span><Link to={parent}>论文库</Link><span>/</span>论文详情</p>
      </div>
      <h1>论文标题：{paper.title}</h1>
      <p className="read-meta">{meta.join(" · ")}</p>
      <div className="read-flags">
        <span className={`read-flag ${result?.analysis_basis === "full_text" ? "is-ok" : ""}`}>{result?.analysis_basis === "full_text" ? "全文可用" : result ? "仅摘要" : "未分析"}</span>
        <span className={`read-flag ${result && !running ? "is-ok" : ""}`}>{running || starting ? "分析中" : result ? "分析完成" : "等待分析"}</span>
      </div>
      <div className="read-actions">
        <Button loading={starting || running} onClick={rerun}>{result ? "重新分析" : "开始分析"}</Button>
        <Button onClick={() => navigate(`/w/${id}/compare?pick=${encodeURIComponent(key)}`)}>加入论文对比</Button>
        <Button disabled={!result} onClick={exportAnalysis}>导出分析</Button>
      </div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {state?.task?.status === "failed" ? <Alert type="error" showIcon message={state.task.error || "分析失败，请重试"} /> : null}
      <div className="read-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "analysis"} onClick={() => setTab("analysis")}>AI 分析</button>
        <button type="button" role="tab" aria-selected={tab === "source"} onClick={() => setTab("source")}>原文阅读</button>
      </div>
      {tab === "source" ? <section className="read-source">
        <p>{paper.abstract || "这篇论文没有可用摘要。"}</p>
        {paper.pdf_url ? <p><a href={paper.pdf_url} target="_blank" rel="noopener noreferrer">打开 PDF 原文</a></p> : <p className="muted">没有可打开的 PDF 链接。分析依据来自已获取的摘要或全文文本，不在这里嵌入原文页面。</p>}
      </section> : <div className="read-cards">
        <article className="read-card is-summary">
          <h2>一句话摘要</h2>
          <p>{summary || (running ? "正在生成…" : "还没有分析结果。开始分析后，这里会概括研究问题、方法和结论。")}</p>
        </article>
        {GROUPS.map(group => {
          const claims = claimsOf(result, group.kinds);
          const text = sectionText(result, group.kinds, legacy[group.no]);
          const shown = open === group.no;
          return <article key={group.no} className="read-card">
            <h2><span>{group.no}</span>{group.title}</h2>
            <p>{text || (running || starting ? "正在生成…" : result ? "材料中未明确说明" : "尚未分析，请先开始分析。")}</p>
            <p className="read-source-line">来源：{claims.length ? sourceLabel(claims) : "尚无原文依据"}
              {claims.some(claim => claim.evidence.length) ? <button type="button" onClick={() => setOpen(shown ? "" : group.no)}>{shown ? "收起原文" : "查看原文"}</button> : null}
            </p>
            {shown ? <div className="read-quotes">{claims.flatMap(claim => claim.evidence).map((item, index) => <blockquote key={`${item.paragraph_id}-${index}`}>{item.quote}</blockquote>)}</div> : null}
          </article>;
        })}
      </div>}
    </main>
    <PaperChat paperId={key} title={paper.title} />
  </div>;
}

import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "antd";
import type { ReactNode } from "react";
import {
  ArrowLeftOutlined, ArrowUpOutlined, BarChartOutlined, BulbOutlined, ClockCircleOutlined, ExportOutlined, FileTextOutlined,
  PlayCircleOutlined, PlusSquareOutlined, ProfileOutlined, QuestionCircleOutlined, ReadOutlined, RobotOutlined, SettingOutlined,
  StarOutlined, SwapOutlined,
} from "@ant-design/icons";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { askLibrary, fetchPaper, startAnalysis } from "../../api";
import { useAnalysisPolling } from "../../useAnalysisPolling";
import { Abstract, formatAuthors } from "../../components/PaperRow";
import { AnalysisProgress } from "../../components/AnalysisProgress";
import { useSessionState } from "../../hooks";
import { projects } from "../../projects";
import type { AnalysisClaim, AnalysisResult, AnalysisState, ClaimKind, Paper, QAResponse } from "../../types";
import { ccfLabel, SOURCE_LABEL } from "../../types";
import { safeReturn } from "../../workspace";
import { MissingWorkspace, useWorkspaceProject } from "./shared";

const ACTIVE = new Set(["pending", "preparing", "analyzing"]);
const GROUPS: { no: string; title: string; hint: string; tone: string; icon: ReactNode; kinds: ClaimKind[] }[] = [
  { no: "02", title: "研究内容", hint: "研究的背景、研究问题、研究目标以及主要研究内容。", tone: "violet", icon: <ProfileOutlined />, kinds: ["research_problem", "main_content"] },
  { no: "03", title: "技术方案", hint: "论文提出的方法、模型结构、关键技术点和实现思路。", tone: "green", icon: <SettingOutlined />, kinds: ["technical_method", "method_step"] },
  { no: "04", title: "创新点与主要贡献", hint: "相比现有工作的创新之处，以及理论或应用方面的主要贡献。", tone: "amber", icon: <BulbOutlined />, kinds: ["innovation"] },
  { no: "05", title: "实验结果与局限性", hint: "实验设置、主要实验结果、消融分析，以及存在的局限和未来工作。", tone: "rose", icon: <BarChartOutlined />, kinds: ["evaluation", "limitation"] },
];
const PROMPTS: { title: string; hint: string; tone: string; icon: ReactNode; question: string }[] = [
  { title: "解释论文内容", hint: "用通俗的语言解释这篇论文的核心内容", tone: "blue", icon: <FileTextOutlined />, question: "请用通俗的语言解释这篇论文的核心内容。" },
  { title: "总结创新点", hint: "这篇论文相比以往工作有哪些创新点？", tone: "amber", icon: <BulbOutlined />, question: "这篇论文相比以往工作有哪些创新点？" },
  { title: "分析实验结果", hint: "实验结果如何？有什么局限性？", tone: "rose", icon: <BarChartOutlined />, question: "这篇论文的实验结果如何？有什么局限性？" },
  { title: "对比相关工作", hint: "与其他相关工作有什么区别？", tone: "blue", icon: <SwapOutlined />, question: "这篇论文和其他相关工作有什么区别？" },
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
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [turns, busy]);

  function send(text = draft) {
    const question = text.trim();
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
      <span className="read-chat-mark" aria-hidden><RobotOutlined /></span>
      <strong>论文学习助手</strong>
      <span className="read-chat-badge">RAG 问答</span>
    </header>
    <div className="read-chat-log" ref={box}>
      {!turns.length ? <>
        <p className="read-chat-empty">基于《{title}》的内容提供解读、分析与对比。回答只使用这篇论文已入库的摘要或全文索引，并附上原文片段。</p>
        <div className="read-prompts">
          {PROMPTS.map(item => <button key={item.title} type="button" disabled={busy} onClick={() => send(item.question)}>
            <span className={`read-ico is-${item.tone}`} aria-hidden>{item.icon}</span>
            <span><strong>{item.title}</strong><small>{item.hint}</small></span>
          </button>)}
          <button type="button" onClick={() => input.current?.focus()}>
            <span className="read-ico is-amber" aria-hidden><QuestionCircleOutlined /></span>
            <span><strong>其他问题</strong><small>你可以基于这篇论文提出任何问题</small></span>
          </button>
        </div>
      </> : null}
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
      <textarea ref={input} rows={2} maxLength={2000} value={draft} placeholder="继续提问这篇论文…" aria-label="向这篇论文提问" disabled={busy} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} />
      <button type="submit" aria-label="发送" disabled={busy || !draft.trim()}><ArrowUpOutlined /></button>
    </form>
    <p className="read-chat-note">基于论文内容进行回答，可能包含 AI 生成内容，请注意核实。</p>
  </aside>;
}

export default function PaperReadPage() {
  const navigate = useNavigate();
  const { paperKey: key = "" } = useParams();
  const { id, project } = useWorkspaceProject();
  const [params] = useSearchParams();
  const parent = `/w/${id}/library`;
  const requestedBack = safeReturn(params.get("from"), parent);
  const backPath = requestedBack.split("?")[0];
  const back = [`/w/${id}`, parent, `/w/${id}/compare`, `/w/${id}/graph`, "/graph"].includes(backPath) ? requestedBack : parent;
  const stored = project?.papers[key];
  const [paper, setPaper] = useState<Paper | null>(stored?.paper ?? null);
  const [state, setState] = useState<AnalysisState | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const connectionError = useAnalysisPolling(key, starting, setState);
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

  if (!project) return <MissingWorkspace />;
  if (!stored || !paper) return <main className="page page-medium"><h1 className="page-title">这篇论文不在当前工作区</h1><Link to={`/w/${id}/library`}>返回论文库</Link></main>;

  const result = state?.result ?? null;
  const running = Boolean(state?.task && ACTIVE.has(state.task.status));
  const summary = claimsOf(result, ["summary"]).map(claim => claim.text).join("") || (result?.summary && result.summary !== "论文未明确说明" ? result.summary : "");
  const legacy: Record<string, string[]> = {
    "02": [result?.research_problem ?? "", result?.main_content ?? ""],
    "03": [result?.technical_method ?? ""],
    "04": result?.innovations ?? [],
    "05": [result?.experimental_results ?? "", result?.limitations ?? ""],
  };

  async function rerun() {
    setStarting(true); setError("");
    try { setState(await startAnalysis(key, Boolean(result))); }
    catch (err) { setError(err instanceof TypeError ? "提交时网络连接中断，请以重新获取的任务进度为准。" : err instanceof Error ? err.message : "分析失败，请重试"); }
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
  const busy = running || starting;
  const sources = paper.sources.map(source => SOURCE_LABEL[source] ?? source);
  const keywords = claimsOf(result, ["keyword"]).map(claim => claim.text);
  const tags = [...new Set([ccfLabel(paper), ...sources, ...(keywords.length ? keywords : result?.keywords ?? [])])]
    .filter((item): item is string => Boolean(item) && item !== "论文未明确说明").slice(0, 8);
  const facts = [paper.year ? String(paper.year) : "年份未知", paper.venue, paper.doi ? `DOI ${paper.doi}` : null].filter(Boolean);
  const pill = (text: string) => busy
    ? <span className="read-pill is-blue">分析中</span>
    : text ? <span className="read-pill is-green">已分析</span>
    : <span className="read-pill"><ClockCircleOutlined aria-hidden />{result ? "未说明" : "尚未分析"}</span>;

  return <div className="read-page">
    <main className="read-main">
      <div className="read-navigation">
        <Link className="back-link" to={back}><ArrowLeftOutlined aria-hidden />{back === parent ? "返回论文库" : "返回上一级"}</Link>
        <p className="read-crumb"><Link to={`/w/${id}`}>{project.name}</Link><span>/</span><Link to={parent}>论文库</Link><span>/</span>论文详情</p>
      </div>
      <header className="read-hero">
        <div className="read-thumb" aria-hidden><i /><i /><i /><i /><i /><b>{sources[0] ?? "PDF"}</b></div>
        <div className="read-hero-body">
          <h1>{paper.title}</h1>
          <p className="read-authors">{formatAuthors(paper.authors, 6)}</p>
          <p className="read-meta">{facts.join(" · ")}</p>
          <Abstract text={paper.abstract} lines={2} />
        </div>
      </header>
      <div className="read-flags">
        <span className={`read-pill ${result?.analysis_basis === "full_text" ? "is-green" : "is-amber"}`}>{result?.analysis_basis === "full_text" ? "全文可用" : result ? "仅摘要" : "未分析"}</span>
        {tags.length ? <i className="read-split" aria-hidden /> : null}
        {tags.map(item => <span key={item} className="read-tag">{item}</span>)}
      </div>
      <AnalysisProgress status={state?.task?.status ?? (state ? result ? "success" : "idle" : error ? "unavailable" : "loading")} hasResult={Boolean(result)} starting={starting} />
      <div className="read-actions">
        <Button type={result ? "default" : "primary"} icon={<PlayCircleOutlined />} loading={busy} onClick={rerun}>{result ? "重新分析" : "开始分析"}</Button>
        <Button icon={<PlusSquareOutlined />} onClick={() => navigate(`/w/${id}/compare?pick=${encodeURIComponent(key)}`)}>加入论文对比</Button>
        <Button icon={<ExportOutlined />} disabled={!result} onClick={exportAnalysis}>导出分析</Button>
      </div>
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {connectionError ? <Alert type="warning" showIcon message={connectionError} /> : null}
      {state?.task?.status === "failed" ? <Alert type="error" showIcon message={state.task.error || "分析失败，请重试"} /> : null}
      <div className="read-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === "analysis"} onClick={() => setTab("analysis")}><StarOutlined aria-hidden />AI 分析</button>
        <button type="button" role="tab" aria-selected={tab === "source"} onClick={() => setTab("source")}><ReadOutlined aria-hidden />原文阅读</button>
      </div>
      {tab === "source" ? <section className="read-source">
        <p>{paper.abstract || "这篇论文没有可用摘要。"}</p>
        {paper.pdf_url ? <p><a href={paper.pdf_url} target="_blank" rel="noopener noreferrer">打开 PDF 原文</a></p> : <p className="muted">没有可打开的 PDF 链接。分析依据来自已获取的摘要或全文文本，不在这里嵌入原文页面。</p>}
      </section> : <div className="read-cards">
        <article className="read-card">
          <header>
            <span className="read-ico is-blue" aria-hidden><FileTextOutlined /></span>
            <div><h2><span>01</span>一句话摘要</h2><p>基于论文内容生成的简明摘要，帮助你快速了解研究的核心内容、方法和结论。</p></div>
            {pill(summary)}
          </header>
          {summary || busy ? <p className="read-card-text">{summary || "正在生成…"}</p> : null}
        </article>
        {GROUPS.map(group => {
          const claims = claimsOf(result, group.kinds);
          const text = sectionText(result, group.kinds, legacy[group.no]);
          const shown = open === group.no;
          return <article key={group.no} className="read-card">
            <header>
              <span className={`read-ico is-${group.tone}`} aria-hidden>{group.icon}</span>
              <div><h2><span>{group.no}</span>{group.title}</h2><p>{group.hint}</p></div>
              {pill(text)}
            </header>
            {text || busy || result ? <>
              <p className="read-card-text">{text || (busy ? "正在生成…" : "材料中未明确说明")}</p>
              {text ? <p className="read-source-line">来源：{claims.length ? sourceLabel(claims) : "尚无原文依据"}
                {claims.some(claim => claim.evidence.length) ? <button type="button" onClick={() => setOpen(shown ? "" : group.no)}>{shown ? "收起原文" : "查看原文"}</button> : null}
              </p> : null}
            </> : null}
            {shown ? <div className="read-quotes">{claims.flatMap(claim => claim.evidence).map((item, index) => <blockquote key={`${item.paragraph_id}-${index}`}>{item.quote}</blockquote>)}</div> : null}
          </article>;
        })}
      </div>}
    </main>
    <PaperChat paperId={key} title={paper.title} />
  </div>;
}

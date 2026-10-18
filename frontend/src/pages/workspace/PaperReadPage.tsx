import { useEffect, useState } from "react";
import { Alert, Button } from "antd";
import type { ReactNode } from "react";
import {
  ArrowLeftOutlined, BarChartOutlined, BulbOutlined, ClockCircleOutlined, ExportOutlined, FileTextOutlined,
  PlayCircleOutlined, PlusSquareOutlined, ProfileOutlined, ReadOutlined, SettingOutlined,
  StarOutlined,
} from "@ant-design/icons";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { fetchPaper, prepareWorkspace, startAnalysis } from "../../api";
import { useAnalysisPolling } from "../../useAnalysisPolling";
import { Abstract, formatAuthors } from "../../components/PaperRow";
import { AnalysisProgress } from "../../components/AnalysisProgress";
import { projects } from "../../projects";
import type { AnalysisClaim, AnalysisResult, AnalysisState, ClaimKind, Paper } from "../../types";
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

export default function PaperReadPage() {
  const navigate = useNavigate();
  const { paperKey: key = "" } = useParams();
  const { id, project } = useWorkspaceProject();
  const [params] = useSearchParams();
  const parent = `/w/${id}/library`;
  const requestedBack = safeReturn(params.get("from"), parent);
  const backPath = requestedBack.split("?")[0];
  const back = [`/w/${id}`, parent, `/w/${id}/compare`, `/w/${id}/studio`, `/w/${id}/graph`, "/graph"].includes(backPath) ? requestedBack : parent;
  const stored = project?.papers[key];
  const [paper, setPaper] = useState<Paper | null>(stored?.paper ?? null);
  const [state, setState] = useState<AnalysisState | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [openingChat, setOpeningChat] = useState(false);
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
  async function openChat() {
    if (openingChat || !project || !paper) return;
    setOpeningChat(true); setError("");
    try {
      await prepareWorkspace(id, project.name, Object.keys(project.papers));
      navigate(`/w/${id}/research?papers=${encodeURIComponent(key)}`);
    } catch (err) { setError(err instanceof Error ? err.message : "新建对话失败"); }
    finally { setOpeningChat(false); }
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
        <Button loading={openingChat} onClick={openChat}>与研究助手讨论</Button>
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
  </div>;
}

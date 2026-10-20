import { Fragment, useEffect, useState } from "react";
import { Alert, Button, Checkbox, Collapse, Switch } from "antd";
import { fetchAnalysis, startAnalysis } from "../api";
import type { AnalysisClaim, AnalysisResult, AnalysisState, ClaimKind } from "../types";

const ACTIVE = new Set(["pending", "preparing", "analyzing"]);
const UNKNOWN = "论文未明确说明";
const STEPS = [["pending", "排队"], ["preparing", "准备文本"], ["analyzing", "模型分析"], ["success", "完成"]] as const;

export interface EvidenceView {
  key: string;
  section: string;
  claim: string;
  refs: { paragraph_id: string; quote: string; paragraph?: { section: string; text: string } }[];
}

const BLOCKS: [ClaimKind, string][] = [
  ["research_problem", "研究背景与核心问题"],
  ["main_content", "主要研究内容"],
  ["technical_method", "技术方案"],
  ["method_step", "技术步骤与研究过程"],
  ["innovation", "创新点（作者声称）"],
  ["evaluation", "实验与验证"],
  ["limitation", "局限与未来工作"],
];

export type Tone = "problem" | "method" | "contribution" | "result";
export const TONES: { key: Tone; label: string; hint: string }[] = [
  { key: "problem", label: "研究问题", hint: "背景、目标与主要研究内容，蓝色" },
  { key: "method", label: "方法", hint: "技术方案与步骤，橙色" },
  { key: "contribution", label: "作者声称的贡献", hint: "创新点，紫色" },
  { key: "result", label: "实验结果与局限", hint: "实验验证、局限与未来工作，粉色" },
];
const TONE_OF: Partial<Record<ClaimKind, Tone>> = {
  research_problem: "problem", main_content: "problem", technical_method: "method", method_step: "method",
  innovation: "contribution", evaluation: "result", limitation: "result",
};
export interface Skim { on: boolean; tones: Tone[] }
export const ALL_TONES: Tone[] = TONES.map(t => t.key);

export function SkimPanel({ result, value, onChange }: { result: AnalysisResult; value: Skim; onChange: (value: Skim) => void }) {
  const count = (tone: Tone) => result.claims?.filter(c => c.status === "supported" && TONE_OF[c.kind] === tone).length ?? 0;
  return <section className="skim-panel" aria-label="速读高亮">
    <div className="skim-head"><h2>速读高亮</h2><Switch checked={value.on} onChange={on => onChange({ ...value, on })} aria-label="启用速读高亮" /></div>
    <ul className="skim-list">{TONES.map(tone => <li key={tone.key}>
      <Checkbox disabled={!value.on} checked={value.tones.includes(tone.key)}
        onChange={e => onChange({ ...value, tones: e.target.checked ? [...value.tones, tone.key] : value.tones.filter(t => t !== tone.key) })}>
        <span className={`skim-dot tone-${tone.key}`} />{tone.label}（{count(tone.key)} 条）
      </Checkbox>
      <span className="small-print">{tone.hint}</span>
    </li>)}</ul>
  </section>;
}

function toView(key: string, section: string, claim: AnalysisClaim, result: AnalysisResult): EvidenceView {
  const sources = new Map(result.source_paragraphs?.map(p => [p.id, p]));
  return { key, section, claim: claim.text, refs: claim.evidence.map(ref => ({ ...ref, paragraph: sources.get(ref.paragraph_id) })) };
}

export function EvidenceList({ view }: { view: EvidenceView }) {
  return <div className="evidence-list">
    {view.refs.map((ref, index) => <figure key={`${ref.paragraph_id}-${index}`} className="evidence-item">
      <figcaption>{ref.paragraph?.section || "原文"} · 段落 {ref.paragraph_id}</figcaption>
      <blockquote>{ref.quote}</blockquote>
      {ref.paragraph && ref.paragraph.text.trim() !== ref.quote.trim() ? <details><summary>查看所在段落</summary><p>{ref.paragraph.text}</p></details> : null}
    </figure>)}
  </div>;
}

interface ClaimProps { claim: AnalysisClaim; result: AnalysisResult; section: string; id: string; mode: "aside" | "inline"; active: string; onEvidence: (view: EvidenceView | null) => void }

function Claim({ claim, result, section, id, mode, active, onEvidence }: ClaimProps) {
  const [open, setOpen] = useState(false);
  const selected = mode === "aside" ? active === id : open;
  const details = claim.kind === "evaluation" ? [
    ["数据集／对象", claim.dataset], ["比较对象", claim.baseline], ["指标", claim.metric], ["结果", claim.result], ["成立条件", claim.conditions],
  ].filter(([, value]) => value) : [];
  const view = toView(id, section, claim, result);
  return <div className={`claim${selected ? " is-active" : ""}`} data-tone={TONE_OF[claim.kind]}>
    <p className="claim-text">{claim.text}</p>
    {details.length ? <dl className="claim-fields">{details.map(([label, value]) => <Fragment key={label}><dt>{label}</dt><dd>{value}</dd></Fragment>)}</dl> : null}
    {claim.evidence.length
      ? <button type="button" className="evidence-toggle" aria-expanded={selected} aria-controls={mode === "aside" ? "evidence-pane" : undefined}
          onClick={() => mode === "aside" ? onEvidence(selected ? null : view) : setOpen(value => !value)}>
          {selected ? "收起依据" : `原文依据 ${claim.evidence.length}`}
        </button>
      : <span className="evidence-none">未附原文依据</span>}
    {mode === "inline" && open ? <EvidenceList view={view} /> : null}
  </div>;
}

function StructuredView({ result, mode, active, onEvidence }: { result: AnalysisResult; mode: "aside" | "inline"; active: string; onEvidence: (view: EvidenceView | null) => void }) {
  const supported = (kind: ClaimKind) => result.claims?.filter(c => c.kind === kind && c.status === "supported") || [];
  const summary = supported("summary");
  const keywords = supported("keyword");
  return <>
    {summary.length ? <div className="analysis-summary">
      <h3>速读摘要</h3>
      {summary.map((claim, i) => <Claim key={i} id={`summary-${i}`} claim={claim} result={result} section="速读摘要" mode={mode} active={active} onEvidence={onEvidence} />)}
    </div> : null}
    {result.paper_type ? <p className="analysis-type">论文类型：{result.paper_type}</p> : null}
    {BLOCKS.map(([kind, title]) => {
      const claims = supported(kind);
      const items = claims.map((claim, i) => <Claim key={i} id={`${kind}-${i}`} claim={claim} result={result} section={title} mode={mode} active={active} onEvidence={onEvidence} />);
      return <section key={kind} className="analysis-block" aria-label={title}>
        <h3>{title}</h3>
        {!claims.length ? <p className="muted">材料中未明确说明</p> : kind === "method_step" ? <ol className="claim-steps">{items.map((item, i) => <li key={i}>{item}</li>)}</ol> : items}
      </section>;
    })}
    {keywords.length ? <section className="analysis-block" aria-label="关键词">
      <h3>关键词</h3>
      <p className="keyword-line">{keywords.map(claim => claim.text).join("，")}</p>
    </section> : null}
  </>;
}

function LegacyView({ result }: { result: AnalysisResult }) {
  const rows: [string, string | string[]][] = [
    ["研究问题", result.research_problem], ["主要内容", result.main_content], ["技术方法", result.technical_method],
    ["创新点", result.innovations], ["实验结果", result.experimental_results], ["局限性", result.limitations], ["关键词", result.keywords.join("，")],
  ];
  return <>
    <Alert type="info" showIcon message="这是旧版分析结果，没有逐条原文依据。重新分析可生成速读摘要、技术步骤和原文依据。" />
    {rows.map(([title, value]) => <section key={title} className="analysis-block"><h3>{title}</h3>
      {Array.isArray(value) ? <ul>{value.map((item, i) => <li key={i} className={item === UNKNOWN ? "muted" : undefined}>{item}</li>)}</ul>
        : <p className={value === UNKNOWN ? "muted" : undefined}>{value}</p>}
    </section>)}
  </>;
}

export function basisOf(result: AnalysisResult) {
  if (result.analysis_basis === "full_text") return { tone: "success", label: result.truncated ? "全文分析 · 部分章节" : "全文分析" };
  return { tone: "warning", label: "摘要级分析" };
}

export function AnalysisPanel({ paperId, onImported, mode, active, onEvidence, onResult }: {
  paperId: string; onImported: () => void; mode: "aside" | "inline"; active: string;
  onEvidence: (view: EvidenceView | null) => void; onResult: (result: AnalysisResult | null) => void;
}) {
  const [state, setState] = useState<AnalysisState | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => { setState(null); setError(""); }, [paperId]);
  useEffect(() => { onResult(state?.result ?? null); }, [state?.result, onResult]);

  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    let imported = false;
    const follow = (data: AnalysisState) => {
      const status = data.task?.status;
      if (status && status !== "pending" && status !== "preparing" && !imported) {
        imported = true;
        onImported();
      }
      if (status && ACTIVE.has(status)) {
        timer = window.setTimeout(() => {
          fetchAnalysis(paperId, controller.signal).then(next => {
            if (controller.signal.aborted) return;
            setState(next);
            follow(next);
          }).catch((err: Error) => {
            if (!controller.signal.aborted) setError(err.message || "分析结果加载失败");
          });
        }, 2000);
      }
    };
    fetchAnalysis(paperId, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      setState(data);
      setError("");
      follow(data);
    }).catch((err: Error) => {
      if (!controller.signal.aborted) setError(err.message || "分析结果加载失败");
    });
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [paperId, version, onImported]);

  async function run(reanalyze: boolean) {
    setStarting(true);
    setError("");
    try {
      setState(await startAnalysis(paperId, reanalyze));
      onEvidence(null);
      setVersion(value => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "分析失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  const task = state?.task;
  const result = state?.result ?? null;
  const running = Boolean(task && ACTIVE.has(task.status));
  const waiting = state === null && !error;
  const stepIndex = STEPS.findIndex(([key]) => key === task?.status);
  const basis = result ? basisOf(result) : null;

  return <section className="reading-section" id="analysis" aria-labelledby="analysis-title">
    <div className="section-head">
      <h2 id="analysis-title">AI 分析</h2>
      {basis ? <span className={`status status-${basis.tone}`}>{basis.label}</span> : null}
      <div className="section-actions">
        {result || task?.status === "failed"
          ? <Button disabled={waiting || running} loading={starting} onClick={() => run(Boolean(result))}>{task?.status === "failed" && !result ? "重试分析" : "重新分析"}</Button>
          : <Button type="primary" disabled={waiting || running} loading={starting || waiting} onClick={() => run(false)}>开始分析</Button>}
      </div>
    </div>
    {running ? <ol className="task-steps" aria-label="分析进度" aria-live="polite">
      {STEPS.map(([key, label], index) => <li key={key} className={index < stepIndex ? "done" : index === stepIndex ? "current" : ""}>{label}</li>)}
    </ol> : null}
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => setVersion(v => v + 1)}>重试</Button>} /> : null}
    {task?.status === "failed" && task.error ? <Alert type="error" showIcon message={`上次分析失败：${task.error}`} /> : null}
    {!result && !running && !waiting && !error && task?.status !== "failed" ? <p className="empty-hint">尚未分析。分析会先尝试获取全文；拿不到全文时只基于摘要，结果会标注为“摘要级分析”。</p> : null}
    {result ? <>
      <p className="analysis-basis">
        {result.analysis_basis === "full_text"
          ? <>基于可获取的全文文本{result.truncated ? "的部分章节" : ""}；图表、公式和附录可能未完整解析。</>
          : <>仅基于摘要：{result.degradation_reason || "没有使用全文"}。结论覆盖面有限。</>}
        <span className="muted"> {new Date(result.generated_at).toLocaleString("zh-CN")} · {result.model_name} · 提示词 {result.prompt_version}。AI 可能出错，重要结论请对照原文依据。</span>
      </p>
      {result.analysis_warnings?.map(warning => <Alert key={warning} type="warning" showIcon message={warning} />)}
      {result.claims?.length ? <StructuredView result={result} mode={mode} active={active} onEvidence={onEvidence} /> : <LegacyView result={result} />}
      {result.evidence_sections.length ? <Collapse ghost className="coverage" items={[{
        key: "coverage", label: `分析覆盖的文本章节（${result.evidence_sections.length}）`,
        children: <>
          {result.source_url ? <p className="muted">文本来源：<a href={result.source_url} target="_blank" rel="noopener noreferrer">{result.source_url}</a></p> : null}
          {result.evidence_sections.map((item, index) => <div key={`${index}-${item.title}`} className="coverage-item"><strong>{item.title}</strong><p>{item.excerpt}</p></div>)}
        </>,
      }]} /> : null}
    </> : null}
  </section>;
}

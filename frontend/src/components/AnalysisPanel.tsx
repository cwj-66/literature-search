import { useEffect, useState } from "react";
import { Alert, Button, Collapse, Steps, Tag } from "antd";
import { fetchAnalysis, startAnalysis } from "../api";
import type { AnalysisClaim, AnalysisResult, AnalysisState, ClaimKind } from "../types";

const ACTIVE = new Set(["pending", "preparing", "analyzing"]);
const UNKNOWN = "论文未明确说明";

function stepOf(status?: string) {
  if (status === "analyzing") return { current: 1, status: "process" as const };
  if (status === "failed") return { current: 2, status: "error" as const };
  if (status === "success") return { current: 2, status: "finish" as const };
  return { current: 0, status: "process" as const };
}

function TextBlock({ title, value }: { title: string; value: string }) {
  return <>
    <h3>{title}</h3>
    <p className={value === UNKNOWN ? "meta" : undefined}>{value}</p>
  </>;
}

function ClaimView({ claim, result }: { claim: AnalysisClaim; result: AnalysisResult }) {
  const sources = new Map(result.source_paragraphs?.map((p) => [p.id, p]));
  const details = claim.kind === "evaluation" ? [
    ["数据集／研究对象", claim.dataset], ["比较对象", claim.baseline], ["指标", claim.metric],
    ["结果", claim.result], ["成立条件", claim.conditions],
  ] : [];
  return <div className="analysis-claim">
    <p>{claim.text}</p>
    {details.length ? <dl className="evaluation-fields">{details.map(([label, value]) => <div key={label}>
      <dt>{label}</dt><dd>{value || "提供的材料中未明确说明"}</dd>
    </div>)}</dl> : null}
    {claim.evidence.length ? <Collapse size="small" items={[{
      key: "source", label: "查看原文依据",
      children: claim.evidence.map((ref, index) => {
        const paragraph = sources.get(ref.paragraph_id);
        return <div key={`${ref.paragraph_id}-${index}`}>
          <strong>{paragraph?.section || "原文"} · {ref.paragraph_id}</strong>
          <blockquote>{ref.quote}</blockquote>
          {paragraph ? <details><summary>查看所在段落</summary><p>{paragraph.text}</p></details> : null}
        </div>;
      }),
    }]} /> : <p className="meta">提供的材料中未明确说明</p>}
  </div>;
}

function StructuredView({ result }: { result: AnalysisResult }) {
  const blocks: [ClaimKind, string][] = [
    ["summary", "中文速读摘要"], ["research_problem", "研究背景与核心问题"],
    ["main_content", "主要研究内容"], ["technical_method", "技术方案概述"],
    ["method_step", "技术步骤／研究过程"], ["innovation", "创新点（作者声称的贡献）"],
    ["evaluation", "实验或验证"], ["limitation", "作者说明的局限与未来工作"], ["keyword", "关键词"],
  ];
  return <>
    <p className="analysis-paper-type">论文类型：{result.paper_type || "未知"}</p><div className="analysis-sections">
    {blocks.map(([kind, title]) => {
      const claims = result.claims?.filter((c) => c.kind === kind && c.status === "supported") || [];
      return <section key={kind} aria-label={title} className={`analysis-section analysis-${kind}`}>
        <h3>{title}</h3>
        {!claims.length ? <p className="meta">提供的材料中未明确说明</p> : null}
        {kind === "keyword" && claims.length ? <><div className="tag-row">{claims.map((claim, i) => <Tag key={i}>{claim.text}</Tag>)}</div><Collapse size="small" items={[{ key: "keywords", label: "查看关键词依据", children: claims.map((claim, i) => <ClaimView key={i} claim={claim} result={result} />) }]} /></>
          : kind === "method_step" ? <ol>{claims.map((claim, i) => <li key={i}><ClaimView claim={claim} result={result} /></li>)}</ol>
          : claims.map((claim, i) => <ClaimView key={i} claim={claim} result={result} />)}
      </section>;
    })}</div>
  </>;
}

function ResultView({ result }: { result: AnalysisResult }) {
  const full = result.analysis_basis === "full_text";
  const structured = Boolean(result.claims?.length);
  return <>
    <Alert
      style={{ marginTop: 12 }}
      type={full ? "info" : "warning"}
      showIcon
      message={full ? (result.truncated ? "基于全文部分章节分析" : "基于可获取的全文文本分析") : "仅基于摘要分析"}
      description={full ? "分析范围以实际解析的文本为准，图表、公式或附录可能未被完整解析。" : (result.degradation_reason || "没有使用全文")}
    />
    <p className="meta">
      生成时间：{new Date(result.generated_at).toLocaleString("zh-CN")} · 模型：{result.model_name} · 提示词 {result.prompt_version}
    </p>
    <p className="meta">AI 分析可能有误，请通过原文依据核对重要结论。</p>
    {result.analysis_warnings?.map((warning) => <Alert style={{ marginTop: 12 }} key={warning} type="warning" showIcon message={warning} />)}
    {structured ? <StructuredView result={result} /> : <>
    <Alert type="info" message="这是旧版分析结果，重新分析可生成中文摘要、技术步骤和逐条原文依据。" />
    <TextBlock title="研究问题" value={result.research_problem} />
    <TextBlock title="主要内容" value={result.main_content} />
    <TextBlock title="技术方法" value={result.technical_method} />
    <h3>创新点</h3>
    <ul>
      {result.innovations.map((item, index) => <li key={`${index}-${item}`} className={item === UNKNOWN ? "meta" : undefined}>{item}</li>)}
    </ul>
    <TextBlock title="实验结果" value={result.experimental_results} />
    <TextBlock title="局限性" value={result.limitations} />
    <h3>关键词</h3>
    <div className="tag-row">{result.keywords.map((item, index) => <Tag key={`${index}-${item}`}>{item}</Tag>)}</div>
    </>}
    {result.evidence_sections.length ? (
      <Collapse style={{ marginTop: 12 }} items={[{
        key: "evidence",
        label: "文本来源与分析覆盖章节",
        children: <>
          {result.source_url ? <p className="meta">文本来源：{result.source_url}</p> : null}
          {result.evidence_sections.map((item, index) => (
            <div key={`${index}-${item.title}`}>
              <strong>{item.title}</strong>
              <p className="meta">{item.excerpt}</p>
            </div>
          ))}
        </>,
      }]} />
    ) : null}
  </>;
}

export function AnalysisPanel({ paperId, onImported }: { paperId: string; onImported: () => void }) {
  const [state, setState] = useState<AnalysisState | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    setState(null);
    setError("");
  }, [paperId]);

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
          fetchAnalysis(paperId, controller.signal).then((next) => {
            if (controller.signal.aborted) return;
            setState(next);
            follow(next);
          }).catch((err: Error) => {
            if (!controller.signal.aborted) setError(err.message || "分析结果加载失败");
          });
        }, 2000);
      }
    };
    fetchAnalysis(paperId, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      setState(data);
      setError("");
      follow(data);
    }).catch((err: Error) => {
      if (!controller.signal.aborted) setError(err.message || "分析结果加载失败");
    });
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [paperId, version, onImported]);

  async function run(reanalyze: boolean) {
    setStarting(true);
    setError("");
    try {
      setState(await startAnalysis(paperId, reanalyze));
      setVersion((value) => value + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "分析失败，请重试");
    } finally {
      setStarting(false);
    }
  }

  const task = state?.task;
  const result = state?.result ?? null;
  const active = Boolean(task && ACTIVE.has(task.status));
  const waiting = state === null && !error;
  const label = task?.status === "failed" ? "重试" : result ? "重新分析" : "AI 分析";
  const steps = task ? stepOf(task.status) : null;

  return (
    <section className="paper-card analysis-block" id="analysis" aria-label="AI 分析">
      <div className="analysis-heading"><div><span className="page-kicker">结构化研读</span><h2>AI 内容分析</h2><p className="meta">梳理研究问题、方法与贡献，并查看对应原文依据。</p></div><div className="analysis-actions">
        <Button type="primary" disabled={waiting || active} loading={starting || waiting} onClick={() => run(Boolean(result))}>
          {label}
        </Button>
      </div></div>
      {steps ? (
        <Steps size="small" current={steps.current} status={steps.status} items={[
          { title: "准备文本" },
          { title: "分析中" },
          { title: task?.status === "failed" ? "失败" : "完成" },
        ]} />
      ) : null}
      {error ? (
        <Alert
          style={{ marginTop: 12 }}
          type="error"
          showIcon
          message={error}
          action={<Button size="small" onClick={() => setVersion((value) => value + 1)}>重试</Button>}
        />
      ) : null}
      {task?.status === "failed" && task.error ? <Alert style={{ marginTop: 12 }} type="error" showIcon message={task.error} /> : null}
      {result ? <ResultView result={result} /> : null}
    </section>
  );
}

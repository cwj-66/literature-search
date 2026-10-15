import { useEffect, useState } from "react";
import { Alert, Button, Collapse, Steps, Tag } from "antd";
import { fetchAnalysis, startAnalysis } from "../api";
import type { AnalysisResult, AnalysisState } from "../types";

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

function ResultView({ result }: { result: AnalysisResult }) {
  const full = result.analysis_basis === "full_text";
  return <>
    <Alert
      style={{ marginTop: 12 }}
      type={full ? "info" : "warning"}
      showIcon
      message={full ? "基于全文" : "仅基于摘要"}
      description={full ? "已按章节选取方法、实验和局限性等相关部分。" : (result.degradation_reason || "没有使用全文")}
    />
    <p className="meta">
      生成时间：{new Date(result.generated_at).toLocaleString("zh-CN")} · 模型：{result.model_name} · 提示词 {result.prompt_version}
    </p>
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
    {result.evidence_sections.length ? (
      <Collapse style={{ marginTop: 12 }} items={[{
        key: "evidence",
        label: "分析依据摘录",
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
    <section className="paper-card analysis-block" aria-label="AI 分析">
      <h2>AI 分析</h2>
      <div className="analysis-actions">
        <Button type="primary" disabled={waiting || active} loading={starting || waiting} onClick={() => run(Boolean(result))}>
          {label}
        </Button>
      </div>
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

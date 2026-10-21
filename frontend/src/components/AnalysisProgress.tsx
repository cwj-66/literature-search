import { CheckOutlined, CloseOutlined, LoadingOutlined } from "@ant-design/icons";

const STAGES = ["未分析", "排队中", "准备材料", "分析中", "分析完成"];
const STAGE_INDEX: Record<string, number> = { idle: 0, pending: 1, preparing: 2, analyzing: 3, success: 4 };
const HINTS: Record<string, string> = {
  loading: "正在读取任务状态…",
  idle: "这篇论文尚无分析结果；新入库论文会在后台自动分析。",
  pending: "任务已提交，正在等待处理。",
  preparing: "正在获取并整理论文材料。",
  analyzing: "正在提取结论并核对原文依据，请稍候。",
  success: "分析结果已保存，可以查看原文依据或导出分析。",
  failed: "本次分析未完成。",
  unavailable: "暂时无法读取进度，请刷新重试。",
};

export function AnalysisProgress({ status, hasResult, starting }: { status: string; hasResult: boolean; starting: boolean }) {
  const phase = starting ? "pending" : status;
  const failed = phase === "failed";
  const index = failed ? 4 : STAGE_INDEX[phase] ?? -1;
  const busy = ["loading", "pending", "preparing", "analyzing"].includes(phase);
  const label = failed ? "分析失败" : STAGES[index] ?? (phase === "loading" ? "读取进度" : "进度暂不可用");

  return <section className={`analysis-progress${failed ? " is-failed" : ""}${phase === "success" ? " is-complete" : ""}`} aria-label="论文分析进度" aria-busy={busy}>
    <div className="analysis-progress-head" role="status" aria-live="polite">
      <strong>{busy ? <LoadingOutlined aria-hidden /> : null}{label}</strong>
      <span>{failed && hasResult ? "本次分析失败，上次保存的结果仍保留。" : HINTS[phase] ?? HINTS.unavailable}</span>
    </div>
    <ol className="analysis-progress-stages">
      {STAGES.map((name, step) => {
        const done = !failed && index >= 0 && step < index;
        const current = step === index;
        return <li key={name} className={`${done ? "is-done" : ""}${current ? " is-current" : ""}`} aria-current={current ? "step" : undefined}>
          <span className="analysis-progress-dot" aria-hidden>{failed && current ? <CloseOutlined /> : done || (current && phase === "success") ? <CheckOutlined /> : step + 1}</span>
          <span>{failed && current ? "分析失败" : name}</span>
        </li>;
      })}
    </ol>
  </section>;
}

import { useEffect, useState } from "react";
import { fetchAnalysis } from "../api";
import type { AnalysisState } from "../types";

export type AnalysisLookup = Record<string, AnalysisState | "error" | undefined>;

export function useAnalysisStates(ids: string[]) {
  const [states, setStates] = useState<AnalysisLookup>({});
  const key = ids.join("|");
  useEffect(() => {
    if (!ids.length) return;
    const controller = new AbortController();
    for (const id of ids) {
      fetchAnalysis(id, controller.signal)
        .then(state => { if (!controller.signal.aborted) setStates(current => ({ ...current, [id]: state })); })
        .catch(() => { if (!controller.signal.aborted) setStates(current => ({ ...current, [id]: "error" })); });
    }
    return () => controller.abort();
  }, [key]);
  return states;
}

export function analysisStatus(state: AnalysisState | "error" | undefined) {
  if (state === undefined) return { tone: "loading", label: "…" };
  if (state === "error") return { tone: "neutral", label: "状态未知" };
  const status = state.task?.status;
  if (status && ["pending", "preparing", "analyzing"].includes(status)) return { tone: "accent", label: "分析中" };
  if (status === "failed" && !state.result) return { tone: "danger", label: "分析失败" };
  if (state.result) return state.result.analysis_basis === "full_text"
    ? { tone: "success", label: "全文分析" }
    : { tone: "warning", label: "摘要级分析" };
  return { tone: "neutral", label: "未分析" };
}

export function isAnalyzed(state: AnalysisState | "error" | undefined) {
  return Boolean(state && state !== "error" && state.result);
}

export function AnalysisBadge({ state }: { state: AnalysisState | "error" | undefined }) {
  const { tone, label } = analysisStatus(state);
  return <span className={`status status-${tone}`} aria-label={tone === "loading" ? "正在读取分析状态" : `分析状态：${label}`}>{label}</span>;
}

export interface FieldPaper { id: string; title: string; year?: number; url?: string; authors?: string[]; venue?: string }
export interface FieldEvidence { paper_id: string; quote: string }
export interface FieldMilestone { paper_id: string; year: number; kind: "foundation" | "breakthrough" | "representative"; contribution: string; significance: string; evidence: FieldEvidence[] }
export interface FieldObservation { title: string; description: string; uncertainty?: string; evidence: FieldEvidence[] }
export interface FieldReport {
  field_name: string; summary: string; milestones: FieldMilestone[]; hotspots: FieldObservation[]; directions: FieldObservation[];
  papers: FieldPaper[]; limitations: string[]; partial: boolean; generated_at: string;
  coverage: { candidate_count: number; analyzed_count: number; year_from: number; year_to: number; recent_year_from: number; queries: { query: string; purpose: string; returned: number }[] };
}
export interface FieldAnalysis { status: string; report: FieldReport | null; error: string | null; stage: string; stale?: boolean; computed_at?: string }
export interface ResearchFieldItem { id: string; name: string; canonical_name: string; parent?: string; papers: FieldPaper[]; analysis: FieldAnalysis }
export interface WorkspaceFields { fields: ResearchFieldItem[]; unidentified_count: number; legacy_count: number; failed_count?: number; identifying_count: number; pending_analysis_count: number }

async function request<T>(path: string, signal?: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(path, { signal, ...(body !== undefined ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || "研究领域请求未完成，请重试"); }
  return response.json();
}
export const fetchWorkspaceFields = (id: string, signal?: AbortSignal) => request<WorkspaceFields>(`/api/workspaces/${encodeURIComponent(id)}/fields`, signal);
export const startFieldAnalysis = (id: string, fieldId: string, refresh: boolean) => request<FieldAnalysis>(`/api/workspaces/${encodeURIComponent(id)}/fields/${encodeURIComponent(fieldId)}/analyze`, undefined, { refresh });
export const identifyExistingFields = (id: string) => request<{ queued: number }>(`/api/workspaces/${encodeURIComponent(id)}/fields/identify`, undefined, {});
export const fieldActive = (status: string) => status === "queued" || status === "running";

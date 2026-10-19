import type { AnalysisState, AnalyzePaperResult, CompareTable, KnowledgeThemesResponse, Paper, SearchResponse, ImportResult, LibraryResponse, Recommendations, PaperNetwork, QAResponse, ResearchTask, Conversation, ConversationSummary, ConversationMessage } from "./types";

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}

export interface SearchParams {
  q: string;
  page: number;
  pageSize: number;
  sort: "relevance" | "date";
  source: "all" | "arxiv" | "semantic_scholar";
  yearFrom?: string;
  yearTo?: string;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
  } catch {
    return `请求失败（${response.status}）`;
  }
  return `请求失败（${response.status}）`;
}

const searchCache = new Map<string, { expires: number; data: SearchResponse }>();

export async function searchPapers(params: SearchParams, signal?: AbortSignal, onProgress?: (data: SearchResponse) => void, bypassCache = false): Promise<SearchResponse> {
  const query = new URLSearchParams({
    q: params.q,
    page: String(params.page),
    page_size: String(params.pageSize),
    sort: params.sort,
    source: params.source,
  });
  if (params.yearFrom) query.set("year_from", params.yearFrom);
  if (params.yearTo) query.set("year_to", params.yearTo);
  const key = query.toString();
  const cached = searchCache.get(key);
  if (!bypassCache && cached && cached.expires > Date.now()) return cached.data;
  if (onProgress) query.set("stream", "true");
  const response = await fetch(`/api/papers/search?${query}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  let result: SearchResponse;
  if (onProgress && response.headers.get("content-type")?.includes("application/x-ndjson") && response.body) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let latest: SearchResponse | undefined;
    const accept = (line: string) => {
      if (!line.trim()) return;
      latest = JSON.parse(line) as SearchResponse;
      if (!signal?.aborted) onProgress(latest);
    };
    try {
      while (true) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value, { stream: !chunk.done });
        let index: number;
        while ((index = buffer.indexOf("\n")) >= 0) { accept(buffer.slice(0, index)); buffer = buffer.slice(index + 1); }
        if (chunk.done) break;
      }
      accept(buffer);
      if (!latest || latest.pending_sources?.length) throw new Error("结果传输中断，请重试；已收到的论文仍可查看");
      result = latest;
    } catch (error) {
      await reader.cancel().catch(() => undefined);
      throw error;
    } finally { reader.releaseLock(); }
  } else result = await response.json();
  if (!signal?.aborted && !Object.keys(result.source_errors).length && !Object.keys(result.source_cached_at || {}).length && !Object.keys(result.source_retry_after || {}).length) {
    searchCache.delete(key);
    searchCache.set(key, { expires: Date.now() + 120000, data: result });
    while (searchCache.size > 24) searchCache.delete(searchCache.keys().next().value!);
  }
  return result;
}

export async function fetchPaper(id: string, signal?: AbortSignal): Promise<Paper> {
  const local = await fetch(`/api/papers/${encodeURIComponent(id)}`, { signal });
  if (local.ok) return local.json();
  const query = new URLSearchParams({ id });
  const response = await fetch(`/api/papers/detail?${query}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export interface UploadPaperResult {
  paper: Paper;
  created: boolean;
  index: { status: string; chunk_count: number; message: string | null };
}

export async function uploadPaper(file: File, workspaceId: string): Promise<UploadPaperResult> {
  const body = new FormData();
  body.append("file", file);
  body.append("workspace_id", workspaceId);
  const response = await fetch("/api/papers/upload", { method: "POST", body });
  if (!response.ok) throw new Error(await errorMessage(response));
  const result: UploadPaperResult = await response.json();
  window.dispatchEvent(new Event("library-changed"));
  return result;
}

export async function importPaper(externalId: string): Promise<ImportResult> {
  const response = await fetch("/api/papers/import", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ external_id: externalId }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  const result: ImportResult = await response.json();
  window.dispatchEvent(new Event("library-changed"));
  return result;
}

export async function startAnalysis(id: string, reanalyze: boolean): Promise<AnalysisState> {
  const response = await fetch(`/api/papers/${encodeURIComponent(id)}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reanalyze }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function fetchAnalysis(id: string, signal?: AbortSignal): Promise<AnalysisState> {
  const response = await fetch(`/api/papers/${encodeURIComponent(id)}/analysis`, { signal });
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return response.json();
}

export async function fetchRecommendations(id: string, refresh: boolean, signal?: AbortSignal): Promise<Recommendations> {
  const query = refresh ? "?refresh=true" : "";
  const response = await fetch(`/api/papers/${encodeURIComponent(id)}/recommendations${query}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function fetchNetwork(id: string, refresh: boolean, signal?: AbortSignal): Promise<PaperNetwork> {
  const query = refresh ? "?refresh=true" : "";
  const response = await fetch(`/api/papers/${encodeURIComponent(id)}/network${query}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function fetchLibrary(page: number, signal?: AbortSignal, pageSize = 20): Promise<LibraryResponse> {
  const response = await fetch(`/api/papers?page=${page}&page_size=${pageSize}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

async function postJson<T>(url: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body), signal,
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export function askLibrary(question: string, paperIds: string[], signal?: AbortSignal) {
  return postJson<QAResponse>("/api/qa", { question, paper_ids: paperIds }, signal);
}

const conversationUrl = (workspaceId: string, id?: string) => `/api/workspaces/${encodeURIComponent(workspaceId)}/conversations${id ? `/${encodeURIComponent(id)}` : ""}`;
async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  return response.json();
}
export const listConversations = (workspaceId: string, signal?: AbortSignal) => getJson<ConversationSummary[]>(conversationUrl(workspaceId), signal);
export const fetchConversation = (workspaceId: string, id: string, signal?: AbortSignal) => getJson<Conversation>(conversationUrl(workspaceId, id), signal);
export const createConversation = (workspaceId: string, paperIds: string[] = [], signal?: AbortSignal) => postJson<Conversation>(conversationUrl(workspaceId), { paper_ids: paperIds }, signal);
export const updateConversation = (workspaceId: string, id: string, changes: { title?: string; paper_ids?: string[] }, signal?: AbortSignal) => postJson<Conversation>(conversationUrl(workspaceId, id), changes, signal);
export const deleteConversation = (workspaceId: string, id: string) => postJson(`${conversationUrl(workspaceId, id)}/delete`, {});
export const askConversation = (workspaceId: string, id: string, question: string, paperIds: string[], requestId: string, signal?: AbortSignal) => postJson<ConversationMessage>(`${conversationUrl(workspaceId, id)}/messages`, { question, paper_ids: paperIds, request_id: requestId }, signal);
export const removeWorkspacePapers = (workspaceId: string, paperIds: string[]) => postJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/papers/remove`, { paper_ids: paperIds });
export const deleteWorkspace = (workspaceId: string) => postJson(`/api/workspaces/${encodeURIComponent(workspaceId)}/delete`, {});

export function analyzePapers(body: { paper_ids: string[]; fields?: string[]; depth?: "standard" | "deep"; workspace_id?: string }, signal?: AbortSignal) {
  return postJson<AnalyzePaperResult>("/api/tools/analyze_paper", body, signal);
}

export function fillGraphCitations(paperIds: string[], signal?: AbortSignal) {
  return postJson<{ queried: number; failed: number; linked: number; items: { id: string; title: string; status: string; note: string }[] }>("/api/knowledge-graph/citations", { paper_ids: paperIds }, signal);
}

export function fetchKnowledgeThemes(references: string[], signal?: AbortSignal) {
  return postJson<KnowledgeThemesResponse>("/api/knowledge-graph/themes", references, signal);
}

export function compareLibrary(paperIds: string[], signal?: AbortSignal) {
  return postJson<CompareTable>("/api/compare", { paper_ids: paperIds }, signal);
}

export function startResearch(goal: string, options: { autoApprove: boolean; background?: boolean; yearFrom?: number | null; yearTo?: number | null; workspaceId?: string; scope?: "current" | "selected" | "workspace"; currentPaperId?: string; selectedPaperIds?: string[]; accessiblePaperIds?: string[]; conversationHistory?: string; mode?: "auto" | "react" | "plan" }, signal?: AbortSignal) {
  return postJson<ResearchTask>("/api/research/tasks", {
    goal, background: options.background ?? false, auto_approve: options.autoApprove, year_from: options.yearFrom ?? null, year_to: options.yearTo ?? null,
    workspace_id: options.workspaceId, scope: options.scope ?? "selected", current_paper_id: options.currentPaperId,
    accessible_paper_ids: options.accessiblePaperIds, conversation_history: options.conversationHistory ?? "",
    selected_paper_ids: options.selectedPaperIds ?? [], mode: options.mode ?? "auto",
  }, signal);
}

export type ResearchDecision = {
  background?: boolean; accessible_paper_ids?: string[]; approved?: boolean; approved_ids?: string[]; queries?: string[]; abort?: boolean;
  scope?: "current" | "selected" | "workspace"; current_paper_id?: string; selected_paper_ids?: string[];
};

export function researchDecision(taskId: string, decision: ResearchDecision, signal?: AbortSignal) {
  return postJson<ResearchTask>(`/api/research/tasks/${encodeURIComponent(taskId)}/resume`, decision, signal);
}

export async function prepareWorkspace(workspaceId: string, name: string, paperIds: string[], signal?: AbortSignal) {
  await postJson("/api/workspaces/register", { workspace_id: workspaceId, name }, signal);
  if (paperIds.length) {
    for (let offset = 0; offset < paperIds.length; offset += 50) {
      const result = await postJson<{ failed: number; results: { status: string; message?: string }[] }>("/api/tools/add_papers_to_workspace", {
        workspace_id: workspaceId, paper_ids: paperIds.slice(offset, offset + 50),
      }, signal);
      if (result.failed) throw new Error(result.results.find(item => item.status === "failed")?.message || "工作区同步失败");
    }
  }
}

export async function workspacePapers(workspaceId: string, signal?: AbortSignal): Promise<Paper[]> {
  const response = await fetch(`/api/workspaces/${encodeURIComponent(workspaceId)}/papers`, { signal });
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
  const body = await response.json() as { papers: { paper: Paper }[] };
  return body.papers.map(item => item.paper);
}

export function resumeResearch(taskId: string, queries: string[], signal?: AbortSignal) {
  return postJson<ResearchTask>(`/api/research/tasks/${encodeURIComponent(taskId)}/resume`, {
    queries, plan: queries.map(query => ({ action: "search", query })),
  }, signal);
}

export async function fetchResearchTask(taskId: string, signal?: AbortSignal): Promise<ResearchTask> {
  const r = await fetch(`/api/research/tasks/${encodeURIComponent(taskId)}`, { signal });
  if (!r.ok) throw new ApiError(await errorMessage(r), r.status);
  return r.json();
}
export function cancelResearchTask(taskId: string) {
  return postJson(`/api/research/tasks/${encodeURIComponent(taskId)}/cancel`, {});
}

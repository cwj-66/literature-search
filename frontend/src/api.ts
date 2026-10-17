import type { AnalysisState, Paper, SearchResponse, ImportResult, LibraryResponse, Recommendations, PaperNetwork, QAResponse, ResearchTask } from "./types";

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
  if (!response.ok) throw new Error(await errorMessage(response));
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

export async function fetchLibrary(page: number, signal?: AbortSignal): Promise<LibraryResponse> {
  const response = await fetch(`/api/papers?page=${page}&page_size=20`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function askLibrary(question: string, paperIds: string[]): Promise<QAResponse> {
  const response = await fetch("/api/qa", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, paper_ids: paperIds }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function compareLibrary(paperIds: string[]) {
  const response = await fetch("/api/compare", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paper_ids: paperIds }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function startResearch(goal: string, autoApprove = false): Promise<ResearchTask> {
  const response = await fetch("/api/research/tasks", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, auto_approve: autoApprove }),
  });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

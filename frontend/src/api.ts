import type { Paper, SearchResponse } from "./types";

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

export async function searchPapers(params: SearchParams, signal?: AbortSignal): Promise<SearchResponse> {
  const query = new URLSearchParams({
    q: params.q,
    page: String(params.page),
    page_size: String(params.pageSize),
    sort: params.sort,
    source: params.source,
  });
  if (params.yearFrom) query.set("year_from", params.yearFrom);
  if (params.yearTo) query.set("year_to", params.yearTo);
  const response = await fetch(`/api/papers/search?${query}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

export async function fetchPaper(id: string, signal?: AbortSignal): Promise<Paper> {
  const query = new URLSearchParams({ id });
  const response = await fetch(`/api/papers/detail?${query}`, { signal });
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

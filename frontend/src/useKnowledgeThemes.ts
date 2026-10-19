import { useEffect, useState } from "react";
import { fetchKnowledgeThemes } from "./api";
import type { KnowledgeThemesResponse } from "./types";

export const EMPTY_THEMES: KnowledgeThemesResponse = {
  themes: [], evolution: { periods: [], links: [], reason: "" }, years: [],
  total_papers: 0, analyzed_papers: 0, scope_note: "", scope: "workspace", status: "ok", status_reason: "",
};

export function useKnowledgeThemes(references?: string[], scope: "workspace" | "expanded" = "workspace") {
  const key = references === undefined ? "local" : JSON.stringify([...new Set(references)].sort());
  const [data, setData] = useState<KnowledgeThemesResponse | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setData(null); setError("");
    const refs: string[] | undefined = key === "local" ? undefined : JSON.parse(key);
    if (refs && !refs.length) { setData({ ...EMPTY_THEMES, scope }); return () => abort.abort(); }
    (refs ? fetchKnowledgeThemes(refs, abort.signal, scope) : fetch(`/api/knowledge-graph/themes?limit=2000&scope=${scope}`, { signal: abort.signal }).then(async response => {
      if (!response.ok) throw new Error(response.status === 422 ? "论文标识过多或无效，暂时无法读取主题统计。" : "主题统计加载失败，请重试。");
      return await response.json() as KnowledgeThemesResponse;
    })).then(result => { if (!abort.signal.aborted) setData(result); })
      .catch((err: Error) => { if (!abort.signal.aborted) setError(err.message); });
    return () => abort.abort();
  }, [key, revision, scope]);
  return { data, error, refresh: () => setRevision(r => r + 1) };
}

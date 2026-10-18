import { useEffect, useRef, useState } from "react";
import type { AnalyzePaperResult, Paper, ResearchTask } from "./types";
import { prepareWorkspace } from "./api";

export interface ResearchTurn {
  id: number; question: string; answeredAt?: number; pending?: boolean; error?: string;
  papers?: Paper[]; total?: number; analysis?: AnalyzePaperResult; task?: ResearchTask; note?: string;
  request?: { selectedIds: string[]; history: string; mode: "deep" | "standard" };
}
export interface ResearchChat {
  id: string; title: string; titleEdited?: boolean; pinned?: boolean; archived?: boolean;
  createdAt?: number; updatedAt?: number; turns: ResearchTurn[]; selectedIds: string[];
  version?: number; dirty?: boolean;
}
const key = (id: string) => `assistant-documents:${id}`;
function parse<T>(name: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(name) || "null") ?? fallback; } catch { return fallback; }
}
export function cachedChats(id: string): ResearchChat[] {
  const current = parse<ResearchChat[] | null>(key(id), null);
  if (Array.isArray(current)) return current;
  let old = parse<{ id: string; title: string; titleEdited?: boolean }[]>(`research-chats:${id}`, []);
  if (!old.length && localStorage.getItem(`ws-ask:${id}`)) old = [{ id: "first", title: "研究对话" }];
  return old.map(chat => ({ ...chat, turns: parse(`ws-ask:${id}:${chat.id}`, chat.id === "first" ? parse(`ws-ask:${id}`, []) : []),
    selectedIds: parse(`research-sources:${id}:${chat.id}`, []), createdAt: Date.now(), updatedAt: Date.now(), version: 0 }));
}
async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) {
    let message = `保存失败（${response.status}）`;
    try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch { /* use status */ }
    throw new Error(message);
  }
  return response.json();
}
export function useResearchChats(workspaceId: string, workspaceName: string) {
  const [chats, setChats] = useState(() => cachedChats(workspaceId));
  const latest = useRef(chats); latest.current = chats;
  const versions = useRef(new Map<string, number>());
  const dirty = useRef(new Map<string, ResearchChat>());
  const saving = useRef<Promise<void> | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [reload, setReload] = useState(0);
  const base = `/api/workspaces/${encodeURIComponent(workspaceId)}/assistant-chats`;
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        await prepareWorkspace(workspaceId, workspaceName, []);
        // Import is create-only, including tombstones: an old browser cannot resurrect deleted chats.
        const local = cachedChats(workspaceId);
        const result = await request<{ chats: ResearchChat[]; deleted_ids: string[] }>(`${base}/import`, { chats: local });
        if (!alive) return;
        const merged = result.chats.map(remote => {
          versions.current.set(remote.id, remote.version ?? 0);
          const cached = local.find(chat => chat.id === remote.id);
          if (cached?.dirty) {
            if (!cached.version) cached.version = remote.version;
            dirty.current.set(cached.id, cached);
            return cached;
          }
          return remote;
        });
        latest.current = merged; setChats(merged); setReady(true); setError("");
      } catch (err) { if (alive) setError(err instanceof Error ? err.message : "对话同步失败"); }
    })();
    return () => { alive = false; };
  }, [workspaceId, reload]);
  useEffect(() => {
    try { localStorage.setItem(key(workspaceId), JSON.stringify(chats)); } catch { setError("浏览器缓存已满，请保持页面打开并等待服务端保存，或先导出对话。"); }
  }, [workspaceId, chats]);
  function update(chat: ResearchChat) {
    const next = { ...chat, updatedAt: Date.now(), dirty: true };
    dirty.current.set(chat.id, next);
    latest.current = latest.current.some(c => c.id === chat.id) ? latest.current.map(c => c.id === chat.id ? next : c) : [...latest.current, next];
    setChats(latest.current); setPending(true);
    try { localStorage.setItem(key(workspaceId), JSON.stringify(latest.current)); } catch { /* server sync remains available */ }
  }
  async function flush() {
    if (saving.current) { await saving.current; if (dirty.current.size) return flush(); return; }
    const run = async () => {
      setPending(true);
      try {
        for (const [id, chat] of dirty.current) {
          const remote = await request<ResearchChat>(`${base}/save`, { ...chat, version: chat.version ?? versions.current.get(id) ?? 0 });
          versions.current.set(id, remote.version ?? 0);
          if (dirty.current.get(id) === chat) {
            dirty.current.delete(id);
            latest.current = latest.current.map(c => c.id === id ? { ...remote, dirty: false } : c);
          } else {
            const newer = dirty.current.get(id)!;
            newer.version = remote.version;
            latest.current = latest.current.map(c => c.id === id ? { ...c, version: remote.version } : c);
          }
        }
        setChats(latest.current); setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "同步失败，记录已保留在浏览器");
        throw err;
      } finally { setPending(dirty.current.size > 0); }
    };
    saving.current = run();
    try { await saving.current; } finally { saving.current = null; }
  }
  useEffect(() => {
    if (!ready || !dirty.current.size || error) return;
    const timer = window.setTimeout(() => { void flush().catch(() => {}); }, 600);
    return () => window.clearTimeout(timer);
  }, [chats, ready, error]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty.current.size) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  async function remove(chat: ResearchChat) {
    await flush();
    await request(`${base}/${encodeURIComponent(chat.id)}/delete`, { version: versions.current.get(chat.id) ?? chat.version });
    dirty.current.delete(chat.id);
    latest.current = latest.current.filter(c => c.id !== chat.id);
    setChats(latest.current);
    try { localStorage.setItem(key(workspaceId), JSON.stringify(latest.current)); } catch { /* server remains authoritative */ }
    // Remove old local records as well; membership and paper data are untouched.
    for (const prefix of ["ws-ask:", "research-sources:", "ws-ask-greet:"]) localStorage.removeItem(`${prefix}${workspaceId}:${chat.id}`);
  }
  return { chats, ready, error, pending, update, remove, flush,
    patch: (id: string, changes: Partial<ResearchChat>) => {
      const chat = latest.current.find(c => c.id === id);
      if (!chat || Object.entries(changes).every(([key, value]) => chat[key as keyof ResearchChat] === value)) return;
      update({ ...chat, ...changes });
    },
    retry: () => { setError(""); if (!ready) setReload(n => n + 1); else void flush().catch(() => {}); } };
}

export function chatMarkdown(chat: ResearchChat, workspace: string) {
  const lines = [`# ${chat.title}`, "", `工作区：${workspace}`, ""];
  for (const turn of chat.turns) {
    lines.push(`## 用户\n\n${turn.question}`, "", `## 助手\n\n${turn.task?.answer || turn.error || turn.note || "尚未完成"}`, "");
    const refs = new Set(turn.task?.referenced_evidence_ids ?? (turn.task?.answer?.match(/E\d{4,}/g) ?? []));
    for (const e of turn.task?.evidence ?? []) if (refs.has(e.evidence_id)) lines.push(`> [${e.evidence_id}] ${e.paper_id ?? ""}：${e.quote}`, "");
    for (const p of turn.papers ?? []) lines.push(`- ${p.title}${p.doi ? ` · DOI ${p.doi}` : ""}`, "");
  }
  return lines.join("\n");
}
export function exportChat(chat: ResearchChat, workspace: string) {
  const url = URL.createObjectURL(new Blob([chatMarkdown(chat, workspace)], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = `${chat.title.replace(/[<>:"/\\|?*]/g, "_").slice(0, 80)}.md`; a.click(); URL.revokeObjectURL(url);
}

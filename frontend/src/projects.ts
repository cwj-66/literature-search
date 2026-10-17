import { useSyncExternalStore } from "react";
import type { Paper } from "./types";
import { paperKey } from "./types";

export type ReadStatus = "unread" | "reading" | "read";
export interface ProjectPaper { paper: Paper; status: ReadStatus; note: string; addedAt: string; openedAt?: string }
export interface Project { id: string; name: string; description: string; createdAt: string; papers: Record<string, ProjectPaper> }
interface State { projects: Project[]; currentId: string }

export const STATUS_LABEL: Record<ReadStatus, string> = { unread: "未读", reading: "在读", read: "已读" };
const KEY = "research-projects";
const listeners = new Set<() => void>();

function load(): State {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (value && Array.isArray(value.projects)) {
      const projects = value.projects.filter((p: Project) => typeof p?.id === "string" && typeof p.name === "string" && p.papers && typeof p.papers === "object")
        .map((p: Project) => ({ ...p, description: typeof p.description === "string" ? p.description : "" }));
      return { projects, currentId: projects.some((p: Project) => p.id === value.currentId) ? value.currentId : projects[0]?.id ?? "" };
    }
  } catch { /* fall through to an empty workspace */ }
  return { projects: [], currentId: "" };
}

let state = load();

function commit(next: State) {
  state = next;
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* In-memory state remains usable when storage is disabled. */ }
  listeners.forEach(listener => listener());
}

function patchCurrent(change: (project: Project) => Project) {
  commit({ ...state, projects: state.projects.map(p => p.id === state.currentId ? change(p) : p) });
}

function patchPaper(key: string, change: (item: ProjectPaper) => ProjectPaper) {
  patchCurrent(project => project.papers[key] ? { ...project, papers: { ...project.papers, [key]: change(project.papers[key]) } } : project);
}

export function findKey(project: Project | undefined, paper: { id: string; local_id?: string }) {
  if (!project) return "";
  return Object.entries(project.papers).find(([key, item]) => key === paper.local_id || key === paper.id || item.paper.id === paper.id)?.[0] ?? "";
}

export const projects = {
  create(name: string, description = "") {
    const id = `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    commit({ projects: [...state.projects, { id, name: name.trim(), description: description.trim(), createdAt: new Date().toISOString(), papers: {} }], currentId: id });
    return id;
  },
  select(id: string) { if (id !== state.currentId && state.projects.some(p => p.id === id)) commit({ ...state, currentId: id }); },
  rename(name: string) { patchCurrent(project => ({ ...project, name: name.trim() || project.name })); },
  removeProject() {
    const rest = state.projects.filter(p => p.id !== state.currentId);
    commit({ projects: rest, currentId: rest[0]?.id ?? "" });
  },
  ensure() { return state.currentId || projects.create("我的第一个研究项目"); },
  add(paper: Paper) {
    projects.ensure();
    const key = paperKey(paper);
    patchCurrent(project => {
      const existing = findKey(project, paper);
      const papers = { ...project.papers };
      if (existing && existing !== key) { papers[key] = { ...papers[existing], paper }; delete papers[existing]; }
      else papers[key] = existing ? { ...papers[key], paper } : { paper, status: "unread", note: "", addedAt: new Date().toISOString() };
      return { ...project, papers };
    });
  },
  removePaper(key: string) {
    patchCurrent(project => { const papers = { ...project.papers }; delete papers[key]; return { ...project, papers }; });
  },
  setStatus(key: string, status: ReadStatus) { patchPaper(key, item => ({ ...item, status })); },
  setNote(key: string, note: string) { patchPaper(key, item => ({ ...item, note })); },
  open(paper: { id: string; local_id?: string }) {
    const project = state.projects.find(p => p.id === state.currentId);
    const key = findKey(project, paper);
    if (key) patchPaper(key, item => ({ ...item, status: item.status === "unread" ? "reading" : item.status, openedAt: new Date().toISOString() }));
  },
};

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function projectUpdated(project: Project) {
  const times = Object.values(project.papers).flatMap(item => [item.openedAt, item.addedAt].filter((value): value is string => Boolean(value)));
  return times.sort().at(-1) ?? project.createdAt;
}

export function useProjects() {
  const snapshot = useSyncExternalStore(subscribe, () => state);
  return { projects: snapshot.projects, current: snapshot.projects.find(p => p.id === snapshot.currentId) };
}

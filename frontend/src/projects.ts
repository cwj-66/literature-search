import { useSyncExternalStore } from "react";
import type { Paper } from "./types";
import { paperKey } from "./types";

export type ReadStatus = "unread" | "reading" | "read";
export const COVER_IDS = ["layers", "docs", "agent", "media", "chart", "vision", "graph", "notes"] as const;
export type CoverId = (typeof COVER_IDS)[number];
export const DEFAULT_COVER: CoverId = "notes";
export interface ProjectPaper { paper: Paper; status: ReadStatus; note: string; addedAt: string; openedAt?: string }
export interface Project { id: string; name: string; description: string; createdAt: string; openedAt?: string; cover?: CoverId; coverImage?: string; papers: Record<string, ProjectPaper> }
interface State { projects: Project[]; currentId: string }

export function isCoverId(value: unknown): value is CoverId {
  return typeof value === "string" && (COVER_IDS as readonly string[]).includes(value);
}

export const STATUS_LABEL: Record<ReadStatus, string> = { unread: "未读", reading: "在读", read: "已读" };
const KEY = "research-projects";
const listeners = new Set<() => void>();

function load(): State {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "null");
    if (value && Array.isArray(value.projects)) {
      const projects = value.projects.filter((p: Project) => typeof p?.id === "string" && typeof p.name === "string" && p.papers && typeof p.papers === "object")
        .map((p: Project) => ({
          ...p,
          description: typeof p.description === "string" ? p.description : "",
          cover: isCoverId(p.cover) ? p.cover : undefined,
          coverImage: typeof p.coverImage === "string" && p.coverImage.startsWith("data:image/") ? p.coverImage : undefined,
          openedAt: typeof p.openedAt === "string" ? p.openedAt : undefined,
        }));
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
  syncPapers(id: string, incoming: Paper[]) {
    commit({ ...state, projects: state.projects.map(project => project.id !== id ? project : {
      ...project, papers: Object.fromEntries(incoming.map(paper => {
        const existing = project.papers[findKey(project, paper)];
        return [paperKey(paper), existing ? { ...existing, paper } : { paper, status: "unread" as ReadStatus, note: "", addedAt: new Date().toISOString() }];
      })),
    }) });
  },
  create(name: string, description = "", extra?: { cover?: CoverId; coverImage?: string }) {
    const id = `p${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    const now = new Date().toISOString();
    const coverImage = extra?.coverImage?.startsWith("data:image/") ? extra.coverImage : undefined;
    commit({
      projects: [...state.projects, {
        id, name: name.trim(), description: description.trim(), createdAt: now, openedAt: now,
        cover: coverImage ? undefined : extra?.cover && isCoverId(extra.cover) ? extra.cover : DEFAULT_COVER,
        coverImage, papers: {},
      }],
      currentId: id,
    });
    return id;
  },
  select(id: string) { if (id !== state.currentId && state.projects.some(p => p.id === id)) commit({ ...state, currentId: id }); },
  setCover(id: string, cover: CoverId) {
    if (!isCoverId(cover) || !state.projects.some(p => p.id === id)) return;
    commit({ ...state, projects: state.projects.map(p => p.id === id ? { ...p, cover, coverImage: undefined } : p) });
  },
  setCoverImage(id: string, coverImage: string) {
    if (!coverImage.startsWith("data:image/") || !state.projects.some(p => p.id === id)) return;
    commit({ ...state, projects: state.projects.map(p => p.id === id ? { ...p, coverImage, cover: undefined } : p) });
  },
  touch(id: string) {
    if (!state.projects.some(p => p.id === id)) return;
    const now = new Date().toISOString();
    commit({ ...state, projects: state.projects.map(p => p.id === id ? { ...p, openedAt: now } : p) });
  },
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

export function projectOpened(project: Project) {
  const times = [project.openedAt, ...Object.values(project.papers).map(item => item.openedAt)].filter((value): value is string => Boolean(value));
  return times.sort().at(-1) ?? project.createdAt;
}

export function useProjects() {
  const snapshot = useSyncExternalStore(subscribe, () => state);
  return { projects: snapshot.projects, current: snapshot.projects.find(p => p.id === snapshot.currentId) };
}

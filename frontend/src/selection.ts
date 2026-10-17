import { useSyncExternalStore } from "react";

export interface Picked { id: string; title: string }

const KEY = "paper-selection";
export const MAX_SELECTION = 40;
const listeners = new Set<() => void>();

function load(): Picked[] {
  try {
    const value = JSON.parse(sessionStorage.getItem(KEY) || "[]");
    return Array.isArray(value) ? value.filter(item => typeof item?.id === "string" && typeof item?.title === "string").filter((item, index, list) => list.findIndex(other => other.id === item.id) === index).slice(0, MAX_SELECTION) : [];
  } catch {
    return [];
  }
}

let items = load();

function commit(next: Picked[]) {
  items = next.slice(0, MAX_SELECTION);
  try { sessionStorage.setItem(KEY, JSON.stringify(items)); } catch { /* In-memory selection remains available when storage is disabled. */ }
  listeners.forEach(listener => listener());
}

export const selection = {
  toggle(item: Picked) {
    commit(items.some(current => current.id === item.id) ? items.filter(current => current.id !== item.id) : [...items, item]);
  },
  setMany(list: Picked[], picked: boolean) {
    const ids = new Set(list.map(item => item.id));
    commit(picked ? [...items, ...list.filter(item => !items.some(current => current.id === item.id))] : items.filter(item => !ids.has(item.id)));
  },
  remove(id: string) { commit(items.filter(item => item.id !== id)); },
  clear() { commit([]); },
};

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useSelection() {
  return useSyncExternalStore(subscribe, () => items);
}

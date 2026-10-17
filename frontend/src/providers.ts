import { useSyncExternalStore } from "react";

export interface ProviderModel { id: string; economy: boolean; strong: boolean }
export interface ProviderConn { id: string; name: string; baseUrl: string; apiKey: string; enabled: boolean; models: ProviderModel[] }

const KEY = "model-providers";
const listeners = new Set<() => void>();

function load(): ProviderConn[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(value) ? value.filter(item => typeof item?.id === "string" && typeof item.name === "string") : [];
  } catch { return []; }
}

let items = load();

function commit(next: ProviderConn[]) {
  items = next;
  try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* keep in memory */ }
  listeners.forEach(listener => listener());
}

export const providers = {
  add(input: Omit<ProviderConn, "id">) {
    const id = `m${Date.now().toString(36)}`;
    commit([...items, { ...input, id }]);
    return id;
  },
  update(id: string, patch: Partial<ProviderConn>) {
    commit(items.map(item => item.id === id ? { ...item, ...patch, id } : item));
  },
  remove(id: string) { commit(items.filter(item => item.id !== id)); },
};

export function useProviders() {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => items);
}

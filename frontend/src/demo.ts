/** Demo bootstraps before importing stores, so no personal/browser data leaks in. */
import { refreshDemoProjects } from "./demoProjects";
import type { Project } from "./projects";
export const DEMO_BASE = "/demo/paper-search/";
export const isDemo = window.location.pathname.startsWith(DEMO_BASE);
export interface DemoInfo { visitor: string; generation: string; model: string; model_configured: boolean; retention_days: number; projects: Project[]; seed_paper_count?: number; seed_grouped_count?: number }
export let demoInfo: DemoInfo | null = null;

export async function initializeDemo() {
  if (!isDemo) {
    // Public preview has its own storage namespace. Preserve the old domain's
    // user data without treating it as this fresh instance's initial content.
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/api/runtime", { signal: controller.signal });
      if (response.ok && (await response.json()).mode === "public_preview") {
        const prototype = Storage.prototype;
        const get = prototype.getItem, set = prototype.setItem, remove = prototype.removeItem;
        const prefix = "paper-opensource-preview:v1:";
        prototype.getItem = function (key) { return get.call(this, prefix + key); };
        prototype.setItem = function (key, value) { set.call(this, prefix + key, value); };
        prototype.removeItem = function (key) { remove.call(this, prefix + key); };
      }
    } catch { /* A private local instance can still show its UI with the API offline. */ }
    finally { window.clearTimeout(timer); }
    return;
  }
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const parsed = new URL(url, window.location.origin);
    let target = input;
    if (parsed.origin === window.location.origin && parsed.pathname.startsWith("/api/")) {
      parsed.pathname = DEMO_BASE.slice(0, -1) + parsed.pathname;
      target = input instanceof Request ? new Request(parsed, input) : parsed.href;
    }
    const response = await originalFetch(target, init);
    if (response.status === 401) window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    return response;
  };
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30_000);
  let response: Response;
  try {
    response = await fetch("/api/demo/bootstrap", { signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) throw new Error("连接演示环境超时，请检查网络后重试。");
    throw error;
  } finally { window.clearTimeout(timeout); }
  if (!response.ok) throw new Error(response.status === 401 ? "请重新登录作品集" : "演示环境暂不可用，请稍后刷新重试");
  demoInfo = await response.json() as DemoInfo;
  // All app caches use the same visitor namespace, including conversations and sources.
  const prototype = Storage.prototype;
  const get = prototype.getItem;
  const set = prototype.setItem;
  const remove = prototype.removeItem;
  const prefix = `paper-demo:${demoInfo.visitor}:${demoInfo.generation}:`;
  prototype.getItem = function (key) { return get.call(this, prefix + key); };
  prototype.setItem = function (key, value) { set.call(this, prefix + key, value); };
  prototype.removeItem = function (key) { remove.call(this, prefix + key); };
  const cached = localStorage.getItem("research-projects");
  if (!cached) {
    localStorage.setItem("research-projects", JSON.stringify({ projects: demoInfo.projects, currentId: "" }));
  } else {
    try {
      const state = JSON.parse(cached) as { projects: Project[]; currentId: string };
      if (Array.isArray(state.projects)) localStorage.setItem("research-projects", JSON.stringify(refreshDemoProjects(state, demoInfo.projects)));
    } catch { /* Preserve the cached copy rather than erase visitor-owned data. */ }
  }
}

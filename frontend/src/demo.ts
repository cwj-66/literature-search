/** Demo bootstraps before importing stores, so no personal/browser data leaks in. */
export const DEMO_BASE = "/demo/paper-search/";
export const isDemo = window.location.pathname.startsWith(DEMO_BASE);
export interface DemoInfo { visitor: string; generation: string; model: string; model_configured: boolean; retention_days: number; projects: unknown[] }
export let demoInfo: DemoInfo | null = null;

export async function initializeDemo() {
  if (!isDemo) return;
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
  const response = await fetch("/api/demo/bootstrap");
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
  if (!localStorage.getItem("research-projects")) {
    localStorage.setItem("research-projects", JSON.stringify({ projects: demoInfo.projects, currentId: "" }));
  }
}

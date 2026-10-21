export function safeReturn(value: string | null, fallback = "/library") {
  return value?.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") ? value : fallback;
}

function originOf(params: URLSearchParams, depth = 0): string {
  const from = safeReturn(params.get("from"), "");
  if (!from || depth > 6) return "";
  const parent = new URL(from, window.location.origin);
  if (["/paper", "/recommend"].includes(parent.pathname)) return originOf(parent.searchParams, depth + 1) || from;
  return from;
}

export function libraryLocation(params: URLSearchParams): string {
  const origin = originOf(params);
  return origin.startsWith("/library") ? origin : "/library";
}

export type WorkspaceSection = "home" | "search" | "assistant" | "studio" | "library" | "compare" | "graph" | "reading";

export function workspaceContext(pathname: string, search: string): { id: string; section: WorkspaceSection } | null {
  const params = new URLSearchParams(search);
  let path = pathname;
  if (pathname === "/paper" || pathname === "/recommend") {
    const origin = originOf(params);
    if (!origin.startsWith("/w/")) return null;
    path = new URL(origin, "http://local").pathname;
  }
  const match = path.match(/^\/w\/([^/]+)(?:\/(home|search|library|compare|graph|studio|research|p)(?:\/([^/]+))?)?\/?$/);
  if (!match || (match[2] === "p" && !match[3])) return null;
  const section: WorkspaceSection = match[2] === "home" ? "home" : match[2] === "search" ? "search" : match[2] === "studio" ? "studio"
    : match[2] === "p" ? "reading"
    : match[2] === "library" || match[2] === "compare" || match[2] === "graph" ? match[2]
    : match[2] === "research" ? "assistant" : "home";
  return { id: match[1], section };
}

export function sectionOf(pathname: string, params: URLSearchParams) {
  if (pathname === "/paper" || pathname === "/recommend") {
    const origin = originOf(params);
    if (!origin) return "library";
    return sectionOf(new URL(origin, window.location.origin).pathname, new URLSearchParams());
  }
  if (pathname.startsWith("/library")) return "library";
  if (pathname.startsWith("/studio")) return "studio";
  if (pathname.startsWith("/graph")) return "graph";
  if (pathname.startsWith("/search")) return "search";
  if (pathname.startsWith("/settings")) return "settings";
  return "home";
}

export function returnLabel(from: string) {
  const path = new URL(from, window.location.origin).pathname;
  if (path === "/" || path.startsWith("/w/")) return "工作区";
  if (path === "/search") return "检索结果";
  if (path.startsWith("/recommend")) return "相关推荐";
  if (path.startsWith("/paper")) return "上一篇论文";
  if (path.startsWith("/graph")) return "图谱";
  if (path.startsWith("/studio")) return "研究台";
  return "文献库";
}

export function workspacePaperPath(workspaceId: string, paperKey: string, from?: string) {
  const path = `/w/${workspaceId}/p/${encodeURIComponent(paperKey)}`;
  return from ? `${path}?${new URLSearchParams({ from })}` : path;
}

export function paperLocation(id: string, from: string) {
  return `/paper?${new URLSearchParams({ id, from })}`;
}

export function recommendLocation(id: string, from: string) {
  return `/recommend?${new URLSearchParams({ id, from })}`;
}

export function graphLocation(from: string, focus?: string) {
  const params = new URLSearchParams({ from, mode: "local" });
  if (focus) params.set("focus", focus);
  return `/graph?${params}`;
}

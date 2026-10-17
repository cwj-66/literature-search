export function safeReturn(value: string | null, fallback = "/library") {
  return value?.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\") ? value : fallback;
}

export function libraryLocation(params: URLSearchParams): string {
  const from = safeReturn(params.get("from"));
  if (from.startsWith("/library")) return from;
  // Keep the originating collection even when following a recommendation.
  if (from.startsWith("/recommend") || from.startsWith("/paper") || from.startsWith("/graph")) {
    const parent = new URL(from, window.location.origin);
    return libraryLocation(parent.searchParams);
  }
  return "/library";
}

export function paperLocation(id: string, from: string) {
  return `/paper?${new URLSearchParams({ id, from })}`;
}

export function graphLocation(from: string, focus?: string) {
  const params = new URLSearchParams({ from, mode: "local" });
  if (focus) params.set("focus", focus);
  return `/graph?${params}`;
}

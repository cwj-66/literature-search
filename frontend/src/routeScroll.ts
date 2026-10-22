import { useLayoutEffect, useRef } from "react";

const positions = new Map<string, { window: number; panel: number }>();

// A graph/field selection changes the URL, but remains the same scrollable page.
export function scrollPageKey(pathname: string, search: string) {
  return pathname === "/graph" || /^\/w\/[^/]+\/(fields|graph)$/.test(pathname) ? pathname : pathname + search;
}

export function useRouteScroll(pathname: string, search: string, hash: string, inside: boolean) {
  const key = scrollPageKey(pathname, search);
  const capture = useRef(() => {});
  useLayoutEffect(() => {
    const panel = inside ? document.querySelector<HTMLElement>(".ws-main") : null;
    const target = positions.get(key) ?? { window: 0, panel: 0 };
    let restoring = true;
    let frame = 0;
    const deadline = performance.now() + 4000;
    const save = () => {
      if (!restoring) positions.set(key, { window: window.scrollY, panel: panel?.scrollTop ?? 0 });
    };
    const stop = () => { restoring = false; cancelAnimationFrame(frame); save(); };
    capture.current = () => {
      restoring = false;
      cancelAnimationFrame(frame);
      positions.set(key, { window: window.scrollY, panel: panel?.scrollTop ?? 0 });
    };
    const restore = () => {
      if (!restoring || hash) return;
      window.scrollTo({ top: target.window, behavior: "instant" });
      panel?.scrollTo({ top: target.panel, behavior: "instant" });
      const reached = Math.abs(window.scrollY - target.window) < 2 && (!panel || Math.abs(panel.scrollTop - target.panel) < 2);
      // Lazy routes and fetched lists may not have enough height on the first frame.
      if (reached || performance.now() >= deadline) stop();
      else frame = requestAnimationFrame(restore);
    };
    if (hash) stop(); else restore();
    window.addEventListener("scroll", save, { passive: true });
    panel?.addEventListener("scroll", save, { passive: true });
    for (const event of ["wheel", "touchstart", "pointerdown", "keydown"] as const) window.addEventListener(event, stop, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", save);
      panel?.removeEventListener("scroll", save);
      for (const event of ["wheel", "touchstart", "pointerdown", "keydown"] as const) window.removeEventListener(event, stop);
    };
  }, [key, hash, inside]);
  return () => capture.current();
}

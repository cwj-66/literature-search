import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { ApiError, fetchAnalysis } from "./api";
import type { AnalysisState } from "./types";

const ACTIVE = new Set(["pending", "preparing", "analyzing"]);

export function useAnalysisPolling(id: string, refresh: boolean, setState: Dispatch<SetStateAction<AnalysisState | null>>) {
  const [connectionError, setConnectionError] = useState("");
  useEffect(() => { setState(null); }, [id, setState]);
  useEffect(() => {
    if (!id) return;
    let disposed = false;
    let timer = 0;
    let failures = 0;
    let current: AbortController | null = null;
    const schedule = (delay: number) => { window.clearTimeout(timer); timer = window.setTimeout(poll, delay); };
    async function poll() {
      if (disposed || current) return;
      window.clearTimeout(timer);
      if (!navigator.onLine) {
        setConnectionError("网络已断开，恢复联网后会自动查询分析进度。后台任务状态暂未确认。");
        schedule(10000);
        return;
      }
      const controller = new AbortController();
      current = controller;
      const timeout = window.setTimeout(() => controller.abort(), 10000);
      try {
        const data = await fetchAnalysis(id, controller.signal);
        if (disposed) return;
        failures = 0;
        setState(data);
        setConnectionError("");
        if (data.task && ACTIVE.has(data.task.status)) schedule(2000);
      } catch (error) {
        if (disposed) return;
        if (error instanceof ApiError && [400, 401, 403, 404].includes(error.status)) {
          setConnectionError(error.message);
          return;
        }
        failures += 1;
        setConnectionError("暂时无法连接分析服务，正在自动重连。后台任务可能仍在运行，请勿重复提交。");
        schedule(Math.min(2000 * 2 ** Math.min(failures - 1, 3), 15000));
      } finally {
        window.clearTimeout(timeout);
        current = null;
      }
    }
    const reconnect = () => { if (document.visibilityState !== "hidden") void poll(); };
    void poll();
    window.addEventListener("online", reconnect);
    window.addEventListener("focus", reconnect);
    document.addEventListener("visibilitychange", reconnect);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
      current?.abort();
      window.removeEventListener("online", reconnect);
      window.removeEventListener("focus", reconnect);
      document.removeEventListener("visibilitychange", reconnect);
    };
  }, [id, refresh, setState]);
  return connectionError;
}

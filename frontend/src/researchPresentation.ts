import type { ResearchTurn } from "./researchChats";

export function researchResults(turn: ResearchTurn) {
  const task = turn.task;
  if (task?.recommended !== undefined) return { recommended: task.recommended, candidates: task.candidates ?? [], legacy: false };
  // Historical snapshots did not distinguish the pool. Only recover membership
  // explicitly stated in the old answer; do not relabel the first N candidates.
  const pool = turn.papers ?? task?.papers ?? [];
  const answer = task?.answer ?? "";
  const recommended = pool.filter(paper => {
    const escaped = paper.id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return Boolean(paper.title && answer.includes(paper.title)) || new RegExp(`(?<![\\w:.-])${escaped}(?![\\w:.-])`).test(answer);
  });
  const ids = new Set(recommended.map(p => p.id));
  return { recommended, candidates: pool.filter(p => !ids.has(p.id)), legacy: true };
}

export function researchSummary(turn: ResearchTurn) {
  const result = researchResults(turn);
  const searches = turn.task?.search_count ?? (turn.task?.events ?? []).filter(e => e.step === "search").length;
  const reads = Object.keys(turn.task?.reading_status ?? {}).length;
  return [`检索 ${searches} 次`, ...(reads ? [`读取资料 ${reads} 篇`] : []), `推荐 ${result.recommended.length} 篇`].join(" · ");
}

export function researchHistory(turn: ResearchTurn) {
  const papers = researchResults(turn).recommended;
  return `用户：${turn.question}\n助手：${turn.task?.answer ?? ""}${papers.length ? "\n本轮最终推荐（后续‘这些论文’指此集合）：\n" + papers.map(p => `${p.id}：${p.title}`).join("\n") : ""}`;
}

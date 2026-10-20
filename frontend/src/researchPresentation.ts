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
  if (!researchHasActivity(turn)) return "已回复";
  const result = researchResults(turn);
  const searches = turn.task?.search_count ?? (turn.task?.events ?? []).filter(e => e.step === "search").length;
  const reads = Object.keys(turn.task?.reading_status ?? {}).length;
  return [`检索 ${searches} 次`, ...(reads ? [`读取资料 ${reads} 篇`] : []), `推荐 ${result.recommended.length} 篇`].join(" · ");
}

export function researchHasActivity(turn: ResearchTurn) {
  return Boolean(turn.task?.search_count || Object.keys(turn.task?.reading_status ?? {}).length ||
    turn.task?.events?.some(event => ["search", "read", "quotes", "assess", "expand", "synthesize"].includes(event.step)));
}

export function researchHistory(turn: ResearchTurn) {
  const papers = researchResults(turn).recommended;
  const task = turn.task;
  const activity = researchHasActivity(turn) ? "\n上轮任务记录（只描述此次执行，不代表整个领域）：" + JSON.stringify({
    status: task?.status, goal: task?.goal ?? turn.question, queries: task?.queries,
    requested: task?.result_limit, recommended: papers.length,
    candidate_count: task?.candidate_count ?? turn.total, searches: task?.search_count,
    source_errors: task?.source_errors, source_status: task?.source_status,
    source_warnings: task?.source_warnings, stop_reason: task?.stop_reason,
  }) : "";
  return `用户：${turn.question}\n助手：${task?.answer ?? ""}${papers.length ? "\n本轮最终推荐（后续‘这些论文’指此集合；摘要仅为题录证据，不是全文）：\n" + papers.map(p => JSON.stringify({
    id: p.id, title: p.title, year: p.year, url: p.url,
    reason: task?.recommendation_reasons?.[p.id] ?? "历史记录未保存推荐理由",
    abstract: p.abstract?.slice(0, 1200),
  })).join("\n") : ""}${activity}`;
}

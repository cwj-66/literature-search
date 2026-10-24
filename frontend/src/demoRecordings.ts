// Immutable task IDs from the published demo seed, not a conversation-wide flag.
// New questions in the same conversation keep their own timestamps.
const recordedDiscoveryTasks = new Set([
  "0dff833957014ee3b32057b7bac9d6e5", "1785ed0f918c40b1aeb98a75265c7f24",
  "29499d2ccf3f4aab8c59769bc7a1d0a8", "29a9043f56e54604923af749b0a2c824",
  "2ab5ed95709c4e21a79ef54a2548dd56", "3c6f6b00c9e44811a5c736fb9ac6fd2a",
  "3c82ec7f4e6e4d8d912b510fc108d219", "599a5c34982f4397a65b7ea95e343025",
  "742d9e2f480b46a6a73e25a8e660aa84", "792f097ac1334030b8b017343b1044ce",
  "83a89decdb6542c69bf28bd4e8480d74", "91e708399c4d48dc8631559c7d6e7534",
  "c203f1b5df7f4d83a06d5970740ceb8d", "c43b87530d934cb4b8fdfb58f18d780d",
  "cd10d463495b4659911ea68de852cf34", "d8cf461e8fad41499df6b64345b51a58",
  "dc6fadc65a2048c9a97e777d12abd0ad", "f5dd9f9f3bb14fe0a3b4b5aff8ab7419",
]);
export function isDemoDiscoveryTurn(turn: { demo_example?: boolean; task?: { task_id: string } }) {
  return !!turn.demo_example || !!turn.task && recordedDiscoveryTasks.has(turn.task.task_id);
}
const researchDemos: Record<string, string> = {
  "demo-hallucination-v1-method": "2026-10-24T11:22:19.331566+00:00",
  "demo-hallucination-v1-experiments": "2026-10-24T11:24:10.459818+00:00",
  "demo-hallucination-v1-connections": "2026-10-24T11:29:31.747799+00:00",
  "demo-research-v2-method": "2026-10-24T03:24:35.435151+00:00",
  "demo-research-v2-experiments": "2026-10-24T03:33:57.461943+00:00",
  "demo-research-v2-comparison": "2026-10-24T03:30:40.754202+00:00",
  "demo-research-v2-connections": "2026-10-24T03:30:41.399565+00:00",
  "demo-research-20261023-simple": "2026-10-23T13:41:00.042143+00:00",
  "demo-research-20261023-critique": "2026-10-23T13:33:57.793268+00:00",
  "demo-research-20261023-exploration": "2026-10-23T13:37:15.295131+00:00",
};
export function isDemoResearchMessage(message: { id: string; demo_example?: boolean; recorded_demo?: { kind: string } }) {
  return !!message.demo_example || message.recorded_demo?.kind === "recorded_live_model_run"
    || Object.keys(researchDemos).some(id => message.id === `${id}-message`);
}
export function isUnchangedDemoConversation(item: { id: string; updated_at: string }) {
  return researchDemos[item.id] === item.updated_at;
}

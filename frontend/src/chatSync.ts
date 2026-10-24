import type { ResearchChat } from "./researchChats";

function content(chat: ResearchChat) {
  return JSON.stringify({ title: chat.title, pinned: !!chat.pinned, archived: !!chat.archived,
    selectedIds: chat.selectedIds, turns: chat.turns.map(({ demo_example: _demo, ...turn }) => turn) });
}

/** Preserve conflicting edits as a new copy, never overwrite a newer server chat. */
export function reconcileChats(local: ResearchChat[], remote: ResearchChat[], newId = () => crypto.randomUUID()) {
  const pending: ResearchChat[] = [];
  const chats = remote.map(chat => {
    const cached = local.find(item => item.id === chat.id);
    if (!cached?.dirty || content(cached) === content(chat)) return { ...chat, dirty: false };
    if (cached.version === chat.version) {
      const next = { ...cached, version: chat.version };
      pending.push(next); return next;
    }
    pending.push({ ...cached, id: newId(), title: `${cached.title.slice(0, 100)}（本地未保存副本）`,
      titleEdited: true, version: 0, dirty: true, createdAt: Date.now(), updatedAt: Date.now() });
    return { ...chat, dirty: false };
  });
  // A server tombstone stays deleted. Preserve an unsaved draft separately.
  for (const cached of local.filter(item => item.dirty && !remote.some(r => r.id === item.id))) {
    const next = cached.version ? { ...cached, id: newId(), title: `${cached.title.slice(0, 100)}（本地未保存副本）`, version: 0 } : cached;
    pending.push(next);
  }
  return { chats: [...chats, ...pending.filter(p => !chats.some(c => c.id === p.id))], pending };
}

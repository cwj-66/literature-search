/** Bind browser caches to the server-issued user namespace before loading stores. */
export async function initializeSession() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/api/session", { signal: controller.signal });
    if (!response.ok) throw new Error("无法初始化工作区，请稍后重试");
    const { namespace } = await response.json() as { namespace: string | null };
    if (!namespace) return;
    const proto = Storage.prototype;
    const get = proto.getItem, set = proto.setItem, remove = proto.removeItem;
    const prefix = `research-user:${namespace}:`;
    proto.getItem = function (key) { return get.call(this, prefix + key); };
    proto.setItem = function (key, value) { set.call(this, prefix + key, value); };
    proto.removeItem = function (key) { remove.call(this, prefix + key); };
  } finally { clearTimeout(timer); }
}

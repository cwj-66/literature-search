/** Product terms and user-facing labels, independent of API identifiers. */
export const PRODUCT_NAME = "文献检索";
export function pageTitle(page: string) {
  return page.endsWith(PRODUCT_NAME) ? page : `${page} · ${PRODUCT_NAME}`;
}
export function formatMessageTime(value: number | string) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hourCycle:"h23" }).format(date);
}
/** Only editorial prose; never apply to titles, quotations or source metadata. */
export function displayProse(value: string) {
  return value.replaceAll("公开API", "公开 API ").replaceAll("概念 grounded 上下文集合", "有原文依据的概念上下文集合");
}
export function evidenceLabels(items: { evidence_id: string }[]) {
  return new Map(items.map((item, index) => [item.evidence_id, `来源 ${index + 1}`]));
}

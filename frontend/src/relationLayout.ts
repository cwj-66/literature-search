import type { RecommendationItem, RelateSatelliteGroup } from "./types";

export function toggleSelection(selected: string[], key: string): string[] {
  if (selected.includes(key)) return selected.filter((item) => item !== key);
  return [...selected, key];
}

export function parseSeeds(raw: string | null, valid: string[]): string[] {
  if (!raw) return [];
  const allowed = new Set(valid);
  const seen = new Set<string>();
  const result: string[] = [];
  for (const key of raw.split(',')) {
    if (!allowed.has(key) || seen.has(key)) continue;
    seen.add(key);
    result.push(key);
  }
  return result;
}

export const CATEGORY_ORDER = ["citation", "semantic", "keyword"] as const;
export type RelationCategory = (typeof CATEGORY_ORDER)[number];

const CATEGORY_PRIORITY: Record<string, number> = { citation: 0, semantic: 1, keyword: 2 };

export interface SectionEntry {
  item: RecommendationItem;
  alsoIn: RelationCategory[];
}

export interface RelationSection {
  key: RelationCategory;
  status: string;
  matchedCount: number;
  items: SectionEntry[];
  hidden: number;
}

export function resolveFocus(selected: string[], focus: string | null): string | null {
  if (!selected.length) return null;
  return focus && selected.includes(focus) ? focus : selected[0];
}

export function arrowOf(item: Pick<RecommendationItem, "evidence">): "out" | "in" | "both" | null {
  const types = new Set((item.evidence ?? []).map((e) => e.type));
  const out = types.has("out");
  const into = types.has("in");
  if (out && into) return "both";
  if (out) return "out";
  if (into) return "in";
  return null;
}

export function metricText(item: RecommendationItem, category: string): string {
  if (category === "citation") {
    switch (arrowOf(item)) {
      case "out": return "当前论文引用该文";
      case "in": return "该文引用当前论文";
      case "both": return "互引";
      default: return "间接引用（本次抽样）";
    }
  }
  if (category === "semantic") return `语义 ${(item.semantic_score ?? 0).toFixed(2)}`;
  return `共同词 ${item.shared_keywords?.length ?? 0} 个`;
}

export function assignSections(groups: RelateSatelliteGroup[]): RelationSection[] {
  const ranks = new Map<string, Map<RelationCategory, number>>();
  const byCategory = new Map<RelationCategory, RelateSatelliteGroup>();
  for (const group of groups) {
    const key = group.category as RelationCategory;
    if (!CATEGORY_ORDER.includes(key)) continue;
    byCategory.set(key, group);
    group.items.forEach((item, index) => {
      if (!ranks.has(item.id)) ranks.set(item.id, new Map());
      ranks.get(item.id)!.set(key, index);
    });
  }
  const primaryOf = (id: string): RelationCategory => {
    const own = ranks.get(id)!;
    let best: RelationCategory = "semantic";
    let bestRank = Number.POSITIVE_INFINITY;
    for (const key of CATEGORY_ORDER) {
      const rank = own.get(key);
      if (rank === undefined) continue;
      if (rank < bestRank || (rank === bestRank && CATEGORY_PRIORITY[key] < CATEGORY_PRIORITY[best])) {
        best = key;
        bestRank = rank;
      }
    }
    return best;
  };
  return CATEGORY_ORDER.map((key) => {
    const group = byCategory.get(key);
    const items: SectionEntry[] = [];
    let hidden = 0;
    for (const item of group?.items ?? []) {
      if (primaryOf(item.id) === key) {
        const alsoIn = CATEGORY_ORDER.filter((other) => other !== key && ranks.get(item.id)!.has(other));
        items.push({ item, alsoIn });
      } else {
        hidden += 1;
      }
    }
    return { key, status: group?.status ?? "completed", matchedCount: group?.matched_count ?? 0, items, hidden };
  });
}

export type EntityKind = "paper" | "author" | "keyword" | "venue";
export interface GraphAnalysis {
  technical_method: string | null; innovations: string[]; result_id: string;
  analysis_basis: string; model_name: string; generated_at: string;
  source_url: string | null; degradation_reason: string | null;
}
export interface EntityNode {
  id: string; kind: EntityKind; label: string; year?: number | null; paper_id?: string;
  local_id?: string; aliases?: string[]; url?: string | null; external?: boolean; authors?: string[];
  abstract?: string | null; venue?: string | null; sources?: string[];
  provenance?: string; analysis?: GraphAnalysis | null;
}
export interface EntityEdge { source: string; target: string; relation: string; provenance?: string; generated_at?: string; result_id?: string; analysis_basis?: string; source_url?: string | null }
export interface EntityGraph { nodes: EntityNode[]; edges: EntityEdge[]; total_papers: number; scope: string; missing_references?: string[] }
export const RELATIONS: Record<string, { label: string; color: string; dash?: number[] }> = {
  authored_by: { label: "作者署名", color: "#8b78a3" },
  has_keyword: { label: "主题关联 · AI提取", color: "#468e82", dash: [5, 4] },
  published_in: { label: "发表渠道", color: "#b29467", dash: [2, 4] },
  cites: { label: "引用 → 被引用", color: "#4169a7" },
};
export const KIND_LABEL: Record<EntityKind, string> = { paper: "论文", author: "作者", keyword: "关键词", venue: "期刊／会议" };
export const KIND_COLOR: Record<EntityKind, string> = { paper: "#2a53b0", author: "#85599f", keyword: "#23806f", venue: "#a8701c" };

// Public paper identities are real. Topic assignments are curated for this
// prototype and must not be presented as automatic extraction or a field census.
const records: [string, string, number, string[], string[]][] = [
  ["2002.08909", "REALM: Retrieval-Augmented Language Model Pre-Training", 2020, ["Kelvin Guu", "Kenton Lee"], ["检索增强", "预训练"]],
  ["2005.11401", "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks", 2020, ["Patrick Lewis", "Ethan Perez"], ["检索增强", "知识密集任务"]],
  ["2112.04426", "Improving language models by retrieving from trillions of tokens", 2021, ["Sebastian Borgeaud"], ["检索增强", "预训练"]],
  ["2212.10496", "Precise Zero-Shot Dense Retrieval without Relevance Labels", 2022, ["Luyu Gao", "Xueguang Ma"], ["稠密检索", "查询改写"]],
  ["2309.15217", "Ragas: Automated Evaluation of Retrieval Augmented Generation", 2023, ["Shahul Es", "Jithin James"], ["检索增强", "评估", "幻觉"]],
  ["2310.11511", "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection", 2023, ["Akari Asai", "Zeqiu Wu"], ["检索增强", "自我反思", "幻觉"]],
  ["2401.15884", "Corrective Retrieval Augmented Generation", 2024, ["Shi-Qi Yan", "Jia-Chen Gu"], ["检索增强", "检索纠错", "幻觉"]],
  ["2404.16130", "From Local to Global: A Graph RAG Approach to Query-Focused Summarization", 2024, ["Darren Edge", "Ha Trinh"], ["检索增强", "知识图谱", "全局总结"]],
];

export function ragExample(): EntityGraph {
  // Venues checked against PMLR, NeurIPS Proceedings and ACL Anthology.
  const venues: Record<string, string> = { "2002.08909": "ICML", "2005.11401": "NeurIPS", "2212.10496": "ACL", "2309.15217": "EACL" };
  const nodes = new Map<string, EntityNode>();
  const edges: EntityEdge[] = [];
  for (const [arxiv, label, year, authors, keywords] of records) {
    const id = `paper:${arxiv}`;
    nodes.set(id, { id, label, year, kind: "paper", paper_id: `arxiv:${arxiv}`, url: `https://arxiv.org/abs/${arxiv}` });
    for (const [kind, values, relation] of [["author", authors, "authored_by"], ["keyword", keywords, "has_keyword"], ["venue", [venues[arxiv] || "arXiv 预印本"], "published_in"]] as const) {
      for (const value of values) {
        const target = `${kind}:${value}`;
        nodes.set(target, { id: target, label: value, kind });
        edges.push({ source: id, target, relation });
      }
    }
  }
  return { nodes: [...nodes.values()], edges, total_papers: records.length,
    scope: "8篇人工选取的RAG相关论文，年份采用预印本年份，作者仅列部分，主题为演示标注；不代表完整领域或实际热度。" };
}

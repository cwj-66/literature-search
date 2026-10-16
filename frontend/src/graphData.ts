export type EntityKind = "paper" | "author" | "keyword" | "venue";
export interface EntityNode { id: string; kind: EntityKind; label: string; year?: number | null; paper_id?: string; url?: string | null }
export interface EntityEdge { source: string; target: string; relation: string }
export interface EntityGraph { nodes: EntityNode[]; edges: EntityEdge[]; total_papers: number; scope: string }
export const KIND_LABEL: Record<EntityKind, string> = { paper: "论文", author: "作者", keyword: "关键词", venue: "期刊／会议" };
export const KIND_COLOR: Record<EntityKind, string> = { paper: "#4168d8", author: "#a378cc", keyword: "#269d8b", venue: "#d89937" };

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

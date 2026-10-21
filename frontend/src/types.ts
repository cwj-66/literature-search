export interface Paper {
  id: string;
  local_id?: string;
  imported_at?: string;
  title: string;
  authors: string[];
  abstract: string | null;
  year: number | null;
  venue: string | null;
  sources: string[];
  url: string | null;
  pdf_url: string | null;
  citation_count: number | null;
  arxiv_id: string | null;
  doi: string | null;
  semantic_scholar_id: string | null;
  ccf_rank: string | null;
  cas_partition: string | null;
  ccf_status?: string | null;
  ccf_catalog_version?: string | null;
}

export function ccfLabel(paper: Paper): string | null {
  if (paper.ccf_status === "pending") return "CCF 待核验";
  if (paper.ccf_rank) return `CCF ${paper.ccf_rank}`;
  return null;
}

export const SOURCE_LABEL: Record<string, string> = { arxiv: "arXiv", semantic_scholar: "Semantic Scholar", openalex: "OpenAlex", upload: "本地上传" };

export function paperKey(paper: Paper) {
  return paper.local_id ?? paper.id;
}

export interface EvidenceItem {
  evidence_id: string;
  paper_id: string;
  quote: string;
  section?: string | null;
  material?: "original" | "model_summary" | "note";
}

export interface QAResponse {
  answer: string;
  evidence: EvidenceItem[];
  reviews: { claim_id: string; verdict: "supported" | "insufficient" | "contradictory" | "unverifiable" | string; note: string; evidence_ids: string[] }[];
  coverage_gap: boolean;
}

export interface ConversationSummary {
  id: string;
  workspace_id: string;
  title: string;
  paper_ids: string[];
  created_at: string;
  updated_at: string;
}
export interface ConversationMessage {
  id: string;
  question: string;
  papers: { id: string; paper_id: string; title: string }[];
  created_at: string;
  status: "pending" | "completed" | "failed";
  response: QAResponse | null;
  error: string | null;
}
export interface Conversation extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface CompareEvidence {
  paragraph_id: string;
  quote: string;
  section?: string | null;
}

export interface CompareTable {
  dimensions: string[];
  notes: string[];
  rows: { paper_id: string; title: string; cells: { dimension: string; text: string; missing: boolean; evidence_ids: string[]; evidence?: CompareEvidence[] }[] }[];
}

export interface ResearchEvent {
  step: string;
  reason?: string;
  query?: string;
  queries?: string[];
  count?: number;
  paper_id?: string;
  errors?: Record<string, string>;
}

export interface ResearchTask {
  output_mode?: "bibliography" | "research";
  result_limit?: number | null;
  recommended?: Paper[];
  candidates?: Paper[];
  candidate_count?: number;
  recommendation_reasons?: Record<string, string>;
  source_errors?: Record<string, string>;
  source_status?: { source: string; status: string; http_status?: number | null; message?: string; retry_after_seconds?: number }[];
  source_warnings?: string[];
  search_count?: number;
  workflow_stage?: string;
  research_intent?: "auto" | "discover" | "read" | "mixed";
  discovered_paper_ids?: string[];
  reading_status?: Record<string, { status: string; basis: "full_text" | "abstract" | "unavailable"; evidence_count: number }>;
  papers?: Paper[];
  evidence?: { evidence_id: string; paper_id?: string; quote: string; material?: string }[];
  referenced_evidence_ids?: string[];
  task_id: string;
  status: "waiting" | "done" | string;
  goal?: string;
  queries?: string[];
  paper_ids?: string[];
  evidence_ids?: string[];
  gaps?: string[];
  events?: ResearchEvent[];
  api_calls?: number;
  llm_calls?: number;
  deep_reads?: number;
  stop_reason?: string | null;
  interrupt?: { type?: string; plan?: unknown; queries?: string[]; paper_ids?: string[]; tool?: string; hint?: string;
    slots?: { name: string; reason?: string }[] } | null;
  answer?: string | null;
}

export interface SearchResponse {
  pending_sources?: string[];
  total_is_limited?: boolean;
  query: string;
  page: number;
  page_size: number;
  total: number;
  total_estimated: boolean;
  papers: Paper[];
  source_counts: Record<string, number>;
  source_errors: Record<string, string>;
  source_warnings?: Record<string, string>;
  source_cached_at?: Record<string, string>;
  source_retry_after?: Record<string, number>;
}

export interface ImportResult { paper: Paper; created: boolean }
export interface LibraryResponse { papers: Paper[]; total: number; page: number; page_size: number }

export interface AnalysisTask {
  task_id: string;
  status: string;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  reused: boolean;
}

export type ClaimKind = "summary" | "paper_type" | "research_problem" | "main_content" | "technical_method" | "method_step" | "innovation" | "evaluation" | "limitation" | "keyword";
export interface AnalysisClaim {
  kind: ClaimKind;
  text: string;
  evidence: { paragraph_id: string; quote: string }[];
  status: "supported" | "not_stated";
  dataset?: string | null;
  baseline?: string | null;
  metric?: string | null;
  result?: string | null;
  conditions?: string | null;
}

export interface AnalysisResult {
  summary?: string | null;
  paper_type?: string;
  claims?: AnalysisClaim[];
  source_paragraphs?: { id: string; section: string; text: string }[];
  analysis_warnings?: string[];
  content_hash?: string | null;
  research_problem: string;
  main_content: string;
  technical_method: string;
  innovations: string[];
  experimental_results: string;
  limitations: string;
  keywords: string[];
  analysis_basis: "abstract" | "full_text";
  generated_at: string;
  model_name: string;
  prompt_version: string;
  degradation_reason: string | null;
  source_kind: string;
  source_url: string | null;
  truncated: boolean;
  evidence_sections: { title: string; excerpt: string }[];
}

export interface AnalysisState {
  task: AnalysisTask | null;
  result: AnalysisResult | null;
}

export interface AnalyzeEvidence { quote: string; section: string | null; page: string | null; paragraph_id: string | null }
export interface AnalyzeField {
  field: string;
  label: string;
  content: string | null;
  evidence: AnalyzeEvidence[];
  missing: string[];
  status: "found" | "partial" | "not_found";
}
export interface AnalyzePaperItem {
  paper_id: string;
  title: string | null;
  status: "completed" | "partial" | "failed" | "not_found" | "out_of_scope";
  depth: "standard" | "deep";
  depth_applied: "standard" | "deep";
  basis: string | null;
  message: string | null;
  fields: AnalyzeField[];
}
export interface AnalyzePaperResult {
  tool: "analyze_paper";
  workspace_id: string | null;
  depth: "standard" | "deep";
  fields: string[];
  status: "completed" | "partial" | "failed";
  papers: AnalyzePaperItem[];
}

export interface RecommendationReason {
  type: string;
  text: string;
}

export interface RecommendationItem {
  id: string;
  aliases?: string[];
  title: string;
  year: number | null;
  authors?: string[];
  venue?: string | null;
  abstract?: string | null;
  url?: string | null;
  citation_count?: number | null;
  strength: number;
  semantic_score: number | null;
  keyword_score: number;
  citation_score: number | null;
  reasons: RecommendationReason[];
  relations: string[];
  categories?: string[];
  sources?: string[];
  recalled_by?: string[];
  shared_keywords?: string[];
  shared_reference_ids?: string[];
  common_citing_ids?: string[];
  evidence?: { type: string; via: string; source: string }[];
}

export interface RecommendationGroup {
  key: string;
  label: string;
  status: string;
  recall_count: number;
  matched_count: number;
  notes: string[];
  items: RecommendationItem[];
}

export interface Recommendations {
  paper_id: string;
  paper_title: string;
  algorithm_version: string;
  weights: Record<string, number>;
  formula: string;
  note: string;
  warnings: string[];
  computed_at: string;
  items: RecommendationItem[];
  groups?: RecommendationGroup[];
  candidate_count?: number;
  vector_policy?: string;
  search_plan?: { semantic: string[]; keyword: string[]; terms: string[]; planner: string };
  source_status?: { category: string; source: string; status: string; count: number; query?: string; message?: string }[];
}

export type ThemeQuadrant = "motor" | "basic" | "emerging" | "niche";

export interface KnowledgeTheme {
  id: string;
  name: string;
  description: string;
  keywords: string[];
  paper_ids: string[];
  size: number;
  centrality: number;
  density: number;
  quadrant: ThemeQuadrant | null;
  by_year: Record<string, number>;
}

export interface ThemeEvolutionLink {
  from: string;
  to: string;
  weight: number;
  from_period: number;
  to_period: number;
  from_label: string;
  to_label: string;
}

export interface KnowledgeThemesResponse {
  themes: KnowledgeTheme[];
  evolution: { periods: string[]; links: ThemeEvolutionLink[]; reason?: string };
  years: number[];
  total_papers: number;
  analyzed_papers: number;
  scope_note: string;
  scope?: "workspace" | "expanded";
  status?: "ok" | "insufficient";
  status_reason?: string;
}

export interface PaperNetwork {
  paper_id: string;
  paper_title: string;
  algorithm_version: string;
  warnings: string[];
  computed_at: string;
  nodes: { id: string; title: string; year: number | null; center: boolean }[];
  edges: { source: string; target: string; type: string; label: string; directed: boolean; category?: string; strength?: number }[];
}

export interface RelateCenter {
  id: string;
  aliases?: string[];
  local_id: string | null;
  title: string;
  year: number | null;
  authors: string[];
  venue?: string | null;
  citation_count?: number | null;
  color_index: number;
  similarities: Record<string, number | null>;
}

export interface RelateSatelliteGroup {
  center_id: string;
  category: string;
  status: string;
  matched_count: number;
  items: RecommendationItem[];
}

export interface RelateCenterEdge {
  source: string;
  target: string;
  type: string;
  label: string;
  directed: boolean;
  category: string;
  strength?: number | null;
}

export interface RelateResponse {
  pending_ids?: string[];
  precompute?: { paper_id: string; status: string; computed_at: string | null }[];
  centers: RelateCenter[];
  satellites: RelateSatelliteGroup[];
  center_edges: RelateCenterEdge[];
  overlaps: { paper_id: string; center_ids: string[] }[];
  warnings: string[];
  computed_at: string;
}

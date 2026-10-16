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
}

export interface SearchResponse {
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

export interface AnalysisResult {
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

export interface RecommendationReason {
  type: string;
  text: string;
}

export interface RecommendationItem {
  id: string;
  title: string;
  year: number | null;
  strength: number;
  semantic_score: number | null;
  keyword_score: number;
  citation_score: number;
  reasons: RecommendationReason[];
  relations: string[];
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
}

export interface PaperNetwork {
  paper_id: string;
  paper_title: string;
  algorithm_version: string;
  warnings: string[];
  computed_at: string;
  nodes: { id: string; title: string; year: number | null; center: boolean }[];
  edges: { source: string; target: string; type: string; label: string; directed: boolean }[];
}

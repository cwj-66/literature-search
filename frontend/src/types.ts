export interface Paper {
  id: string;
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
}

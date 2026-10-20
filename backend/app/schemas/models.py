from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PaperOut(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    author_ids: list[str | None] = Field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    sources: list[str] = Field(default_factory=list)
    url: str | None = None
    pdf_url: str | None = None
    citation_count: int | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    semantic_scholar_id: str | None = None
    ccf_rank: str | None = None
    cas_partition: str | None = None
    ccf_status: str | None = None
    ccf_catalog_version: str | None = None


class SearchResponse(BaseModel):
    query: str
    page: int
    page_size: int
    total: int
    total_estimated: bool
    papers: list[PaperOut]
    source_counts: dict[str, int]
    source_errors: dict[str, str]
    source_warnings: dict[str, str] = Field(default_factory=dict)
    source_cached_at: dict[str, str] = Field(default_factory=dict)
    source_retry_after: dict[str, int] = Field(default_factory=dict)
    source_status: list[dict] = Field(default_factory=list)
    omitted_unknown_year: int = 0
    pending_sources: list[str] = Field(default_factory=list)
    total_is_limited: bool = False


class ImportRequest(BaseModel):
    external_id: str | None = Field(None, min_length=1, max_length=500)
    url: str | None = Field(None, min_length=1, max_length=500)
    id: str | None = Field(None, min_length=1, max_length=500)
    arxiv_id: str | None = Field(None, min_length=1, max_length=100)
    doi: str | None = Field(None, min_length=1, max_length=300)

    @model_validator(mode="after")
    def one_reference(self):
        if sum(value is not None for value in (self.external_id, self.url, self.id, self.arxiv_id, self.doi)) != 1:
            raise ValueError("请提供一个外部 ID 或论文链接")
        return self

    def reference(self) -> str:
        if self.arxiv_id is not None:
            return "arxiv:" + self.arxiv_id
        if self.doi is not None:
            return "doi:" + self.doi
        return self.external_id or self.url or self.id or ""


class LocalPaperOut(PaperOut):
    local_id: str
    imported_at: str


class ImportResult(BaseModel):
    paper: LocalPaperOut
    created: bool


class LibraryResponse(BaseModel):
    papers: list[LocalPaperOut]
    total: int
    page: int
    page_size: int


class AnalyzeRequest(BaseModel):
    reanalyze: bool = False


class ResearchStart(BaseModel):
    accessible_paper_ids: list[str] | None = Field(None, max_length=500)
    conversation_history: str = Field("", max_length=16000)
    goal: str = Field(min_length=1, max_length=2000)
    year_from: int | None = Field(None, ge=1900, le=2100)
    year_to: int | None = Field(None, ge=1900, le=2100)
    auto_approve: bool = False
    workspace_id: str | None = Field(None, min_length=1, max_length=80)
    scope: Literal["current", "selected", "workspace"] = "selected"
    current_paper_id: str | None = Field(None, min_length=1, max_length=500)
    selected_paper_ids: list[str] = Field(default_factory=list, max_length=50)
    mode: Literal["auto", "react", "plan"] = "auto"
    budget: Literal["fast", "medium", "high"] = "medium"
    background: bool = False
    max_seconds: int | None = Field(None, ge=30, le=3600)


class ResearchResume(BaseModel):
    accessible_paper_ids: list[str] | None = Field(None, max_length=500)
    approved: bool | None = None
    plan: list[dict] | None = None
    queries: list[str] | None = None
    approved_ids: list[str] | None = Field(None, max_length=50)
    abort: bool | None = None
    workspace_id: str | None = Field(None, min_length=1, max_length=80)
    scope: Literal["current", "selected", "workspace"] | None = None
    current_paper_id: str | None = Field(None, min_length=1, max_length=500)
    selected_paper_ids: list[str] | None = Field(None, max_length=50)
    year_from: int | None = Field(None, ge=1900, le=2100)
    year_to: int | None = Field(None, ge=1900, le=2100)
    background: bool = False


class QARequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    paper_ids: list[str] = Field(min_length=1, max_length=40)


class CompareRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=5)
    dimensions: list[str] | None = None


class EvidenceReference(BaseModel):
    paragraph_id: str = Field(min_length=1, max_length=30)
    quote: str = Field(min_length=8, max_length=900)


class AnalysisClaim(BaseModel):
    kind: Literal["summary", "research_problem", "main_content", "technical_method", "method_step", "innovation", "evaluation", "limitation", "keyword", "paper_type"]
    text: str = Field(min_length=1, max_length=2000)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=6)
    dataset: str | None = Field(None, max_length=300)
    baseline: str | None = Field(None, max_length=300)
    metric: str | None = Field(None, max_length=300)
    result: str | None = Field(None, max_length=500)
    conditions: str | None = Field(None, max_length=500)
    status: Literal["supported", "not_stated"] = "not_stated"


class ExtractionOutput(BaseModel):
    claims: list[AnalysisClaim] = Field(max_length=60)


class SourceParagraph(BaseModel):
    id: str
    section: str
    text: str


class AnalysisFields(BaseModel):
    summary: str | None = None
    paper_type: str = "未知"
    claims: list[AnalysisClaim] = Field(default_factory=list)
    source_paragraphs: list[SourceParagraph] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    research_problem: str = Field(min_length=1, max_length=2000)
    main_content: str = Field(min_length=1, max_length=4000)
    technical_method: str = Field(min_length=1, max_length=4000)
    innovations: list[str] = Field(min_length=1, max_length=8)
    experimental_results: str = Field(min_length=1, max_length=4000)
    limitations: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(min_length=1, max_length=12)
    analysis_basis: str

    @model_validator(mode="after")
    def check_items(self):
        self.analysis_basis = self.analysis_basis.strip()
        if self.analysis_basis not in ("abstract", "full_text"):
            raise ValueError("analysis_basis 无效")
        self.research_problem = clean_text(self.research_problem, 2000)
        self.main_content = clean_text(self.main_content, 4000)
        self.technical_method = clean_text(self.technical_method, 4000)
        self.experimental_results = clean_text(self.experimental_results, 4000)
        self.limitations = clean_text(self.limitations, 2000)
        self.innovations = clean_items(self.innovations, 8, 300)
        self.keywords = clean_items(self.keywords, 12, 40)
        return self


def clean_text(value: str, limit: int) -> str:
    value = value.strip()
    if not value or len(value) > limit:
        raise ValueError("字段为空或过长")
    return value


def clean_items(values: list[str], count: int, limit: int) -> list[str]:
    cleaned = [item.strip() for item in values if item and item.strip()]
    if not cleaned or len(cleaned) > count or any(len(item) > limit for item in cleaned):
        raise ValueError("列表为空、过长或含有空项")
    return cleaned


class EvidenceSection(BaseModel):
    title: str
    excerpt: str


class AnalysisTaskOut(BaseModel):
    task_id: str
    status: str
    error: str | None = None
    created_at: str
    finished_at: str | None = None
    reused: bool = False


class AnalysisResultOut(BaseModel):
    summary: str | None = None
    paper_type: str = "未知"
    claims: list[AnalysisClaim] = Field(default_factory=list)
    source_paragraphs: list[SourceParagraph] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)
    content_hash: str | None = None
    research_problem: str
    main_content: str
    technical_method: str
    innovations: list[str]
    experimental_results: str
    limitations: str
    keywords: list[str]
    analysis_basis: str
    generated_at: str
    model_name: str
    prompt_version: str
    degradation_reason: str | None = None
    source_kind: str
    source_url: str | None = None
    truncated: bool = False
    evidence_sections: list[EvidenceSection] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    task: AnalysisTaskOut | None = None
    result: AnalysisResultOut | None = None


class ReasonOut(BaseModel):
    type: str
    text: str


class RecommendationItem(BaseModel):
    id: str
    title: str
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    abstract: str | None = None
    url: str | None = None
    citation_count: int | None = None
    strength: int = Field(ge=0, le=100)
    semantic_score: float | None = None
    keyword_score: float = Field(ge=0, le=1)
    citation_score: float | None = Field(None, ge=0, le=1)
    reasons: list[ReasonOut]
    relations: list[str]
    sources: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    recalled_by: list[str] = Field(default_factory=list)
    shared_keywords: list[str] = Field(default_factory=list)
    shared_reference_ids: list[str] = Field(default_factory=list)
    common_citing_ids: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)


class RecommendationGroup(BaseModel):
    key: str
    label: str
    status: str
    recall_count: int
    matched_count: int
    items: list[RecommendationItem]
    notes: list[str] = Field(default_factory=list)


class RecommendationResponse(BaseModel):
    paper_id: str
    paper_title: str
    algorithm_version: str
    weights: dict[str, float]
    formula: str
    note: str
    warnings: list[str]
    computed_at: str
    items: list[RecommendationItem]
    groups: list[RecommendationGroup] = Field(default_factory=list)
    source_status: list[dict] = Field(default_factory=list)
    search_plan: dict = Field(default_factory=dict)
    vector_policy: str = "ephemeral"
    candidate_count: int = 0


class NetworkNode(BaseModel):
    id: str
    title: str
    year: int | None = None
    center: bool = False


class NetworkEdge(BaseModel):
    source: str
    target: str
    type: str
    label: str
    directed: bool
    category: str | None = None
    strength: float | None = None


class NetworkResponse(BaseModel):
    paper_id: str
    paper_title: str
    algorithm_version: str
    warnings: list[str]
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]
    computed_at: str

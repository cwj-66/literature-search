from pydantic import BaseModel, Field, model_validator


class PaperOut(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
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


class SearchResponse(BaseModel):
    query: str
    page: int
    page_size: int
    total: int
    total_estimated: bool
    papers: list[PaperOut]
    source_counts: dict[str, int]
    source_errors: dict[str, str]


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


class AnalysisFields(BaseModel):
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

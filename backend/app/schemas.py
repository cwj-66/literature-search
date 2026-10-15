from pydantic import BaseModel, Field


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

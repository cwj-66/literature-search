"""search_papers：复用现有学术检索，返回候选论文和来源状态，不自动入库。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas import SearchResponse
from app.services.retrieval import search


class SearchPapersRequest(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    page: int = Field(1, ge=1, le=1000000)
    page_size: int = Field(10, ge=1, le=20)
    sort: Literal["relevance", "date"] = "relevance"
    source: Literal["all", "arxiv", "semantic_scholar"] = "all"
    year_from: int | None = Field(None, ge=1900, le=2100)
    year_to: int | None = Field(None, ge=1900, le=2100)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入关键词")
        return value

    @model_validator(mode="after")
    def check_years(self):
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("起始年份不能晚于结束年份")
        return self


TOOL = {
    "name": "search_papers",
    "description": "按关键词搜索学术论文，返回候选论文 ID、摘要、链接和来源状态。需要新候选、检索式翻页或补充检索时使用；返回结果可能包含相关性不足的条目，使用前应自行按标题/摘要筛选，不得把无关条目当作目标论文。支持年份、来源、排序与分页，不自动导入文献库。中文关键词映射不代表覆盖中文文献库。",
    "parameters": {
        "query": "检索关键词，必填，最多 300 字符；优先使用英文研究术语",
        "page": "页码，从 1 开始，默认 1",
        "page_size": "每页 1～20 篇，默认 10",
        "sort": "relevance（相关性，默认）或 date（时间）",
        "source": "all（默认）、arxiv 或 semantic_scholar",
        "year_from": "起始年份，包含该年，可省略",
        "year_to": "结束年份，包含该年，可省略",
    },
}


async def search_papers(
    query: str,
    page: int = 1,
    page_size: int = 10,
    sort: str = "relevance",
    source: str = "all",
    year_from: int | None = None,
    year_to: int | None = None,
) -> dict:
    request = SearchPapersRequest(
        query=query, page=page, page_size=page_size, sort=sort,
        source=source, year_from=year_from, year_to=year_to,
    )
    normalized = search.normalize_query(request.query)
    result = SearchResponse.model_validate(await search.search_papers(
        normalized, request.page, request.page_size, request.sort,
        request.source, request.year_from, request.year_to,
    )).model_dump(mode="json")
    # 保留上游错误和警告，避免将限流或服务失败误报为没有相关论文。
    degraded = bool(result["source_errors"] or result["source_warnings"])
    status = "partial" if degraded else "completed"
    if result["source_errors"] and not result["papers"] and not result["source_counts"]:
        status = "failed"
    return {"tool": "search_papers", "status": status, "input_query": request.query, **result}

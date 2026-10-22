"""Small, native function contracts for the paper-finding conversation.

No topic parsing, planning schema, ranking model, library writes or full text.
The researcher owns interpretation and selection; these tools own provenance.
"""

import asyncio

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.schemas import PaperOut
from app.services.papers.paper_merge import combine, identity_keys
from app.services.retrieval import search


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300, description="英文短检索式；只表达研究主题，不添加数量指令")
    year_from: StrictInt | None = Field(None, ge=1900, le=2100, description="最低发表年份，包含该年；2023年以来填写整数2023")
    year_to: StrictInt | None = Field(None, ge=1900, le=2100, description="最大发表年份，包含该年；没有限制则省略")

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("query 必须是非空检索式")
        return value.strip()

    @model_validator(mode="after")
    def years(self):
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from 不能晚于 year_to")
        return self


class DetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str = Field(min_length=1, max_length=500, description="本轮搜索工具已经返回的论文ID")


class SubmittedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_id: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1800, description="逐篇中文推荐理由：摘要充分时用2—4句说明需求匹配、摘要中的具体机制、阅读价值或限制，通常100—180字；信息不足则明确依据有限，不凑字数、不编造全文细节")

    @field_validator("reason")
    @classmethod
    def reason_present(cls, value):
        if not value.strip():
            raise ValueError("reason 不能为空")
        return value.strip()


class SubmitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SubmittedPaper] = Field(max_length=50, description="仅提交符合要求的真实候选；不足时不凑数；允许空列表")
    note: str = Field(default="", max_length=6000, description="面向用户的结果说明，解释不足、排除条件或来源限制")


MODELS = {"search_papers": SearchArgs, "get_paper_detail": DetailArgs, "submit_papers": SubmitArgs}
DESCRIPTIONS = {
    "search_papers": "搜索真实论文。返回新候选、更新的已有候选、摘要及各来源状态。根据结果决定是否改词或结束；不会自动推荐或入库。",
    "get_paper_detail": "仅当本轮候选摘要不足以判断相关性时获取详情。不读取全文，不接受搜索未返回的ID。",
    "submit_papers": "提交最终相关论文及逐篇理由并结束搜索。只允许本轮真实候选ID；数量不足可以少交或提交空列表。",
}
FUNCTION_TOOLS = [{"type": "function", "function": {
    "name": name, "description": DESCRIPTIONS[name], "parameters": model.model_json_schema(),
}} for name, model in MODELS.items()]
CHAT_TOOLS = tuple({"module": "discovery", **tool["function"]} for tool in FUNCTION_TOOLS)


def paper_view(record: dict) -> dict:
    return {key: record.get(key) for key in ("id", "title", "authors", "year", "venue", "abstract", "url", "sources")}


def merge_candidates(existing: list[dict], incoming: list[dict], aliases: dict) -> tuple[list[dict], list[dict], list[dict], dict, int]:
    """Use provider identities, keeping already exposed IDs stable across merges."""
    pool = [PaperOut.model_validate(row) for row in existing]
    aliases = dict(aliases)
    fresh, updated, duplicates = set(), set(), 0
    for row in incoming:
        paper = PaperOut.model_validate(row)
        keys = identity_keys(paper) | {paper.id}
        matches = [i for i, old in enumerate(pool) if keys & (identity_keys(old) | {old.id})]
        if not matches:
            pool.append(paper)
            fresh.add(paper.id)
            aliases[paper.id] = paper.id
            continue
        duplicates += 1
        first = matches[0]
        stable_id = pool[first].id
        merged = combine(pool[first], paper)
        retired = {pool[i].id for i in matches[1:]}
        for i in matches[1:]:
            merged = combine(merged, pool[i])
        pool[first] = merged.model_copy(update={"id": stable_id})
        for i in reversed(matches[1:]):
            pool.pop(i)
        aliases = {key: stable_id if value in retired else value for key, value in aliases.items()}
        aliases[paper.id] = stable_id
        fresh.difference_update(retired)
        updated.add(stable_id)
    rows = [paper.model_dump(mode="json") for paper in pool]
    return rows, [r for r in rows if r["id"] in fresh], [r for r in rows if r["id"] in updated - fresh], aliases, duplicates


async def search_candidates(args: SearchArgs) -> dict:
    """The service retains legacy pagination; this agent exposes only necessities."""
    result = {}
    try:
        # Keep completed providers and streamed candidates if another provider
        # spends too long retrying. Closing the iterator cancels pending workers.
        async with asyncio.timeout(65):
            async for result in search.search_papers_stream(args.query, 1, 12, "relevance", "all", args.year_from, args.year_to):
                pass
    except TimeoutError:
        pending = result.get("pending_sources") or ["search"]
        failures = [search.SourceFailure("该来源未在本轮时间内完成，保留其他来源已取得的候选", code="timeout").detail(name)
                    for name in pending]
        result = {**result,
                  "source_status": [r for r in result.get("source_status", []) if r["source"] not in pending] + failures,
                  "source_errors": {**result.get("source_errors", {}), **{r["source"]: r["message"] for r in failures}},
                  "pending_sources": []}
    papers = [PaperOut.model_validate(row).model_dump(mode="json") for row in result.get("papers", [])]
    sources = list(result.get("source_status") or [])
    if not sources:
        # Compatibility for adapters returning the older SearchResponse shape.
        names = set(result.get("source_counts", {})) | set(result.get("source_errors", {})) | set(result.get("source_warnings", {}))
        sources = [{"source": name, "status": "failed" if name in result.get("source_errors", {}) else "completed",
                    "http_status": None, "retryable": False,
                    "message": result.get("source_errors", {}).get(name, ""),
                    "paper_count": sum(name in paper.get("sources", []) for paper in papers)} for name in sorted(names)]
    if not search.semantic_enabled():
        sources.append(search.SourceFailure("Semantic Scholar 已由管理员停用", code="disabled").detail("semantic_scholar"))
    errors = result.get("source_errors") or {}
    notices = list(dict.fromkeys((result.get("source_warnings") or {}).values()))
    degraded = bool(errors or notices or any(row["status"] != "completed" for row in sources))
    status = "partial" if degraded else "completed"
    if not papers and sources and all(row["status"] != "completed" for row in sources):
        status = "failed"
    return {"status": status, "papers": papers, "sources": sources, "notice": notices,
            "source_errors": errors, "source_retry_after": result.get("source_retry_after") or {},
            "omitted_unknown_year": result.get("omitted_unknown_year", 0)}


async def paper_detail(record: dict) -> dict:
    paper = await search.fetch_paper(record["id"], refresh=True)
    if paper is None:
        return {"status": "failed", "error": {"code": "not_found", "retryable": False,
                "message": "来源未返回该论文详情；保留已有题录，不能据此补写摘要。"}}
    known = PaperOut.model_validate(record)
    if not ((identity_keys(paper) | {paper.id}) & (identity_keys(known) | {known.id})):
        return {"status": "failed", "error": {"code": "identity_mismatch", "retryable": False,
                "message": "来源返回的论文标识与候选不一致，未采用该详情。"}}
    merged = combine(known, paper).model_copy(update={"id": known.id, "year": paper.year if paper.year is not None else known.year})
    return {"status": "completed", "paper": merged.model_dump(mode="json")}

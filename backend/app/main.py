import logging
import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import analysis, analysis_store, library, llm, recommend
from .schemas import (
    AnalyzeRequest, AnalysisResponse, PaperOut, SearchResponse, ImportRequest, ImportResult, LocalPaperOut, LibraryResponse,
    RecommendationResponse, NetworkResponse,
)
from .search import SourceFailure, fetch_paper, search_papers, semantic_enabled, semantic_has_key

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pending = await asyncio.to_thread(analysis_store.recover)
    queue = analysis.get_queue()
    for task_id in pending:
        queue.put_nowait(task_id)
    worker = asyncio.create_task(analysis.worker(queue)) if os.getenv("ANALYSIS_WORKER", "1") != "0" else None
    try:
        yield
    finally:
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


app = FastAPI(title="文献检索", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type"],
)


@app.get("/api/papers/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=1, max_length=300),
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(10, ge=1, le=20),
    sort: str = Query("relevance", pattern="^(relevance|date)$"),
    source: str = Query("all", pattern="^(all|arxiv|semantic_scholar)$"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
):
    if source == "semantic_scholar" and (page - 1) * page_size >= 1000:
        raise HTTPException(status_code=400, detail="Semantic Scholar 只提供前 1000 条结果，请缩小检索范围")
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="请输入关键词")
    if year_from and year_to and year_from > year_to:
        raise HTTPException(status_code=400, detail="起始年份不能晚于结束年份")
    query = {"深度学习": "deep learning", "机器学习": "machine learning", "大语言模型": "large language model",
             "知识图谱": "knowledge graph", "检索增强生成": "retrieval augmented generation", "人工智能": "artificial intelligence"}.get(query, query)
    return await search_papers(query, page, page_size, sort, source, year_from, year_to)


@app.get("/api/papers/detail", response_model=PaperOut)
async def paper_detail(id: str = Query(min_length=1, max_length=500)):
    paper_id = id.strip()
    try:
        paper = await fetch_paper(paper_id)
    except SourceFailure as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="论文详情请求超时") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="论文详情请求失败") from None
    if paper is None:
        raise HTTPException(status_code=404, detail="没有找到这篇论文")
    return paper


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "请求参数不完整或格式有误，请检查关键词、年份和页码"})


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/sources")
async def sources():
    enabled = semantic_enabled()
    keyed = semantic_has_key()
    message = ("Semantic Scholar 已配置密钥；上游限流时可重试" if keyed
               else "Semantic Scholar 使用公开批量检索；相关性为本地排序，结果按批次缓存")
    return {"semantic_scholar": {"enabled": enabled, "key_configured": keyed,
            "message": message if enabled else "Semantic Scholar 已由管理员停用"}}


@app.post("/api/papers/import", response_model=ImportResult)
async def import_paper(payload: ImportRequest, response: Response):
    try:
        reference = library.normalize_reference(payload.reference())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    try:
        existing = await asyncio.to_thread(library.get_paper, reference)
        if existing:
            return {"paper": existing, "created": False}
        paper = await paper_detail(reference)
        saved, created = await asyncio.to_thread(library.save_paper, paper, reference)
    except (sqlite3.Error, OSError):
        logging.exception("Local library write failed")
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None
    response.status_code = 201 if created else 200
    return {"paper": saved, "created": created}


@app.get("/api/papers", response_model=LibraryResponse)
async def local_library(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    try:
        return await asyncio.to_thread(library.list_papers, page, page_size)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


def _analysis_error(exc: analysis.ResolveError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@app.post("/api/papers/{paper_id:path}/analyze", response_model=AnalysisResponse)
async def analyze_paper(paper_id: str, response: Response, payload: AnalyzeRequest = Body(default_factory=AnalyzeRequest)):
    try:
        target = await asyncio.to_thread(analysis.resolve_target, paper_id)
        decision = await asyncio.to_thread(
            analysis_store.create_task, target.paper_ref, target.aliases, target.local_id,
            payload.reanalyze, llm.model_name(), llm.PROMPT_VERSION,
        )
    except analysis.ResolveError as exc:
        raise _analysis_error(exc) from None
    except (sqlite3.Error, OSError):
        logging.exception("analysis task create failed")
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None
    if decision.created or (decision.reused and decision.task_row and decision.task_row["status"] == "pending"):
        await analysis.enqueue(decision.task_row["task_id"])
    response.status_code = 202 if decision.created or decision.reused else 200
    return decision.payload()


@app.get("/api/papers/{paper_id:path}/analysis", response_model=AnalysisResponse)
async def paper_analysis(paper_id: str):
    try:
        target = await asyncio.to_thread(analysis.resolve_target, paper_id)
        task, result = await asyncio.to_thread(analysis_store.get_state, target.aliases, target.local_id)
    except analysis.ResolveError as exc:
        raise _analysis_error(exc) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None
    return {"task": task, "result": result}


@app.get("/api/papers/{paper_id:path}/recommendations", response_model=RecommendationResponse)
async def paper_recommendations(
    paper_id: str,
    limit: int = Query(10, ge=1, le=10),
    refresh: bool = Query(False),
):
    try:
        return await recommend.recommendations(paper_id, limit, refresh)
    except analysis.ResolveError as exc:
        raise _analysis_error(exc) from None
    except (sqlite3.Error, OSError):
        logging.exception("recommendation lookup failed")
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


@app.get("/api/papers/{paper_id:path}/network", response_model=NetworkResponse)
async def paper_network(paper_id: str, refresh: bool = Query(False)):
    try:
        return await recommend.network(paper_id, refresh)
    except analysis.ResolveError as exc:
        raise _analysis_error(exc) from None
    except (sqlite3.Error, OSError):
        logging.exception("paper network lookup failed")
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


# Keep this after /search and /detail; path supports legacy arXiv and DOI slashes.
@app.get("/api/papers/{id:path}", response_model=LocalPaperOut)
async def local_paper(id: str):
    try:
        result = await asyncio.to_thread(library.get_paper, id)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None
    if result is None:
        raise HTTPException(status_code=404, detail="论文尚未加入本地分析库")
    return result

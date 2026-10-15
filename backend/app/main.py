import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .schemas import PaperOut, SearchResponse
from .search import SourceFailure, fetch_paper, search_papers, semantic_enabled

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="文献检索", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
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
    return {"semantic_scholar": {"enabled": semantic_enabled(), "message":
        "Semantic Scholar 已启用；供应商限流时将提示并保留其他来源结果" if semantic_enabled()
        else "Semantic Scholar 暂不可用（未配置 API 密钥），当前提供 arXiv 检索"}}

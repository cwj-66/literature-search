import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .schemas import PaperOut, SearchResponse
from .search import SourceFailure, fetch_paper, search_papers

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="文献检索")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="请输入关键词")
    if year_from and year_to and year_from > year_to:
        raise HTTPException(status_code=400, detail="起始年份不能晚于结束年份")
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

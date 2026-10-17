import logging
import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from app.agents import agent
from app.infrastructure import llm
from app.schemas import (
    AnalyzeRequest, AnalysisFields, AnalysisResponse, CompareRequest, ImportRequest, ImportResult,
    LibraryResponse, LocalPaperOut, NetworkResponse, PaperOut, QARequest, RecommendationResponse,
    ResearchResume, ResearchStart, SearchResponse,
)
from app.services.analysis import analysis, analysis_store, attribution
from app.services.analysis.evidence_analysis import UNKNOWN
from app.services.knowledge_graph import knowledge_graph
from app.services.papers import ccf, library, metadata, upload
from app.services.retrieval import hybrid, recommend
from app.services.retrieval.search import SourceFailure, fetch_paper, normalize_query, search_papers, search_papers_stream, semantic_enabled, semantic_has_key
import app.tools.analyze_paper as analyze_tool
import app.tools.index_paper as index_tool

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


@app.get("/api/knowledge-graph")
async def get_knowledge_graph(limit: int = Query(80, ge=1, le=2000)):
    try:
        return await asyncio.to_thread(knowledge_graph.build, limit)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地知识图谱暂不可用，请稍后重试") from None


@app.post("/api/knowledge-graph")
async def get_workspace_knowledge_graph(references: list[str] = Body(max_length=2000)):
    if any(not ref.strip() or len(ref) > 300 for ref in references):
        raise HTTPException(status_code=422, detail="论文标识不能为空或超过 300 字符")
    try:
        return await asyncio.to_thread(knowledge_graph.build, references=references)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地知识图谱暂不可用，请稍后重试") from None


@app.get("/api/papers/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=1, max_length=300),
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(10, ge=1, le=20),
    sort: str = Query("relevance", pattern="^(relevance|date)$"),
    source: str = Query("all", pattern="^(all|arxiv|semantic_scholar)$"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    stream: bool = Query(False),
):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="请输入关键词")
    if year_from and year_to and year_from > year_to:
        raise HTTPException(status_code=400, detail="起始年份不能晚于结束年份")
    query = normalize_query(query)
    if stream:
        async def chunks():
            async for snapshot in search_papers_stream(query, page, page_size, sort, source, year_from, year_to):
                yield SearchResponse.model_validate(snapshot).model_dump_json() + "\n"
        return StreamingResponse(chunks(), media_type="application/x-ndjson", headers={
            "X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform",
        })
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


@app.get("/api/ccf")
async def ccf_info():
    return ccf.info()


@app.post("/api/qa")
async def project_qa(payload: QARequest):
    try:
        papers = await asyncio.to_thread(library.list_recent, 100)
        allowed = {paper.id: paper for paper in papers}
        allowed.update({paper.local_id: paper for paper in papers})
        selected = [allowed[pid] for pid in payload.paper_ids if pid in allowed] if payload.paper_ids else papers
        if payload.paper_ids and len(selected) != len(payload.paper_ids):
            raise HTTPException(status_code=400, detail="只能对当前文献库中的论文提问")
        await asyncio.to_thread(hybrid.index_abstracts, selected)
        ids = [paper.id for paper in selected]
        return await attribution.answer_question(payload.question, ids)
    except llm.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


def _empty_fields() -> AnalysisFields:
    return AnalysisFields(
        research_problem=UNKNOWN, main_content=UNKNOWN, technical_method=UNKNOWN,
        innovations=[UNKNOWN], experimental_results=UNKNOWN, limitations=UNKNOWN,
        keywords=[UNKNOWN], analysis_basis="abstract", claims=[],
    )


@app.post("/api/compare")
async def compare_papers(payload: CompareRequest):
    try:
        rows = []
        for paper_id in payload.paper_ids:
            paper = await asyncio.to_thread(library.get_paper, paper_id)
            if paper is None:
                raise HTTPException(status_code=404, detail="对比论文必须先加入文献库")
            target = await asyncio.to_thread(analysis.resolve_target, paper_id)
            _, result = await asyncio.to_thread(analysis_store.get_state, target.aliases, target.local_id)
            fields = AnalysisFields.model_validate(result) if result else _empty_fields()
            rows.append((paper, fields))
        return attribution.comparison_table(rows, payload.dimensions)
    except analysis.ResolveError as exc:
        raise _analysis_error(exc) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


@app.post("/api/tools/analyze_paper")
async def analyze_paper_tool(payload: analyze_tool.AnalyzePaperRequest):
    try:
        return await analyze_tool.analyze_paper(payload.paper_ids, payload.fields, payload.depth, payload.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except llm.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


@app.post("/api/papers/upload")
async def upload_paper(file: UploadFile = File(), workspace_id: str = Form()):
    data = await file.read(upload.MAX_PDF + 1)
    try:
        return await upload.ingest_pdf(data, file.filename, workspace_id)
    except upload.UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (sqlite3.Error, OSError):
        logging.exception("uploaded paper save failed")
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


@app.post("/api/tools/index_paper")
async def index_paper_tool(payload: index_tool.IndexPaperRequest):
    try:
        return await index_tool.index_paper(payload.paper_id, payload.text, payload.title, payload.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="向量库暂不可用，请稍后重试") from None


@app.post("/api/research/tasks")
async def start_research(payload: ResearchStart):
    if payload.year_from and payload.year_to and payload.year_from > payload.year_to:
        raise HTTPException(status_code=400, detail="起始年份不能晚于结束年份")
    try:
        return await agent.run(payload.goal, payload.year_from, payload.year_to, payload.auto_approve)
    except llm.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None


@app.post("/api/research/tasks/{task_id}/resume")
async def resume_research(task_id: str, payload: ResearchResume = Body(default_factory=ResearchResume)):
    current = await asyncio.to_thread(agent.get_task, task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    resume = {key: value for key, value in {"plan": payload.plan, "queries": payload.queries}.items() if value is not None}
    try:
        return await agent.run(current.get("goal") or "", resume=resume or {"plan": current.get("plan"), "queries": current.get("queries")}, task_id=task_id)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None


@app.get("/api/research/tasks/{task_id}")
async def research_task(task_id: str):
    current = await asyncio.to_thread(agent.get_task, task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    return current


@app.post("/api/papers/{paper_id:path}/metadata")
async def enrich_paper(paper_id: str):
    try:
        paper = await asyncio.to_thread(library.get_paper, paper_id)
        if paper is None:
            paper = await fetch_paper(paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail="没有找到这篇论文")
        enriched, extras = await metadata.enrich(paper)
        return {"paper": enriched, **extras}
    except SourceFailure as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except (sqlite3.Error, OSError):
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

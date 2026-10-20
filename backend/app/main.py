import logging
import asyncio
import json
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
from app.assistant_chats import router as assistant_chat_router
from app.infrastructure import llm
from app.schemas import (
    AnalyzeRequest, AnalysisFields, AnalysisResponse, CompareRequest, ImportRequest, ImportResult,
    LibraryResponse, LocalPaperOut, NetworkResponse, PaperOut, QARequest, RecommendationResponse,
    ResearchResume, ResearchStart, SearchResponse,
)
from app.services.analysis import analysis, analysis_store, attribution
from app.services.knowledge_graph import knowledge_graph, themes
from app.services.papers import ccf, library, metadata, upload
from app.services.retrieval import hybrid, recommend
from app.services.retrieval.search import SourceFailure, fetch_paper, normalize_query, search_papers, search_papers_stream, semantic_enabled, semantic_has_key
import app.tools.analyze_paper as analyze_tool
import app.tools.index_paper as index_tool
import app.tools.parse_paper as parse_tool
import app.tools.search_papers as search_tool
import app.tools.add_papers_to_workspace as workspace_tool
from app.services.papers import workspaces
from app.services.papers import conversations
from app.conversation_api import router as conversation_router
from app.services.papers import processing
from app.tools import research as research_tools, BACKGROUND_TOOLS
from app.tools.research_scope import ToolContext
from pydantic import BaseModel, Field, ValidationError

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(conversations.recover)
    pending = await asyncio.to_thread(analysis_store.recover)
    queue = analysis.get_queue()
    for task_id in pending:
        queue.put_nowait(task_id)
    worker = asyncio.create_task(analysis.worker(queue)) if os.getenv("ANALYSIS_WORKER", "1") != "0" else None
    import_worker = asyncio.create_task(processing.worker()) if os.getenv("PAPER_PROCESSING_WORKER", "1") != "0" else None
    try:
        yield
    finally:
        if import_worker:
            import_worker.cancel()
            with suppress(asyncio.CancelledError):
                await import_worker
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


app = FastAPI(title="文献检索", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(conversation_router)
app.include_router(assistant_chat_router)
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


class CitationFillRequest(BaseModel):
    paper_ids: list[str] = Field(default_factory=list, max_length=80)


@app.post("/api/knowledge-graph/citations")
async def fill_graph_citations(payload: CitationFillRequest):
    if any(not ref.strip() or len(ref) > 300 for ref in payload.paper_ids):
        raise HTTPException(status_code=422, detail="论文标识不能为空或超过 300 字符")
    try:
        return await knowledge_graph.fill_citations(payload.paper_ids)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地知识图谱暂不可用，请稍后重试") from None


@app.post("/api/knowledge-graph/similar")
async def fill_graph_similar(payload: CitationFillRequest):
    if any(not ref.strip() or len(ref) > 300 for ref in payload.paper_ids):
        raise HTTPException(status_code=422, detail="论文标识不能为空或超过 300 字符")
    try:
        return await knowledge_graph.fill_similar(payload.paper_ids)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地知识图谱暂不可用，请稍后重试") from None


@app.get("/api/knowledge-graph/themes")
async def get_knowledge_graph_themes(
    limit: int = Query(80, ge=1, le=2000),
    scope: str = Query("workspace", pattern="^(workspace|expanded)$"),
):
    try:
        return await themes.build(references=None, limit=limit, scope=scope)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地主题分析暂不可用，请稍后重试")


@app.post("/api/knowledge-graph/themes")
async def get_workspace_knowledge_graph_themes(
    references: list[str] = Body(default_factory=list, max_length=2000),
    scope: str = Query("workspace", pattern="^(workspace|expanded)$"),
):
    if any(not ref.strip() or len(ref) > 300 for ref in references):
        raise HTTPException(status_code=422, detail="论文标识不能为空或超过 300 字符")
    try:
        return await themes.build(references=references, scope=scope)
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
        def selected_papers():
            papers = []
            seen = set()
            for ref in payload.paper_ids:
                paper = library.get_paper(ref)
                if paper is None:
                    raise HTTPException(status_code=400, detail="只能对已入库的所选论文提问")
                if paper.id not in seen:
                    seen.add(paper.id)
                    papers.append(paper)
            return papers

        selected = await asyncio.to_thread(selected_papers)
        await asyncio.to_thread(hybrid.index_abstracts, selected)
        ids = [paper.id for paper in selected]
        return await attribution.answer_question(payload.question, ids)
    except llm.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


_RUNNING = {"pending", "preparing", "analyzing"}


def _analysis_block(task: dict | None, result: dict | None) -> str | None:
    status = task.get("status") if task else None
    if status in _RUNNING:
        return "分析中"
    if status == "failed":
        return "分析失败"
    if not result:
        return "未分析"
    return None


@app.post("/api/compare")
async def compare_papers(payload: CompareRequest):
    try:
        ordered: list[str] = []
        for paper_id in payload.paper_ids:
            if paper_id not in ordered:
                ordered.append(paper_id)
        if len(ordered) < 2:
            raise HTTPException(status_code=400, detail="请选择 2–5 篇不同的论文")
        rows = []
        blocked = []
        for paper_id in ordered:
            paper = await asyncio.to_thread(library.get_paper, paper_id)
            if paper is None:
                raise HTTPException(status_code=404, detail="对比论文必须先加入文献库")
            target = await asyncio.to_thread(analysis.resolve_target, paper_id)
            task, result = await asyncio.to_thread(analysis_store.get_state, target.aliases, target.local_id)
            reason = _analysis_block(task, result)
            if reason:
                blocked.append(f"{paper.title}（{reason}）")
                continue
            try:
                fields = AnalysisFields.model_validate(result)
            except ValidationError:
                blocked.append(f"{paper.title}（分析失败）")
                continue
            rows.append((paper, fields))
        if blocked:
            raise HTTPException(status_code=409, detail="以下论文尚未完成分析，不能生成对比表：" + "、".join(blocked))
        table = attribution.comparison_table(rows, payload.dimensions)
        for line, paper_id in zip(table["rows"], ordered):
            line["paper_id"] = paper_id
            for cell in line["cells"]:
                cell["paper_id"] = paper_id
        return table
    except analysis.ResolveError as exc:
        raise _analysis_error(exc) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


@app.post("/api/papers/analyze-batch")
async def analyze_papers_batch(payload: analyze_tool.AnalyzePaperRequest):
    try:
        return await analyze_tool.analyze_paper(payload.paper_ids, payload.fields, payload.depth, payload.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except llm.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="本地分析库暂不可用，请稍后重试") from None


@app.post("/api/tools/search_papers")
async def search_papers_tool(payload: search_tool.SearchPapersRequest):
    return await search_tool.search_papers(**payload.model_dump())


async def _workspace_call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except workspaces.WorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="工作区存储暂不可用，请稍后重试") from None


@app.post("/api/workspaces/papers/add")
async def add_workspace_papers(payload: workspace_tool.AddPapersToWorkspaceRequest):
    try:
        result = await workspace_tool.add_papers_to_workspace(payload.workspace_id, payload.paper_ids)
        for item in result["results"]:
            if item["status"] in ("added", "already_exists"):
                item["processing"] = await asyncio.to_thread(processing.ensure_queued, item["local_id"], True)
        stages = [item["processing"]["status"] for item in result["results"] if item.get("processing")]
        result["processing_summary"] = {stage: stages.count(stage) for stage in set(stages)}
        return result
    except workspaces.WorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="工作区存储暂不可用，请稍后重试") from None


@app.get("/api/workspaces")
async def list_workspaces():
    return await _workspace_call(workspaces.list_workspaces)


@app.post("/api/workspaces/register")
async def register_workspace(payload: workspaces.RegisterWorkspaceRequest):
    return await _workspace_call(workspaces.register, payload.workspace_id, payload.name)


@app.get("/api/workspaces/{workspace_id}/papers")
async def workspace_papers(workspace_id: str):
    return await _workspace_call(workspaces.list_papers, workspace_id)


@app.post("/api/workspaces/{workspace_id}/papers/remove")
async def remove_workspace_papers(workspace_id: str, payload: workspaces.PaperReferences):
    return await _workspace_call(workspaces.remove, workspace_id, payload.paper_ids)


@app.post("/api/workspaces/{workspace_id}/delete")
async def delete_workspace(workspace_id: str):
    return await _workspace_call(workspaces.delete, workspace_id)


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


@app.post("/api/tools/parse_paper")
async def parse_paper_tool(file: UploadFile = File()):
    try:
        data = await file.read(parse_tool.MAX_PDF + 1)
        return await parse_tool.parse_paper(data, file.filename or "paper.pdf")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except parse_tool.mineru.MinerUError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    finally:
        await file.close()


@app.post("/api/tools/index_paper")
async def index_paper_tool(payload: index_tool.IndexPaperRequest):
    try:
        return await index_tool.index_paper(payload.paper_id, payload.text, payload.title, payload.workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="向量库暂不可用，请稍后重试") from None


class ToolInvocation(BaseModel):
    arguments: dict = Field(default_factory=dict)
    context: ToolContext = Field(default_factory=ToolContext)


@app.get("/api/tools")
async def tool_catalog():
    return {"chat_tools": research_tools.CHAT_TOOLS,
            "background_capabilities": [{"name": tool["name"], "description": tool["description"]} for tool in BACKGROUND_TOOLS]}


@app.post("/api/research/tools/{tool_name}")
@app.post("/api/tools/{tool_name}")
async def invoke_research_tool(tool_name: str, payload: ToolInvocation):
    if tool_name not in research_tools.CATALOG:
        raise HTTPException(status_code=404, detail="未知聊天研究工具")
    try:
        return await research_tools.execute(tool_name, payload.arguments, payload.context)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except workspaces.WorkspaceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (SourceFailure, llm.AnalysisError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="研究工具存储暂不可用") from None


@app.post("/api/research/tasks")
async def start_research(payload: ResearchStart):
    if payload.year_from and payload.year_to and payload.year_from > payload.year_to:
        raise HTTPException(status_code=400, detail="起始年份不能晚于结束年份")
    try:
        context = ToolContext.model_validate({key: getattr(payload, key) for key in ToolContext.model_fields})
        if context.workspace_id:
            await asyncio.to_thread(workspaces.require, context.workspace_id)
        starter = agent.start_background if payload.background else agent.run
        return await starter(payload.goal, payload.year_from, payload.year_to, payload.auto_approve,
                             mode=payload.mode, context=context.model_dump(), max_seconds=payload.max_seconds,
                             budget=payload.budget)
    except agent.TaskConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except llm.AnalysisError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None


@app.post("/api/research/tasks/{task_id}/resume")
async def resume_research(task_id: str, payload: ResearchResume = Body(default_factory=ResearchResume)):
    current = await asyncio.to_thread(agent.get_task, task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    resume = payload.model_dump(exclude_none=True, exclude={"background"})
    try:
        return await agent.resume_task(task_id, resume or {"plan": current.get("plan"), "queries": current.get("queries")},
                                       background=payload.background)
    except agent.TaskConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None


@app.post("/api/research/tasks/{task_id}/continue")
async def continue_research(task_id: str, background: bool = Query(False)):
    try:
        result = await agent.continue_task(task_id, background=background)
    except agent.TaskConflict as exc:
        raise HTTPException(status_code=409, detail=exc.message) from None
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None
    if result is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    return result


@app.get("/api/research/tasks/{task_id}")
async def research_task(task_id: str):
    current = await asyncio.to_thread(agent.get_task, task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    return current


@app.get("/api/research/tasks")
async def research_tasks():
    try:
        return await asyncio.to_thread(agent.list_tasks)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None


@app.get("/api/research/tasks/{task_id}/state")
async def research_task_state(task_id: str):
    try:
        recovered = await agent.recover_state(task_id)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None
    if recovered is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    return recovered


@app.post("/api/research/tasks/{task_id}/cancel")
async def cancel_research(task_id: str):
    current = await asyncio.to_thread(agent.get_task, task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    try:
        await asyncio.to_thread(agent.request_cancel, task_id)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None
    return {"task_id": task_id, "cancelled": True}


@app.post("/api/research/tasks/{task_id}/pause")
async def pause_research(task_id: str):
    current = await asyncio.to_thread(agent.get_task, task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")
    try:
        await asyncio.to_thread(agent.request_pause, task_id)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None
    return {"task_id": task_id, "paused": True}


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"


@app.get("/api/research/tasks/{task_id}/events")
async def research_task_events(
    task_id: str,
    goal: str | None = Query(None, min_length=1, max_length=2000),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    auto_approve: bool = Query(False),
    workspace_id: str | None = Query(None, min_length=1, max_length=80),
    scope: str = Query("selected", pattern="^(current|selected|workspace)$"),
    current_paper_id: str | None = Query(None, min_length=1, max_length=500),
    selected_paper_ids: list[str] = Query(default=[]),
):
    """调研任务的 SSE 事件流：已完成任务回放事件；新任务实时流式运行；挂起任务下发中断信息。"""
    try:
        current = await asyncio.to_thread(agent.get_task, task_id)
    except (sqlite3.Error, OSError):
        raise HTTPException(status_code=503, detail="调研任务存储暂不可用，请稍后重试") from None
    if current is None and not goal:
        raise HTTPException(status_code=404, detail="没有找到该调研任务")

    async def events():
        try:
            if current is not None and current.get("status") == "done":
                for event in current.get("events") or []:
                    yield _sse({"type": "event", "task_id": task_id, "event": event})
            elif current is not None and current.get("status") == "waiting":
                yield _sse({"type": "interrupt", "task_id": task_id, "status": "waiting", "interrupt": current.get("interrupt")})
            elif current is not None:
                async for item in agent.watch(task_id):
                    yield _sse(item)
            else:
                context = ToolContext(workspace_id=workspace_id, scope=scope, current_paper_id=current_paper_id, selected_paper_ids=selected_paper_ids)
                if workspace_id:
                    await asyncio.to_thread(workspaces.require, workspace_id)
                async for item in agent.stream_run(goal, year_from, year_to, auto_approve, task_id=task_id, context=context.model_dump()):
                    yield _sse(item)
            yield "data: [DONE]\n\n"
        except (sqlite3.Error, OSError):
            yield _sse({"type": "error", "message": "调研任务存储暂不可用，请稍后重试"})
        except Exception as exc:
            logging.exception("research event stream failed")
            yield _sse({"type": "error", "message": str(exc)[:200]})

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform",
    })


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

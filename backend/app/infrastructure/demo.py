"""Portfolio-authenticated demo, isolated by the existing anonymous visitor identity.

Only the portfolio service creates sessions. Each visitor receives a writable copy
of a sanitized, immutable seed, including separate vectors and graph checkpoints.
The ordinary local development database is unchanged when PAPER_DEMO is disabled.
"""
import asyncio
from contextlib import contextmanager, closing
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse

visitor: ContextVar[str | None] = ContextVar("paper_demo_visitor", default=None)
_seed_lock = threading.Lock()
log = logging.getLogger(__name__)


def enabled():
    return os.getenv("PAPER_DEMO", "0") == "1"


def base_path():
    return Path(os.getenv("PAPER_DB_PATH", str(Path(__file__).resolve().parents[2] / "data" / "papers.sqlite3")))


def root():
    return base_path().parent / "demo-visitors"


def database_path():
    ident = visitor.get()
    return root() / ident / "papers.sqlite3" if ident else base_path()


@contextmanager
def scope(ident):
    token = visitor.set(ident)
    try:
        yield
    finally:
        visitor.reset(token)


def authenticate(request):
    token = request.cookies.get("portfolio_session", "")
    ident = request.cookies.get("gsa_portfolio_visitor", "").partition(".")[0]
    if not token or len(token) > 256 or not re.fullmatch(r"[0-9a-f]{32}", ident):
        return None
    path = Path(os.environ["PAPER_PORTFOLIO_DB"])
    # Session token hashes and visitor binding match the portfolio login service.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        row = db.execute("""SELECT s.visitor FROM portfolio_sessions s
            JOIN visitors v ON v.id=s.visitor
            WHERE s.token_hash=? AND s.visitor=? AND s.expires>? AND v.blocked=0""",
            (hashlib.sha256(token.encode()).hexdigest(), ident, time.time())).fetchone()
    return row[0] if row else None


def initialize(ident):
    target = root() / ident
    with _seed_lock:
        if not (target / "papers.sqlite3").exists():
            if len(list(root().glob("*/papers.sqlite3"))) >= int(os.getenv("PAPER_DEMO_MAX_VISITORS", "100")):
                raise ValueError("体验空间暂满，请联系作者清理后重试")
            seed = base_path().parent / "demo-seed"
            if not (seed / "papers.sqlite3").is_file():
                raise ValueError("演示示例尚未准备好，请稍后重试")
            temporary = target.with_name(ident + ".initializing")
            temporary.mkdir(parents=True, exist_ok=True)
            for name in ("papers.sqlite3", "vectors.faiss"):
                if (seed / name).exists():
                    shutil.copyfile(seed / name, temporary / name)
            (temporary / "generation").write_text(uuid4().hex, encoding="ascii")
            (temporary / "activity").touch()
            temporary.rename(target)
        (target / "activity").touch()


def bootstrap():
    from app.infrastructure.db import connection
    from app.services.papers.library import as_paper
    with connection() as db:
        items = []
        for row in db.execute("SELECT * FROM workspaces ORDER BY created_at, id"):
            papers = {}
            for item in db.execute("""SELECT p.*,wp.added_at FROM workspace_papers wp
                JOIN papers p ON p.local_id=wp.local_id WHERE wp.workspace_id=? ORDER BY wp.added_at""", (row["id"],)):
                paper = as_paper(item).model_dump(mode="json")
                papers[paper["local_id"]] = {"paper": paper, "status": "unread", "note": "", "addedAt": item["added_at"]}
            items.append({"id": row["id"], "name": row["name"], "description": "示例研究工作区，可自由探索",
                          "createdAt": row["created_at"], "cover": "graph" if not items else "notes", "papers": papers})
    from app.infrastructure.llm import model_name
    generation = (database_path().parent / "generation").read_text(encoding="ascii")
    return {"visitor": visitor.get(), "generation": generation, "projects": items, "model": model_name(),
            "model_configured": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()), "retention_days": 7}


def reserve_request(method, path):
    """Bound expensive tasks; reads, polling and saving conversations remain free."""
    expensive = method == "POST" and (path in {"/api/research/tasks", "/api/papers/upload", "/api/papers/import", "/api/compare", "/api/qa"}
        or path.startswith(("/api/tools/", "/api/research/tools/")) or path.endswith(("/resume", "/continue", "/analyze", "/messages"))
        or path.startswith("/api/knowledge-graph/") or path == "/api/papers/analyze-batch")
    if not expensive:
        return True
    if sum(p.stat().st_size for p in database_path().parent.iterdir() if p.is_file()) > 128 * 1024 * 1024:
        return False
    path = base_path().parent / "demo-quota.sqlite3"
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS quota(day TEXT, visitor TEXT, used INTEGER, PRIMARY KEY(day,visitor))")
        db.execute("BEGIN IMMEDIATE")
        day = time.strftime("%Y-%m-%d", time.gmtime())
        used = db.execute("SELECT used FROM quota WHERE day=? AND visitor=?", (day, visitor.get())).fetchone()
        total = db.execute("SELECT COALESCE(SUM(used),0) FROM quota WHERE day=?", (day,)).fetchone()[0]
        if (used and used[0] >= 30) or total >= 300:
            return False
        db.execute("INSERT INTO quota VALUES(?,?,1) ON CONFLICT(day,visitor) DO UPDATE SET used=used+1", (day, visitor.get()))
    return True


class DemoMiddleware:
    """Pure ASGI middleware keeps SSE streaming and context propagation intact."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope_, receive, send):
        if scope_["type"] != "http" or not enabled() or scope_["path"] == "/api/health":
            return await self.app(scope_, receive, send)
        request = Request(scope_)
        try:
            ident = await asyncio.to_thread(authenticate, request)
        except (KeyError, OSError, sqlite3.Error):
            ident = None
        if not ident:
            return await JSONResponse({"detail": "请重新登录作品集", "code": "demo_access_required"}, status_code=401)(scope_, receive, send)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") not in {None, "https://chenwj.click"}:
            return await JSONResponse({"detail": "不允许跨站操作"}, status_code=403)(scope_, receive, send)
        with scope(ident):
            try:
                await asyncio.to_thread(initialize, ident)
            except ValueError as exc:
                return await JSONResponse({"detail": str(exc)}, status_code=503)(scope_, receive, send)
            if request.url.path.startswith("/api/config/"):
                return await JSONResponse({"detail": "演示环境的模型和检索配置由作者维护"}, status_code=403)(scope_, receive, send)
            if request.method == "GET" and request.query_params.get("goal"):
                return await JSONResponse({"detail": "请通过检索助手发起任务，再订阅任务进度"}, status_code=400)(scope_, receive, send)
            if request.method == "GET" and request.url.path == "/api/demo/bootstrap":
                return await JSONResponse(await asyncio.to_thread(bootstrap), headers={"Cache-Control": "no-store"})(scope_, receive, send)
            if not await asyncio.to_thread(reserve_request, request.method, request.url.path):
                return await JSONResponse({"detail": "今日演示额度已用完，请稍后再试"}, status_code=429)(scope_, receive, send)
            await self.app(scope_, receive, send)


def visitor_ids():
    return [p.parent.name for p in sorted(root().glob("*/papers.sqlite3")) if re.fullmatch(r"[0-9a-f]{32}", p.parent.name)]


async def workers():
    """One serial worker per job type across visitors, never one service per visitor."""
    from app.services.analysis import analysis, analysis_store
    from app.services.papers import processing, conversations
    from app.services.retrieval import relation_precompute
    recovered = set()

    async def loop(kind):
        while True:
            for ident in await asyncio.to_thread(visitor_ids):
                with scope(ident):
                    try:
                        if kind == "analysis":
                            if ident not in recovered:
                                await asyncio.to_thread(conversations.recover)
                                await asyncio.to_thread(analysis_store.recover)
                                def recover_relations():
                                    from app.infrastructure.db import connection
                                    with connection() as db, db:
                                        db.execute("UPDATE relation_jobs SET status='queued' WHERE status='running'")
                                await asyncio.to_thread(recover_relations)
                                recovered.add(ident)
                            task = await asyncio.to_thread(analysis_store.next_pending)
                            if task:
                                await analysis.run_task(task)
                        elif kind == "processing":
                            local_id = await asyncio.to_thread(processing._claim)
                            if local_id:
                                try:
                                    result = await processing.process(local_id)
                                    await asyncio.to_thread(processing._finish, local_id, result["status"], result.get("message"), result.get("source_url"))
                                except Exception:
                                    await asyncio.to_thread(processing._finish, local_id, "failed", "论文处理失败，可稍后重试")
                        else:
                            await relation_precompute.run_one()
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("Demo background processing failed: %s", kind)
            await asyncio.sleep(2)

    async def cleanup():
        while True:
            # Seven idle days greatly exceed the bounded task lifetime. Graph
            # connections are released before removing any expired workspace.
            from app.agents import agent
            cutoff = time.time() - 7 * 86400
            for ident in await asyncio.to_thread(visitor_ids):
                folder = root() / ident
                activity = folder / "activity"
                if activity.exists() and activity.stat().st_mtime < cutoff:
                    await agent.close_demo_graph(ident)
                    await asyncio.to_thread(shutil.rmtree, folder)
                    recovered.discard(ident)
            await asyncio.sleep(3600)

    await asyncio.gather(loop("analysis"), loop("processing"), loop("relations"), cleanup())

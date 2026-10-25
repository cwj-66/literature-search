"""Optional anonymous browser sessions with independent databases and credentials."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import threading

from starlette.requests import Request
from starlette.responses import JSONResponse

visitor = ContextVar('research_user', default=None)
_lock = threading.Lock()
COOKIE = 'research_session'


def enabled():
    return os.getenv('PAPER_USER_SESSIONS', '0') == '1'


def base_path():
    return Path(os.getenv('PAPER_DB_PATH', str(Path(__file__).resolve().parents[2]/'data/papers.sqlite3')))


def root():
    return base_path().parent/'users'


def database_path():
    return root()/visitor.get()/'papers.sqlite3' if visitor.get() else base_path()


@contextmanager
def scope(ident):
    token = visitor.set(ident)
    try:
        yield
    finally:
        visitor.reset(token)


def identify(token):
    with _lock:
        path=base_path().parent/'session-secret'
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            try:
                with path.open('xb') as f:f.write(secrets.token_bytes(32))
                path.chmod(0o600)
            except FileExistsError:pass
        secret=path.read_bytes()
        return hmac.new(secret,token.encode(),hashlib.sha256).hexdigest()[:32]


class UserSessionMiddleware:
    def __init__(self, app):self.app=app

    async def __call__(self, scope_, receive, send):
        if scope_['type']!='http' or not enabled() or scope_['path']=='/api/health':
            return await self.app(scope_,receive,send)
        request=Request(scope_)
        value=request.cookies.get(COOKIE,'')
        fresh=not re.fullmatch(r'[A-Za-z0-9_-]{43}',value)
        if fresh:value=secrets.token_urlsafe(32)
        ident=await asyncio.to_thread(identify,value)
        folder=root()/ident
        await asyncio.to_thread(folder.mkdir,parents=True,exist_ok=True,mode=0o700)
        async def scoped_send(message):
            if message['type']=='http.response.start':
                if fresh:
                    cookie=f'{COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000'
                    if request.url.scheme=='https':cookie+='; Secure'
                    message.setdefault('headers',[]).append((b'set-cookie',cookie.encode()))
                message.setdefault('headers',[]).append((b'cache-control',b'no-store'))
            await send(message)
        with scope(ident):
            await self.app(scope_,receive,scoped_send)


def visitor_ids():
    return [p.parent.name for p in root().glob('*/papers.sqlite3') if re.fullmatch(r'[0-9a-f]{32}',p.parent.name)]


async def workers():
    """Bounded serial workers visit each user's durable queues in their scope."""
    from app.services.analysis import analysis,analysis_store
    from app.services.papers import processing,conversations
    from app.services.retrieval import relation_precompute,qa_index
    from app.services.research_fields import evolution,identify as fields
    recovered=set()
    async def loop(kind):
        while True:
            for ident in await asyncio.to_thread(visitor_ids):
                with scope(ident):
                    try:
                        if kind=='analysis':
                            if ident not in recovered:
                                await asyncio.to_thread(conversations.recover)
                                await asyncio.to_thread(analysis_store.recover)
                                await asyncio.to_thread(relation_precompute.recover)
                                await asyncio.to_thread(fields.recover)
                                recovered.add(ident)
                            task=await asyncio.to_thread(analysis_store.next_pending)
                            if task:await analysis.run_task(task)
                        elif kind=='processing':
                            await qa_index.run_one()
                            local_id=await asyncio.to_thread(processing._claim)
                            if local_id:
                                try:
                                    result=await processing.process(local_id)
                                    await asyncio.to_thread(processing._finish,local_id,result['status'],result.get('message'),result.get('source_url'))
                                except Exception:
                                    await asyncio.to_thread(processing._finish,local_id,'failed','论文处理失败，可重试')
                        elif kind=='fields':
                            await fields.run_one()
                            await evolution.run_report()
                        else:await relation_precompute.run_one()
                    except asyncio.CancelledError:raise
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception('User background task failed: %s',kind)
            await asyncio.sleep(2)
    await asyncio.gather(*(loop(k) for k in ['analysis','processing','relations','fields']))

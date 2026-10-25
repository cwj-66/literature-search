import asyncio
import os
from unittest.mock import patch,AsyncMock
import httpx
import pytest
from app.main import app
from app.infrastructure import llm,embeddings
from app.infrastructure.user_scope import scope,identify

@pytest.fixture
def isolated(monkeypatch,tmp_path):
    monkeypatch.setenv('PAPER_DB_PATH',str(tmp_path/'papers.sqlite3'))
    monkeypatch.setenv('PAPER_USER_SESSIONS','1')
    monkeypatch.setenv('DASHSCOPE_API_KEY','host-key-must-never-be-used')
    monkeypatch.setenv('SILICONFLOW_API_KEY','host-embedding-must-never-be-used')
    monkeypatch.setenv('LLM_FALLBACK_STATE','')

def test_two_sessions_have_separate_keys_and_real_call_credentials(isolated):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as a,httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as b:
            aa=await a.get('/api/session');bb=await b.get('/api/session')
            assert aa.json()!=bb.json()
            assert 'HttpOnly' in aa.headers['set-cookie'] and 'Secure' in aa.headers['set-cookie']
            r=await a.post('/api/providers',json={'llm_key':'user-a-synthetic','llm_model':'user-a-model','embedding_key':'a-vector'})
            assert r.status_code==200 and r.json()['llm_key_configured']
            assert 'user-a-synthetic' not in r.text
            s=(await b.get('/api/providers')).json()
            assert not s['llm_key_configured'] and not s['embedding_key_configured']
            await b.post('/api/providers',json={'llm_key':'user-b-synthetic','llm_model':'user-b-model'})
            key_a=a.cookies.get('research_session');key_b=b.cookies.get('research_session')
            client=AsyncMock();client.__aenter__.return_value=client
            client.post.return_value=httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
            with patch.object(llm.httpx,'AsyncClient',return_value=client):
                for cookie in [key_a,key_b]:
                    with scope(identify(cookie)):
                        await llm.complete([{'role':'user','content':'test'}])
            calls=client.post.call_args_list
            assert [c.kwargs['headers']['Authorization'] for c in calls]==['Bearer user-a-synthetic','Bearer user-b-synthetic']
            assert [c.kwargs['json']['model'] for c in calls]==['user-a-model','user-b-model']
            await a.post('/api/providers',json={'llm_key':''})
            assert not (await a.get('/api/providers')).json()['llm_key_configured']
            assert (await b.get('/api/providers')).json()['llm_key_configured']
            with scope(identify(key_a)):
                assert embeddings.credentials()[0]=='a-vector'
                with pytest.raises(llm.AnalysisError,match='未配置'):
                    await llm.complete([])
    asyncio.run(run())

def test_endpoint_validation_protects_hosted_service(isolated):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as client:
            for url in ['http://127.0.0.1/v1','https://localhost/v1','https://api.openai.com@127.0.0.1/v1','https://api.openai.com:8443/v1']:
                assert (await client.post('/api/providers',json={'llm_base':url})).status_code==400
            assert (await client.post('/api/providers',json={'llm_base':'https://api.deepseek.com/v1'})).status_code==200
    asyncio.run(run())

def test_user_reports_and_background_context_are_isolated(isolated):
    from app.services.research_fields.store import global_db
    from app.infrastructure.config import value
    from app.infrastructure.db import connection
    async def run():
        with scope('a'*32):
            with connection() as db,db:db.execute('INSERT INTO app_config VALUES(?,?)',('provider:DASHSCOPE_API_KEY','only-a'))
            async def background():
                await asyncio.sleep(0)
                return value('DASHSCOPE_API_KEY')
            job=asyncio.create_task(background())
        with scope('b'*32):assert value('DASHSCOPE_API_KEY')==''
        assert await job=='only-a'
    asyncio.run(run())

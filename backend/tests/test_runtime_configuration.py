import asyncio
import json
from unittest.mock import patch
import httpx
import pytest
from app.main import app


def test_runtime_reports_flags_without_credentials(monkeypatch):
    monkeypatch.setenv('PAPER_DEMO','0');monkeypatch.setenv('PAPER_PUBLIC_PREVIEW','1')
    monkeypatch.setenv('DASHSCOPE_API_KEY','private-test-key')
    monkeypatch.setenv('SILICONFLOW_API_KEY','private-embedding-key')
    monkeypatch.setenv('MINERU_API_TOKEN','private-parser-token')
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            return await client.get('/api/runtime')
    r=asyncio.run(request());assert r.status_code==200
    assert r.json()['mode']=='public_preview' and r.json()['llm_configured']
    assert all(value not in r.text for value in ['private-test-key','private-embedding-key','private-parser-token'])


def test_public_preview_never_stores_submitted_keys(monkeypatch):
    monkeypatch.setenv('PAPER_DEMO','0');monkeypatch.setenv('PAPER_PUBLIC_PREVIEW','1')
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            return await client.post('/api/config/semantic-scholar',json={'api_key':'never-store-this'})
    with patch('app.main.server_config.set') as save:
        r=asyncio.run(request())
        assert r.status_code==403;save.assert_not_called()


def test_clean_self_hosted_configuration(monkeypatch):
    monkeypatch.setenv('PAPER_DEMO','0');monkeypatch.delenv('PAPER_PUBLIC_PREVIEW',raising=False)
    for name in ['DASHSCOPE_API_KEY','SILICONFLOW_API_KEY','MINERU_API_TOKEN']:monkeypatch.setenv(name,'')
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            return await client.get('/api/runtime')
    data=asyncio.run(request()).json()
    assert data['mode']=='self_hosted'
    assert not any(data[k] for k in ['llm_configured','embedding_configured','parser_configured'])

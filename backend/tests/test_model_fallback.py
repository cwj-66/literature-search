import asyncio
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.infrastructure import llm, model_fallback as fallback


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setenv('LLM_MODEL', 'primary')
    monkeypatch.setenv('LLM_FALLBACK_MODELS', 'deepseek-v4.1-flash-1017,kimi-k3')
    monkeypatch.setenv('LLM_FALLBACK_STATE', str(tmp_path/'models.sqlite3'))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'synthetic')


def test_persists_and_concurrent_failures_advance_only_once(chain):
    assert fallback.current() == 'primary'
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert set(pool.map(fallback.after_quota, ['primary']*5)) == {'deepseek-v4.1-flash-1017'}
    assert fallback.current() == 'deepseek-v4.1-flash-1017'
    assert fallback.after_quota('deepseek-v4.1-flash-1017') == 'kimi-k3'
    assert fallback.after_quota('kimi-k3') is None
    assert fallback.current() == 'kimi-k3'


def test_config_change_resets_chain(chain, monkeypatch):
    fallback.after_quota('primary')
    monkeypatch.setenv('LLM_MODEL', 'new-primary')
    assert fallback.current() == 'new-primary'


def test_only_quota_errors_are_eligible():
    for status, code in [(403,'AllocationQuota.FreeTierOnly'),(429,'insufficient_quota'),(400,'QuotaExhausted')]:
        assert isinstance(llm._response_error(httpx.Response(status,json={'error':{'code':code}})), llm.QuotaError)
    for status, code in [(429,'Throttling'),(403,'AccessDenied'),(500,'InternalError'),(403,'InsufficientBalance')]:
        assert not isinstance(llm._response_error(httpx.Response(status,json={'error':{'code':code}})),llm.QuotaError)


@pytest.mark.parametrize('tools', [None, [{'type':'function','function':{'name':'test'}}]])
def test_json_and_tools_switch_only_after_quota(chain, tools):
    client=AsyncMock();client.__aenter__.return_value=client
    client.post.side_effect=[httpx.Response(403,json={'error':{'code':'AllocationQuota.FreeTierOnly'}}),
                             httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})]
    with patch.object(llm.httpx,'AsyncClient',return_value=client):
        asyncio.run(llm._chat_completion([{'role':'user','content':'test'}],tools))
    assert [c.kwargs['json']['model'] for c in client.post.call_args_list]==['primary','deepseek-v4.1-flash-1017']
    assert llm.model_name()=='deepseek-v4.1-flash-1017'


def test_no_eager_switch_on_success(chain):
    with patch.object(llm, '_request_completion', new=AsyncMock(return_value={'choices':[]})) as request:
        asyncio.run(llm._chat_completion([]))
        request.assert_awaited_once_with([],None,'primary')
    assert llm.model_name()=='primary'


def test_exhausted_chain_is_bounded(chain):
    async def exhausted(*args):
        raise llm.QuotaError('额度不足')
    with patch.object(llm,'_request_completion',new=AsyncMock(side_effect=exhausted)) as request:
        with pytest.raises(llm.QuotaError):
            asyncio.run(llm._chat_completion([]))
    assert [call.args[2] for call in request.call_args_list]==['primary','deepseek-v4.1-flash-1017','kimi-k3']


def test_rate_limit_does_not_change_model(chain):
    client=AsyncMock();client.__aenter__.return_value=client
    client.post.return_value=httpx.Response(429,headers={'x-should-retry':'false'},json={'error':{'code':'Throttling'}})
    with patch.object(llm.httpx,'AsyncClient',return_value=client):
        with pytest.raises(llm.AnalysisError,match='限流'):
            asyncio.run(llm._chat_completion([]))
    assert llm.model_name()=='primary' and client.post.await_count==1

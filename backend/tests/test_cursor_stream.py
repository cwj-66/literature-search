import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from app.services.retrieval import search

class Body(httpx.AsyncByteStream):
    def __init__(self, gate): self.gate=gate
    async def __aiter__(self):
        rows=[{'paperId':str(i),'title':'检索 paper '+str(i),'year':2023,'abstract':'method '+str(i)} for i in range(11)]
        # Split inside a multi-byte UTF-8 character as a real TCP stream may.
        first=('{"total":11,"data":['+','.join(json.dumps(x,ensure_ascii=False) for x in rows[:10])+',').encode()
        for start in range(0,len(first),29): yield first[start:start+29]
        await self.gate.wait()
        yield (json.dumps(rows[10],ensure_ascii=False)+']}').encode()

class CursorStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{'PAPER_DB_PATH':str(Path(self.temp.name)/'db.sqlite3'),'SEMANTIC_SCHOLAR_ENABLED':'true'})
        self.env.start(); self.addCleanup(self.env.stop)
        for cache in (search._CACHE,search._GATES,search._NEXT,search._COOLDOWN,search._PAPERS):cache.clear()

    async def test_same_source_first_page_before_upstream_body_completes(self):
        gate=asyncio.Event()
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Body(gate))))
        with patch.object(search.httpx,'AsyncClient',return_value=client):
            stream=search.search_papers_stream('paper',1,10,'date','semantic_scholar',2023,2023)
            await anext(stream)
            first=await asyncio.wait_for(anext(stream),1)
            self.assertEqual(len(first['papers']),10)
            self.assertEqual(first['pending_sources'],['semantic_scholar'])
            self.assertFalse(gate.is_set())
            gate.set()
            snapshots=[item async for item in stream]
            self.assertEqual(snapshots[-1]['pending_sources'],[])
            self.assertEqual(snapshots[-1]['total'],11)
            self.assertTrue(all(p.abstract for p in snapshots[-1]['papers']))

    async def test_tokens_beyond_1000_and_dedup_across_batches(self):
        calls=[]
        def handler(r):
            token=r.url.params.get('token');calls.append(token)
            start,end=(0,1000) if token is None else (999,1011)
            return httpx.Response(200,json={'total':1011,'token':'second' if token is None else None,'data':[{'paperId':str(i),'title':'paper '+str(i),'year':2023} for i in range(start,end)]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            papers,total=await search.search_semantic_scholar(client,'paper',1000,10,2023,2023,'date')
            self.assertEqual(len(papers),10)
            self.assertEqual(papers[0].id,'s2:1000')
            self.assertEqual(total,1011)
            self.assertEqual(calls,[None,'second'])
            calls.clear()
            await search.search_semantic_scholar(client,'paper',1000,10,2023,2023,'date')
            self.assertEqual(calls,[])

    async def test_disconnect_cancels_upstream_mid_body(self):
        gate=asyncio.Event();closed=asyncio.Event()
        class CancelBody(Body):
            async def aclose(self):closed.set()
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=CancelBody(gate))))
        with patch.object(search.httpx,'AsyncClient',return_value=client):
            stream=search.search_papers_stream('paper',1,10,'date','semantic_scholar',None,None)
            await anext(stream);await anext(stream);await stream.aclose()
            self.assertTrue(closed.is_set())

    async def test_repeated_token_fails_instead_of_looping(self):
        def handler(r):return httpx.Response(200,json={'total':2000,'token':'same','data':[{'paperId':'one','title':'paper'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(search.SourceFailure):await search.search_s2_bulk(client,'paper',None,None,'relevance',1010)

    async def test_public_api_can_open_page_101(self):
        from app.main import app
        from app.schemas import PaperOut
        async def provider(*args):
            return [PaperOut(id="s2:"+str(i),title="paper "+str(i),year=2023,sources=["semantic_scholar"]) for i in range(1010)],2000
        with patch.object(search,"search_semantic_scholar",provider):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                response=await client.get("/api/papers/search",params={"q":"paper","source":"semantic_scholar","page":101})
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json()["papers"]),10)
        self.assertEqual(response.json()["papers"][0]["id"],"s2:1000")

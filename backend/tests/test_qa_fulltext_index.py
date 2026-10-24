import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from app.infrastructure import embeddings
from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.papers import documents,library,processing
from app.services.retrieval import hybrid,qa_index
from app.services.analysis import fulltext


STRUCTURE={"blocks":[
    {"id":"b1","text":"Methods","heading_level":1,"section_id":"s1","page_number":2},
    {"id":"b2","text":"A graph is constructed before retrieving evidence and verifying the answer.","section_id":"s1","page_number":2},
    {"id":"b3","text":"Experiments","heading_level":1,"section_id":"s2","page_number":3},
    {"id":"b4","text":"We measure evidence recall and answer accuracy on the test set.","section_id":"s2","page_number":3}],
    "sections":[{"id":"s1","title":"Methods"},{"id":"s2","title":"Experiments"}]}
TEXT="\n\n".join(b["text"] for b in STRUCTURE["blocks"])


async def encode(texts,**kwargs):
    return {text:[1.,0.,.5] for text in texts}


class FulltextQAIndexTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.env=patch.dict(os.environ,{"PAPER_DB_PATH":str(self.path/'papers.sqlite3'),"SILICONFLOW_EMBEDDING_MODEL":"BAAI/bge-m3"})
        self.env.start()
        self.paper,_=library.save_paper(PaperOut(id="arxiv:2401.12345",title="Graph Study",abstract="Abstract only: graph research."),"arxiv:2401.12345")
        hybrid.index_abstracts([self.paper]);self.before=hybrid.load_chunks([self.paper.id])

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def save(self,text=TEXT,structure=STRUCTURE):
        documents.save(self.paper.local_id,self.paper.id,text,'paper.pdf',structure)

    async def run_job(self):
        with patch.object(embeddings,"embed_many",new=AsyncMock(side_effect=encode)):
            return await qa_index.run_one()

    async def test_document_save_automatically_enqueues_fulltext_qa_without_deep_read(self):
        self.save()
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"pending")
        await self.run_job()
        rows=hybrid.load_chunks([self.paper.id])
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"ready")
        self.assertTrue(rows);self.assertTrue(all(r["basis"]=="full_text" and r["vector"] for r in rows.values()))
        self.assertFalse(any("Abstract only" in r["text"] for r in rows.values()))
        method=next(r for r in rows.values() if r["section"]=="Methods")
        self.assertEqual(method["page_start"],2);self.assertIn("b2",method["paragraph_ids"])

    async def test_real_fts_retrieves_body_after_fulltext_ready(self):
        self.save();await self.run_job()
        with patch.object(hybrid,"vector_search",new=AsyncMock(return_value=[])),patch.object(hybrid.rerank,"rerank",new=AsyncMock(return_value=[(0,.9)])):
            hits=await hybrid.retrieve("graph constructed verifying",[self.paper.id])
        self.assertTrue(hits);self.assertIn("constructed",hits[0]["text"])
        self.assertEqual(hits[0]["basis"],"full_text")

    async def test_repeat_save_and_job_are_idempotent(self):
        self.save()
        with patch.object(embeddings,"embed_many",new=AsyncMock(side_effect=encode)) as embed:
            await qa_index.run_one();first=hybrid.load_chunks([self.paper.id])
            self.save();self.assertFalse(await qa_index.run_one())
        self.assertEqual(embed.await_count,1)
        self.assertEqual(first,hybrid.load_chunks([self.paper.id]))

    async def test_embedding_failure_preserves_abstract_and_can_retry(self):
        self.save()
        with patch.object(embeddings,"embed_many",new=AsyncMock(side_effect=embeddings.EmbeddingError('offline'))):
            await qa_index.run_one()
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"failed")
        self.assertEqual(hybrid.load_chunks([self.paper.id]),self.before)
        with connection() as db,db:db.execute("UPDATE qa_index_jobs SET retry_at='2000-01-01'")
        await self.run_job();self.assertEqual(qa_index.status(self.paper.local_id)["status"],"ready")

    async def test_incomplete_embeddings_never_mark_ready_or_replace(self):
        self.save()
        with patch.object(embeddings,"embed_many",new=AsyncMock(return_value={})):
            await qa_index.run_one()
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"failed")
        self.assertEqual(hybrid.load_chunks([self.paper.id]),self.before)

    async def test_sql_failure_rolls_back_chunks_and_fts_together(self):
        self.save()
        with connection() as db,db:
            db.execute("""CREATE TRIGGER fail_fulltext BEFORE INSERT ON paper_chunks WHEN NEW.basis='full_text'
                BEGIN SELECT RAISE(ABORT,'simulated write failure'); END""")
        await self.run_job()
        self.assertEqual(hybrid.load_chunks([self.paper.id]),self.before)
        self.assertTrue(hybrid.keyword_search("Abstract",[self.paper.id],5))
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"failed")

    async def test_changed_document_cannot_be_overwritten_by_stale_job(self):
        self.save()
        async def changing(texts,**kwargs):
            self.save(TEXT+'\n\nNew revision',None)
            return await encode(texts)
        with patch.object(embeddings,"embed_many",new=AsyncMock(side_effect=changing)):
            await qa_index.run_one()
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"pending")
        self.assertEqual(hybrid.load_chunks([self.paper.id]),self.before)
        await self.run_job()
        self.assertIn('New revision',' '.join(r['text'] for r in hybrid.load_chunks([self.paper.id]).values()))

    async def test_cancelled_job_recovers_without_losing_evidence(self):
        self.save()
        with patch.object(embeddings,"embed_many",new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):await qa_index.run_one()
        self.assertEqual(qa_index.status(self.paper.local_id)["status"],"pending")
        self.assertEqual(hybrid.load_chunks([self.paper.id]),self.before)
        await self.run_job();self.assertEqual(qa_index.status(self.paper.local_id)["status"],"ready")

    async def test_expired_lease_recovers(self):
        self.save();qa_index.claim()
        with connection() as db,db:db.execute("UPDATE qa_index_jobs SET updated_at='2000-01-01'")
        await self.run_job();self.assertEqual(qa_index.status(self.paper.local_id)["status"],"ready")

    async def test_old_parsed_documents_are_backfilled_without_user_action(self):
        self.save()
        with connection() as db,db:db.execute('DELETE FROM qa_index_jobs')
        await self.run_job();self.assertEqual(qa_index.status(self.paper.local_id)["status"],"ready")

    async def test_markdown_fallback_preserves_headings_and_unknown_pages(self):
        self.save('  ## Methods\n\nA long enough method passage.\n\n## Results\n\nA long enough evaluation passage.  ',None)
        await self.run_job()
        rows=hybrid.load_chunks([self.paper.id])
        self.assertEqual({r["section"] for r in rows.values()},{"Methods","Results"})
        self.assertTrue(all(r['page_start'] is None for r in rows.values()))

    async def test_faiss_file_and_indexer_are_not_touched(self):
        path=self.path/'vectors.faiss';path.write_bytes(b'existing relation index')
        self.save()
        with patch('app.tools.index_paper.index_paper',new=AsyncMock(side_effect=AssertionError('FAISS must remain independent'))):
            await self.run_job()
        self.assertEqual(path.read_bytes(),b'existing relation index')

    async def test_legacy_abstract_or_deep_read_cannot_downgrade_ready_fulltext(self):
        self.save();await self.run_job();before=hybrid.load_chunks([self.paper.id])
        prepared=fulltext.PreparedText('Short abstract','abstract','abstract',None,None,{})
        hybrid.index_prepared(self.paper,prepared,self.paper.local_id)
        self.assertEqual(hybrid.load_chunks([self.paper.id]),before)

    async def test_empty_selection_never_falls_back_to_whole_library(self):
        self.assertEqual(hybrid.load_chunks([]),{})
        self.assertEqual(hybrid.keyword_search('graph',[],5),[])
        self.assertEqual(await hybrid.retrieve('graph',[]),[])

    async def test_empty_structure_and_leading_whitespace_do_not_break_revision(self):
        self.save('  ## Methods\n\nBody with actual evidence.  ',{})
        await self.run_job()
        self.assertEqual(qa_index.status(self.paper.local_id)['status'],'ready')

    async def test_model_change_requeues_and_failure_keeps_old_index(self):
        self.save();await self.run_job();before=hybrid.load_chunks([self.paper.id])
        with patch.dict(os.environ,{'SILICONFLOW_EMBEDDING_MODEL':'replacement-model'}),patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=embeddings.EmbeddingError('offline'))):
            await qa_index.run_one()
        self.assertEqual(hybrid.load_chunks([self.paper.id]),before)
        self.assertEqual(qa_index.status(self.paper.local_id)['status'],'failed')

    async def test_parse_success_flows_to_durable_qa_index_without_analysis(self):
        parsed={"text":TEXT,"markdown":TEXT,**STRUCTURE}
        with patch.object(processing,"acquire_paper_fulltext",new=AsyncMock(return_value={"status":"available","pdf":b'%PDF'})),patch.object(processing,"parse_paper",new=AsyncMock(return_value=parsed)),patch.object(processing.index_tool,"index_paper",new=AsyncMock(return_value={"status":"completed","message":"ok"})):
            result=await processing.process(self.paper.local_id)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(qa_index.status(self.paper.local_id)['status'],'pending')
        await self.run_job();self.assertEqual(qa_index.status(self.paper.local_id)['status'],'ready')

    async def test_parse_failure_keeps_abstract_and_does_not_claim_qa_success(self):
        with patch.object(processing,"acquire_paper_fulltext",new=AsyncMock(return_value={"status":"available","pdf":b'%PDF'})),patch.object(processing,"parse_paper",new=AsyncMock(side_effect=RuntimeError('parse failed'))):
            with self.assertRaises(RuntimeError):await processing.process(self.paper.local_id)
        self.assertEqual(hybrid.load_chunks([self.paper.id]),self.before)
        self.assertIsNone(qa_index.status(self.paper.local_id))

    async def test_catalog_alias_upgrade_moves_qa_chunks_to_current_canonical_id(self):
        p,_=library.save_paper(PaperOut(id='s2:legacy',semantic_scholar_id='legacy',doi='10.1234/alias',title='Alias paper'),'s2:legacy')
        documents.save(p.local_id,p.id,TEXT,'paper.pdf',STRUCTURE)
        await self.run_job()
        updated,_=library.save_paper(PaperOut(id='arxiv:2402.00001',arxiv_id='2402.00001',doi='10.1234/alias',title='Alias paper'),'arxiv:2402.00001')
        self.assertEqual(p.local_id,updated.local_id)
        await self.run_job()
        self.assertTrue(hybrid.load_chunks([updated.id]))
        self.assertFalse(hybrid.load_chunks([p.id]))
        self.assertEqual(qa_index.status(p.local_id)['paper_id'],updated.id)

    async def test_neighbors_use_selected_paper_and_same_revision_only(self):
        self.save();await self.run_job()
        rows=list(hybrid.load_chunks([self.paper.id]).values())
        selected=rows[0]
        neighbors=hybrid.adjacent_chunks([selected],[self.paper.id])
        self.assertTrue(neighbors)
        self.assertTrue(all(h['paper_id']==self.paper.id and 'vector' not in h for h in neighbors))
        self.assertEqual(hybrid.adjacent_chunks([selected],['unselected']),[])

    async def test_unusable_structured_blocks_fall_back_to_real_markdown(self):
        self.save('## Methods\n\nActual markdown method evidence.',{'blocks':[{'text':''}]})
        await self.run_job()
        self.assertEqual(qa_index.status(self.paper.local_id)['status'],'ready')
        self.assertTrue(any('Actual markdown' in r['text'] for r in hybrid.load_chunks([self.paper.id]).values()))

    async def test_existing_worker_consumes_qa_job_automatically(self):
        self.save()
        with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=encode)):
            worker=asyncio.create_task(processing.worker())
            try:
                async def ready():
                    while qa_index.status(self.paper.local_id)['status']!='ready':
                        await asyncio.sleep(.02)
                await asyncio.wait_for(ready(),5)
            finally:
                worker.cancel()
                try:await worker
                except asyncio.CancelledError:pass
        self.assertTrue(all(r['basis']=='full_text' for r in hybrid.load_chunks([self.paper.id]).values()))

    async def test_qa_jobs_and_vectors_are_isolated_between_demo_visitors(self):
        from app.infrastructure import demo
        locals={}
        for ident in ['a'*32,'b'*32]:
            with demo.scope(ident):
                p,_=library.save_paper(PaperOut(id='arxiv:2409.00001',title='Scoped',abstract='Public summary'),'arxiv:2409.00001')
                hybrid.index_abstracts([p]);documents.save(p.local_id,p.id,'## Methods\n\nPrivate evidence '+ident,'paper.pdf')
                locals[ident]=p.local_id
        with demo.scope('a'*32):
            await self.run_job()
            self.assertEqual(qa_index.status(locals['a'*32])['status'],'ready')
        with demo.scope('b'*32):
            self.assertEqual(qa_index.status(locals['b'*32])['status'],'pending')
            self.assertTrue(all(r['basis']=='abstract' for r in hybrid.load_chunks(['arxiv:2409.00001']).values()))

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.infrastructure import embeddings
from app.infrastructure.db import connection
from app.schemas import PaperOut
from app.services.papers import documents, library
from app.services.retrieval import hybrid, qa_chunking as chunk, qa_index, qa_workflow
from app.services.analysis import attribution


class ParentChildTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'PAPER_DB_PATH':str(Path(self.temp.name)/'qa.sqlite3'),
            'SILICONFLOW_EMBEDDING_MODEL':'BAAI/bge-m3'})
        self.env.start()
        self.paper,_ = library.save_paper(PaperOut(id='arxiv:2401.12345',title='Actual scope',abstract='Available abstract.'),'arxiv:2401.12345')

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    async def index(self,text,structure=None):
        documents.save(self.paper.local_id,self.paper.id,text,'paper.md',structure)
        with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=lambda texts,**kw:{t:[1.,.5] for t in texts})):
            await qa_index.run_one()
        self.assertEqual(qa_index.status(self.paper.local_id)['status'],'ready')
        return list(hybrid.load_chunks([self.paper.id]).values())

    def test_real_bge_tokenizer_and_input_budget(self):
        config=chunk.Config.configured()
        self.assertEqual((config.child,config.overlap,config.parent),(350,50,1000))
        self.assertEqual(config.embedding_max+chunk.tokenizer().num_special_tokens_to_add(False),8192)
        self.assertGreater(chunk.count('中文语义切分'),1)
        with patch.dict(os.environ,{'QA_CHILD_TOKENS':'9000','QA_PARENT_TOKENS':'10000'}):
            self.assertEqual(chunk.Config.configured().child,8190)

    def test_invalid_parameters_fail_without_silent_estimates(self):
        for values in ({'QA_CHILD_OVERLAP_TOKENS':'350'},{'QA_PARENT_TOKENS':'100'},{'QA_CHILD_TOKENS':'0'}):
            with self.subTest(values=values),patch.dict(os.environ,values),self.assertRaises(ValueError):chunk.Config.configured()

    async def test_real_tokens_sentence_boundaries_paths_and_exact_source_spans(self):
        text='  # Methods\n\n## Retrieval\n\n'+('The retriever scores passages and preserves factual evidence. '*160)+'\n\n## Generation\n\nAnswers cite retrieved sources.  '
        rows=await self.index(text)
        self.assertGreater(len(rows),3)
        for r in rows:
            self.assertEqual(text[r['source_start']:r['source_end']],r['text'])
            self.assertLessEqual(r['token_count'],350)
            self.assertEqual(r['token_count'],chunk.count(r['text']))
            self.assertTrue(r['parent_id'])
        retrieval=next(r for r in rows if r['section']=='Retrieval')
        self.assertEqual(retrieval['section_path'],['Methods','Retrieval'])
        parents=hybrid.parent_chunks(rows,[self.paper.id])
        for p in parents.values():
            self.assertEqual(text[p['source_start']:p['source_end']],p['text'])
            self.assertLessEqual(p['token_count'],1000)
        self.assertEqual(hybrid.parent_chunks(rows,['unselected']),{})
        forged={**rows[0],'source_hash':'wrong revision'}
        self.assertEqual(hybrid.parent_chunks([forged],[self.paper.id]),{})

    async def test_markdown_table_formula_and_caption_are_not_cut(self):
        table='Table 1: Parameter settings\n\n| setting | value |\n| --- | --- |\n| batch | 32 |\n| rate | 0.0001 |'
        formula='$$\nL = \\sum_{i=1}^{N} \\log p(y_i \\mid x_i)\n\n+ \\lambda R(\\theta)\n$$'
        text='# Experiments\n\n'+table+'\n\n'+formula+'\n\nThe rate is fixed in every experiment.'
        rows=await self.index(text)
        self.assertTrue(any(table in r['text'] for r in rows))
        self.assertTrue(any(formula in r['text'] for r in rows))
        self.assertTrue(all('batch' not in r['text'] or table in r['text'] for r in rows))

    async def test_atomic_object_can_exceed_target_but_not_model_limit(self):
        text='# Data\n\n<table><tr><td>'+('numericvalue '*450)+'</td></tr></table>'
        rows=await self.index(text)
        table=next(r for r in rows if '<table>' in r['text'])
        self.assertGreater(table['token_count'],350)
        self.assertTrue(table['text'].endswith('</table>'))
        self.assertLessEqual(table['token_count'],8190)

    async def test_oversized_atomic_failure_keeps_abstract_and_no_partial_parents(self):
        hybrid.index_abstracts([self.paper]);before=hybrid.load_chunks([self.paper.id])
        documents.save(self.paper.local_id,self.paper.id,'# Data\n\n<table>'+('value '*9000)+'</table>','x.md')
        with patch.object(embeddings,'embed_many',new=AsyncMock()) as embed:
            await qa_index.run_one()
        embed.assert_not_awaited()
        self.assertEqual(qa_index.status(self.paper.local_id)['status'],'failed')
        self.assertEqual(hybrid.load_chunks([self.paper.id]),before)
        with connection() as db:self.assertEqual(db.execute('SELECT count(*) FROM qa_parent_chunks').fetchone()[0],0)

    async def test_fts_and_vector_share_child_ids_and_parent_expansion_is_deduplicated(self):
        text='# Methods\n\n'+('Retriever scores candidate evidence. It rejects unsupported text. '*120)
        rows=await self.index(text)
        keyword=hybrid.keyword_search('Retriever',[self.paper.id],100)
        self.assertTrue(keyword);self.assertTrue(set(keyword)<={r['chunk_id'] for r in rows})
        query_vector=AsyncMock(return_value=keyword[:3])
        async def rank(q,docs,top_n):return [(i,.9) for i in range(min(top_n,len(docs)))]
        with patch.object(hybrid,'vector_search',new=query_vector),patch.object(hybrid.rerank,'rerank',new=rank):
            hits=await hybrid.retrieve('Retriever',[self.paper.id],k_final=4)
        self.assertTrue(all(h['keyword_match'] and h['parent_id'] for h in hits))
        coverage=qa_workflow.Coverage(status='sufficient')
        with patch.object(qa_workflow,'plan_question',new=AsyncMock(return_value=qa_workflow.fallback('具体如何实现',[self.paper.id]))), \
             patch.object(qa_workflow,'assess',new=AsyncMock(return_value=coverage)):
            expanded,_=await qa_workflow.retrieve_evidence('具体如何实现',[self.paper.id],[],AsyncMock(return_value=hits))
        parents=[h for h in expanded if h.get('retrieved_child_ids')]
        self.assertTrue(parents)
        self.assertEqual(len(parents),len({h['parent_id'] for h in parents}))
        self.assertLessEqual(sum(len(h['text'])+len(h['paper_id'])+len(h['section'])+100 for h in expanded),24000)
        for e in attribution.evidence_from_chunks(self.paper.id,expanded):
            self.assertEqual(text[e.source_start:e.source_end],e.quote)

    async def test_config_change_queues_migration_and_failure_keeps_old_parents(self):
        text='# Methods\n\n'+('A factual evidence retrieval method is evaluated. '*100)
        rows=await self.index(text);old=hybrid.parent_chunks(rows,[self.paper.id])
        old_revision=qa_index.status(self.paper.local_id)['revision']
        with patch.dict(os.environ,{'QA_CHILD_TOKENS':'200'}):
            with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=RuntimeError('outage'))):
                await qa_index.run_one()
            self.assertEqual(qa_index.status(self.paper.local_id)['status'],'failed')
            self.assertNotEqual(qa_index.status(self.paper.local_id)['revision'],old_revision)
            self.assertEqual(hybrid.parent_chunks(rows,[self.paper.id]),old)
            with connection() as db,db:db.execute("UPDATE qa_index_jobs SET retry_at='2000-01-01'")
            with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=lambda texts,**kw:{t:[1.,.5] for t in texts})):
                await qa_index.run_one()
            current=hybrid.load_chunks([self.paper.id])
            self.assertTrue(all(r['token_count']<=200 for r in current.values()))
            self.assertEqual(hybrid.parent_chunks(rows,[self.paper.id]),{})
            with connection() as db:
                fts_ids=[r[0] for r in db.execute('SELECT chunk_id FROM paper_fts WHERE paper_id=?',(self.paper.id,))]
                self.assertEqual(len(fts_ids),len(set(fts_ids)))
                self.assertEqual(set(fts_ids),set(current))

    async def test_parent_and_fts_swap_roll_back_with_child_write_failure(self):
        rows=await self.index('# Methods\n\nThe retriever verifies sources.')
        parents=hybrid.parent_chunks(rows,[self.paper.id])
        documents.save(self.paper.local_id,self.paper.id,'# Changed\n\nNew method source.','new.md')
        with connection() as db,db:
            db.execute("CREATE TRIGGER reject_child BEFORE INSERT ON paper_chunks BEGIN SELECT RAISE(ABORT,'controlled'); END")
        with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=lambda texts,**kw:{t:[1.,.5] for t in texts})):
            await qa_index.run_one()
        self.assertEqual(hybrid.parent_chunks(rows,[self.paper.id]),parents)
        self.assertEqual(set(hybrid.load_chunks([self.paper.id])),{r['chunk_id'] for r in rows})

    async def test_legacy_revision_rebuild_and_missing_parent_recovery(self):
        text='# Methods\n\nThe retriever keeps evidence and source provenance.'
        await self.index(text)
        with connection() as db,db:
            db.execute("UPDATE qa_index_jobs SET split_config='legacy'")
            db.execute('DELETE FROM qa_parent_chunks')
            db.execute("UPDATE paper_chunks SET parent_id=NULL,split_version=?",(hybrid.STRUCTURED_SPLIT_VERSION,))
        self.assertTrue(hybrid.load_chunks([self.paper.id]))
        with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=lambda texts,**kw:{t:[1.,.5] for t in texts})):
            await qa_index.run_one()
        rows=list(hybrid.load_chunks([self.paper.id]).values())
        self.assertTrue(all(r['parent_id'] for r in rows))
        with connection() as db,db:db.execute('DELETE FROM qa_parent_chunks')
        with patch.object(embeddings,'embed_many',new=AsyncMock(side_effect=lambda texts,**kw:{t:[1.,.5] for t in texts})):
            await qa_index.run_one()
        self.assertTrue(hybrid.parent_chunks(rows,[self.paper.id]))

    async def test_bibliography_is_not_used_as_own_method_but_can_be_requested(self):
        text='# Methods\n\nOur model performs evidence verification.\n\n# References\n\nOther Author. Evidence verification method.'
        await self.index(text)
        async def rank(q,docs,top_n):return [(i,.9) for i in range(min(top_n,len(docs)))]
        with patch.object(hybrid,'vector_search',new=AsyncMock(return_value=[])),patch.object(hybrid.rerank,'rerank',new=rank):
            body=await hybrid.retrieve('evidence verification',[self.paper.id])
            refs=await hybrid.retrieve('references evidence verification',[self.paper.id])
        self.assertTrue(body);self.assertTrue(all(h['section']!='References' for h in body))
        self.assertTrue(any(h['section']=='References' for h in refs))

    def test_long_atomic_child_is_not_cut_to_4000_chars_for_rerank(self):
        text='<table>'+('parameter '*600)+'</table>'
        self.assertGreater(len(text),4000)
        self.assertEqual(hybrid.rerank_documents('parameter',[{'parent_id':'p','text':text}]),[text])

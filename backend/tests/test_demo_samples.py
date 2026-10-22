import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.infrastructure import demo, demo_samples as samples
from app.infrastructure.db import connection
from app.services.research_fields import store


class DemoSampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'PAPER_DB_PATH':str(Path(self.temp.name)/'papers.sqlite3')})
        self.env.start()
        self.scope = demo.scope('a'*32)
        self.scope.__enter__()
        with connection() as db, db:
            for wid,name,examples in [('pmvcf313hr4xt','RAG研究',samples.RAG),('pmvci88eig1qi','大模型幻觉',samples.HALLUCINATION)]:
                db.execute('INSERT INTO workspaces VALUES(?,?,?)',(wid,name,'2026-10-22'))
                for ref in dict.fromkeys(e[0] for e in examples):
                    paper={'id':ref,'title':samples.SOURCES[ref]['name'],'year':2024,'authors':[], 'source':'arxiv'}
                    db.execute('INSERT INTO papers VALUES(?,?,?)',(ref,json.dumps(paper),'2026-10-22'))
                    db.execute('INSERT INTO workspace_papers VALUES(?,?,?)',(wid,ref,'2026-10-22'))

    def tearDown(self):
        self.scope.__exit__(None,None,None)
        self.env.stop()
        self.temp.cleanup()

    def test_both_interfaces_have_one_three_five_grounded_turns(self):
        samples.install()
        with connection() as db:
            for wid in ('pmvcf313hr4xt','pmvci88eig1qi'):
                chats=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM assistant_chats WHERE workspace_id=? ORDER BY id',(wid,))]
                self.assertEqual([len(c['turns']) for c in chats],[1,3,5])
                for chat in chats:
                    self.assertTrue(chat['title'].startswith('示例'))
                    for turn in chat['turns']:
                        self.assertFalse(turn['task']['events']) # no fabricated search/model execution
                        self.assertEqual(chat['selectedIds'],[])
                        recommended=turn['task']['recommended']
                        self.assertEqual(len(recommended),1)
                        paper=recommended[0]
                        self.assertTrue(paper['authors'])
                        self.assertTrue(paper['abstract'])
                        self.assertEqual(paper['url'],'https://arxiv.org/abs/'+paper['id'].split(':')[1])
                        self.assertFalse(db.execute('SELECT 1 FROM papers WHERE local_id=?',(paper['id'],)).fetchone())
                counts=[r[0] for r in db.execute('SELECT count(*) FROM research_conversations c JOIN research_messages m ON m.conversation_id=c.id WHERE workspace_id=? GROUP BY c.id ORDER BY c.id',(wid,))]
                self.assertEqual(counts,[1,3,5])

    def test_install_preserves_edits_deletions_and_paper_counts(self):
        samples.install()
        with connection() as db,db:
            before=db.execute('SELECT count(*) FROM papers').fetchone()[0]
            db.execute("UPDATE assistant_chats SET deleted=1,payload='{}' WHERE id='demo-reviewed-1'")
            db.execute("DELETE FROM research_conversations WHERE id LIKE 'demo-reviewed-1-%'")
        samples.install()
        with connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM papers').fetchone()[0],before)
            self.assertEqual(db.execute('SELECT sum(deleted) FROM assistant_chats').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM research_conversations').fetchone()[0],4)

    def test_multiple_fields_are_grounded_and_do_not_create_reports(self):
        samples.install()
        result=store.workspace_fields('pmvcf313hr4xt')
        self.assertEqual({f['canonical_name'] for f in result['fields']},{'Information Retrieval','Multi-Hop Question Answering','Retrieval-Augmented Generation'})
        self.assertTrue(all(f['analysis']['report'] is None for f in result['fields']))
        with connection() as db:
            for row in db.execute('SELECT payload FROM paper_fields'):
                source=json.loads(row['payload'])['demo_provenance']
                self.assertTrue(source['url'].startswith('https://arxiv.org/abs/'))
                self.assertIn(source['quote'],{s['quote'] for s in samples.SOURCES.values()})

    def test_removed_sample_is_not_reimported(self):
        with connection() as db,db:
            db.execute("DELETE FROM workspace_papers WHERE local_id='arxiv:2407.02485'")
        samples.install()
        with connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM assistant_chats WHERE workspace_id='pmvcf313hr4xt'").fetchone()[0],0)

    def test_another_visitor_gets_no_unshared_history(self):
        samples.install()
        with demo.scope('b'*32), connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_conversations').fetchone()[0],0)

    def test_upgrade_replaces_only_untouched_old_research_examples(self):
        from app.assistant_chats import ensure
        wid='pmvcf313hr4xt'
        with connection() as db,db:
            ensure(db)
            old={'id':'demo-reviewed-1','title':'示例 · 阅读起点 · 1 轮','turns':[
                {'demo_example':True,'question':samples.RAG[0][1],'task':{'answer':samples.RAG[0][2]}}]}
            db.execute('INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)',(wid,'demo-reviewed-1',json.dumps(old),1,'old'))
            custom={'id':'demo-reviewed-2','title':'我的检索','turns':[{'question':'我的新问题'}]}
            db.execute('INSERT INTO assistant_chats VALUES(?,?,?,?,0,?)',(wid,'demo-reviewed-2',json.dumps(custom),3,'old'))
        samples.install()
        with connection() as db:
            updated=db.execute('SELECT payload,version FROM assistant_chats WHERE workspace_id=? AND id=?',(wid,'demo-reviewed-1')).fetchone()
            self.assertIn('主题检索',json.loads(updated['payload'])['title'])
            self.assertEqual(updated['version'],2)
            preserved=json.loads(db.execute('SELECT payload FROM assistant_chats WHERE workspace_id=? AND id=?',(wid,'demo-reviewed-2')).fetchone()[0])
            self.assertEqual(preserved,custom)

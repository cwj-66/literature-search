import json
import sqlite3
from app.infrastructure import demo_hallucination as demo
from app.infrastructure.demo_samples import HALLUCINATION


def database():
    d=sqlite3.connect(':memory:')
    d.executescript('''CREATE TABLE papers(local_id TEXT,data TEXT);
      CREATE TABLE workspace_papers(workspace_id TEXT,local_id TEXT);
      CREATE TABLE research_conversations(id TEXT PRIMARY KEY,workspace_id TEXT,title TEXT,paper_ids TEXT,created_at TEXT,updated_at TEXT);
      CREATE TABLE research_messages(id TEXT,conversation_id TEXT,payload TEXT);''')
    for ref in dict.fromkeys(r for r,_,_ in HALLUCINATION):
        d.execute('INSERT INTO papers VALUES(?,?)',(ref,json.dumps({'id':ref})))
        d.execute('INSERT INTO workspace_papers VALUES(?,?)',(demo.WID,ref))
    for i,(start,n,label) in enumerate([(0,1,'阅读起点'),(1,3,'方法解读'),(4,5,'证据边界')],1):
        refs=list(dict.fromkeys(r for r,_,_ in HALLUCINATION[start:start+n]));cid=f'demo-reviewed-{i}-{demo.WID}'
        d.execute('INSERT INTO research_conversations VALUES(?,?,?,?,?,?)',(cid,demo.WID,f'示例 · {label} · {n} 轮',json.dumps(refs),'old','old'))
        for j,(r,q,a) in enumerate(HALLUCINATION[start:start+n]):
            m={'question':q,'response':{'answer':a+' [E0001]'},'papers':[{'paper_id':r}],'demo_example':True}
            d.execute('INSERT INTO research_messages VALUES(?,?,?)',(str(j),cid,json.dumps(m)))
    return d


def sample():
    return [{'id':'new','title':'评测与缓解','selected':['arxiv:2409.11353'],'model':'synthetic',
      'messages':[{'id':'real','question':'method','status':'completed','created_at':'now',
       'papers':[{'id':'source-local','paper_id':'arxiv:2409.11353'}],'response':{'answer':'fixture'}}]}]


def test_replace_templates_and_do_not_restore_user_deletions(monkeypatch):
    monkeypatch.setattr(demo,'recordings',sample);d=database();demo.install_into(d)
    assert d.execute('SELECT id FROM research_conversations').fetchall()==[('new',)]
    m=json.loads(d.execute('SELECT payload FROM research_messages').fetchone()[0])
    assert m['papers'][0]['id']=='arxiv:2409.11353' and m['recorded_demo']['kind']=='recorded_live_model_run'
    d.execute('DELETE FROM research_conversations');demo.install_into(d)
    assert d.execute('SELECT count(*) FROM research_conversations').fetchone()[0]==0


def test_preserve_renames_appends_and_modified_answers(monkeypatch):
    monkeypatch.setattr(demo,'recordings',sample);d=database()
    d.execute('UPDATE research_conversations SET title=? WHERE id=?',('user title',f'demo-reviewed-1-{demo.WID}'))
    d.execute('INSERT INTO research_messages VALUES(?,?,?)',('user',f'demo-reviewed-2-{demo.WID}','{}'))
    d.execute('UPDATE research_messages SET payload=? WHERE conversation_id=? AND id=?',('{}',f'demo-reviewed-3-{demo.WID}','0'))
    demo.install_into(d)
    assert d.execute('SELECT count(*) FROM research_conversations').fetchone()[0]==4


def test_missing_papers_are_not_restored(monkeypatch):
    monkeypatch.setattr(demo,'recordings',sample);d=database();d.execute('DELETE FROM workspace_papers')
    demo.install_into(d)
    assert not d.execute("SELECT 1 FROM research_conversations WHERE id='new'").fetchone()


def test_changed_paper_selection_is_preserved(monkeypatch):
    monkeypatch.setattr(demo,'recordings',sample);d=database()
    cid=f'demo-reviewed-1-{demo.WID}'
    d.execute('UPDATE research_conversations SET paper_ids=? WHERE id=?',('[]',cid))
    demo.install_into(d)
    assert d.execute('SELECT paper_ids FROM research_conversations WHERE id=?',(cid,)).fetchone()==('[]',)


def test_recordings_have_reviewed_evidence_for_every_selected_paper():
    import re
    items=demo.recordings()
    assert {i['title']:len(i['messages']) for i in items}=={'幻觉标注与检测':1,'评测与缓解':3,'四篇论文的关联':3}
    for item in items:
        for m in item['messages']:
            assert m['status']=='completed'
            r=m['response'];ev={e['evidence_id']:e for e in r['evidence']}
            cited=set(re.findall(r'E\d{4,}',r['answer']))
            assert cited and cited<=ev.keys()
            assert {ev[c]['paper_id'] for c in cited}==set(item['selected'])
            assert {e['paper_id'] for e in ev.values()}<=set(item['selected'])
            assert any(x['verdict']=='supported' for x in r['reviews'])

import json
import sqlite3
from app.infrastructure import demo_research_refresh as refresh
from app.infrastructure.demo_samples import RAG


def db():
    d=sqlite3.connect(':memory:')
    d.executescript('''CREATE TABLE papers(local_id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE workspace_papers(workspace_id TEXT,local_id TEXT);
        CREATE TABLE research_conversations(id TEXT PRIMARY KEY,workspace_id TEXT,title TEXT,paper_ids TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE research_messages(id TEXT,conversation_id TEXT,payload TEXT);''')
    d.execute('INSERT INTO papers VALUES(?,?)',('rank',json.dumps({'id':'arxiv:2407.02485'})))
    d.execute('INSERT INTO workspace_papers VALUES(?,?)',(refresh.WID,'rank'))
    for index,(start,count,label) in enumerate([(0,1,'阅读起点'),(1,3,'方法解读'),(4,5,'证据边界')],1):
        cid=f'demo-reviewed-{index}-{refresh.WID}'
        d.execute('INSERT INTO research_conversations VALUES(?,?,?,?,?,?)',(cid,refresh.WID,f'示例 · {label} · {count} 轮','[]','old','old'))
        for i,(_,q,_) in enumerate(RAG[start:start+count]):
            d.execute('INSERT INTO research_messages VALUES(?,?,?)',(str(i),cid,json.dumps({'question':q,'demo_example':True})))
    return d


def sample():
    return [{'id':'new-demo','title':'方法解读','selected':['arxiv:2407.02485'],'model':'test-model',
             'messages':[{'id':'q1','question':'explain method','status':'completed','created_at':'now','response':{'answer':'real run fixture'},'papers':[{'id':'original','paper_id':'arxiv:2407.02485'}]}]}]


def test_templates_replaced_and_new_messages_remapped(monkeypatch):
    monkeypatch.setattr(refresh,'recordings',sample)
    d=db();assert refresh.install_into(d)
    assert d.execute('SELECT count(*) FROM research_conversations').fetchone()[0]==1
    m=json.loads(d.execute('SELECT payload FROM research_messages').fetchone()[0])
    assert m['papers'][0]['id']=='rank' and m['recorded_demo']['kind']=='recorded_live_model_run'
    d.execute('DELETE FROM research_conversations');refresh.install_into(d)
    assert d.execute('SELECT count(*) FROM research_conversations').fetchone()[0]==0


def test_modified_templates_and_titles_are_preserved(monkeypatch):
    monkeypatch.setattr(refresh,'recordings',sample)
    d=db();cid=f'demo-reviewed-3-{refresh.WID}'
    d.execute('INSERT INTO research_messages VALUES(?,?,?)',('user',cid,json.dumps({'question':'my question'})))
    d.execute('UPDATE research_conversations SET title=? WHERE id=?',('my title',f'demo-reviewed-2-{refresh.WID}'))
    refresh.install_into(d)
    assert d.execute('SELECT title FROM research_conversations WHERE id=?',(cid,)).fetchone()
    assert d.execute("SELECT title FROM research_conversations WHERE title='my title'").fetchone()
    assert d.execute('SELECT count(*) FROM research_conversations').fetchone()[0]==3


def test_missing_paper_is_not_restored(monkeypatch):
    monkeypatch.setattr(refresh,'recordings',sample)
    d=db();d.execute('DELETE FROM workspace_papers');refresh.install_into(d)
    assert d.execute('SELECT count(*) FROM research_conversations').fetchone()[0]==0


def test_short_names_preserve_user_renames_and_existing_content(monkeypatch):
    monkeypatch.setattr(refresh,'recordings',sample)
    d=db()
    for i,(cid,(old,_)) in enumerate(refresh.RENAMES.items()):
        d.execute('INSERT INTO research_conversations VALUES(?,?,?,?,?,?)',
                  (cid,refresh.WID,'my research' if i==1 else old,'["rank"]','created','updated'))
        d.execute('INSERT INTO research_messages VALUES(?,?,?)',(cid+'-message',cid,'{"answer":"original"}'))
    refresh.install_into(d)
    for i,(cid,(_,new)) in enumerate(refresh.RENAMES.items()):
        row=d.execute('SELECT title,created_at,updated_at FROM research_conversations WHERE id=?',(cid,)).fetchone()
        assert row==('my research' if i==1 else new,'created','updated')
        assert d.execute('SELECT payload FROM research_messages WHERE conversation_id=?',(cid,)).fetchone()[0]=='{"answer":"original"}'


def test_published_recordings_have_complete_scoped_citations():
    import re
    items=refresh.recordings()
    assert {item['title']:len(item['messages']) for item in items}=={'方法解读':1,'实验分析':3,'观点比较':3,'论文关联':3}
    for item in items:
        for message in item['messages']:
            assert message['status']=='completed' and message['response']['answer']
            assert {p['paper_id'] for p in message['papers']}==set(item['selected'])
            response=message['response']
            evidence={e['evidence_id']:e['paper_id'] for e in response['evidence']}
            citations=set(re.findall(r'\[(E\d+)\]',response['answer']))
            assert citations and citations<=set(evidence)
            assert set(evidence.values())<=set(item['selected'])
            if item['title']=='论文关联':
                assert len(item['selected'])==6
                assert {evidence[c] for c in citations}==set(item['selected'])

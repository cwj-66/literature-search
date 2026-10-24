import json,sqlite3
from app.infrastructure import demo_readable as update

def test_exact_demo_response_update_preserves_scope_followups_and_user_edits(monkeypatch):
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE research_conversations(id TEXT PRIMARY KEY,workspace_id TEXT,title TEXT,updated_at TEXT);
                       CREATE TABLE research_messages(id TEXT,conversation_id TEXT,payload TEXT);''')
    original={'answer':'Old audited answer','evidence':[{'paper_id':'p1'}]}
    new={**original,'answer':'Readable answer','presentation':{'composed':True}}
    recordings=[]
    for cid in ['demo','edited','deleted']:
        db.execute('INSERT INTO research_conversations VALUES(?,?,?,?)',(cid,update.WID,'visitor title','historic'))
        m={'id':'m','question':'method','papers':[{'id':'local','paper_id':'p1'}],'status':'completed','response':dict(original)}
        if cid=='edited':m['response']['answer']='visitor content'
        if cid!='deleted':db.execute('INSERT INTO research_messages VALUES(?,?,?)',('m',cid,json.dumps(m)))
        recordings.append({'conversation_id':cid,'message_id':'m','question':'method','paper_ids':['p1'],
                           'expected_response_hash':update.digest(original),'response':new})
    db.execute('INSERT INTO research_messages VALUES(?,?,?)',('followup','demo','{"question":"my own question"}'))
    monkeypatch.setattr(update,'recordings',lambda:recordings)
    update.install_into(db)
    data=json.loads(db.execute("SELECT payload FROM research_messages WHERE conversation_id='demo' AND id='m'").fetchone()[0])
    assert data['response']==new and data['papers'][0]['id']=='local'
    assert 'visitor content' in db.execute("SELECT payload FROM research_messages WHERE conversation_id='edited'").fetchone()[0]
    assert db.execute("SELECT payload FROM research_messages WHERE id='followup'").fetchone()[0]=='{"question":"my own question"}'
    assert db.execute("SELECT COUNT(*) FROM research_messages WHERE conversation_id='deleted'").fetchone()[0]==0
    assert all(r==('visitor title','historic') for r in db.execute('SELECT title,updated_at FROM research_conversations'))
    db.execute("DELETE FROM research_messages WHERE conversation_id='demo'")
    update.install_into(db)
    assert db.execute("SELECT COUNT(*) FROM research_messages WHERE conversation_id='demo'").fetchone()[0]==0

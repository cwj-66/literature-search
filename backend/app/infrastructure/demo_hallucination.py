"""Publish real reviewed hallucination research runs, preserving visitor edits."""
import copy
import json
from pathlib import Path

VERSION = 'hallucination-research-20261024-v1'
WID = 'pmvci88eig1qi'


def recordings():
    path = Path(__file__).parents[1]/'assets/research-hallucination.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def install_into(db):
    items = recordings()
    if not items:
        return False
    db.execute('CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)')
    if db.execute('SELECT 1 FROM demo_sample_updates WHERE version=?',(VERSION,)).fetchone():
        return True
    from app.infrastructure.demo_samples import HALLUCINATION
    members = {json.loads(raw)['id']: lid for lid, raw in db.execute(
        'SELECT p.local_id,p.data FROM papers p JOIN workspace_papers w ON w.local_id=p.local_id WHERE w.workspace_id=?',(WID,))}
    for index,(start,count,label) in enumerate([(0,1,'阅读起点'),(1,3,'方法解读'),(4,5,'证据边界')],1):
        cid=f'demo-reviewed-{index}-{WID}'
        row=db.execute('SELECT title,paper_ids FROM research_conversations WHERE id=? AND workspace_id=?',(cid,WID)).fetchone()
        if not row or row[0]!=f'示例 · {label} · {count} 轮':
            continue
        expected=HALLUCINATION[start:start+count]
        if any(ref not in members for ref,_,_ in expected):
            continue
        if set(json.loads(row[1]))!={members[ref] for ref,_,_ in expected}:
            continue
        messages=[json.loads(r[0]) for r in db.execute('SELECT payload FROM research_messages WHERE conversation_id=? ORDER BY rowid',(cid,))]
        if len(messages)!=count or not all(m.get('demo_example') and m.get('question')==q
                and (m.get('response') or {}).get('answer')==a+' [E0001]'
                and {p['paper_id'] for p in m.get('papers',[])}=={ref}
                for m,(ref,q,a) in zip(messages,expected)):
            continue
        db.execute('DELETE FROM research_messages WHERE conversation_id=?',(cid,))
        db.execute('DELETE FROM research_conversations WHERE id=?',(cid,))
    for item in items:
        if db.execute('SELECT 1 FROM research_conversations WHERE id=?',(item['id'],)).fetchone():
            continue
        if not all(ref in members for ref in item['selected']):
            continue
        messages=copy.deepcopy(item['messages'])
        assert messages and all(m['status']=='completed' and m.get('response') for m in messages)
        for m in messages:
            assert {p['paper_id'] for p in m['papers']}==set(item['selected'])
            for p in m['papers']:
                p['id']=members[p['paper_id']]
            m['recorded_demo']={'kind':'recorded_live_model_run','model':item['model'],'publication':VERSION}
        db.execute('INSERT INTO research_conversations VALUES(?,?,?,?,?,?)',
            (item['id'],WID,item['title'],json.dumps([members[r] for r in item['selected']]),messages[0]['created_at'],messages[-1]['created_at']))
        for m in messages:
            db.execute('INSERT INTO research_messages VALUES(?,?,?)',(m['id'],item['id'],json.dumps(m,ensure_ascii=False)))
    db.execute('INSERT INTO demo_sample_updates VALUES(?)',(VERSION,))
    return True


def install():
    from app.infrastructure.db import connection
    with connection() as db,db:
        db.execute('BEGIN IMMEDIATE')
        return install_into(db)

"""Replace untouched abstract templates with reviewed real multi-turn runs once."""
import copy
import json
from pathlib import Path

VERSION='research-dialogs-20261024-v2'
WID='pmvcf313hr4xt'
RENAMES={
    'demo-research-20261023-simple':('实测 · 研究目标与方法起点','研究目标'),
    'demo-research-20261023-exploration':('实测 · 方法结合与实验设想','方法结合'),
    'demo-research-20261023-critique':('实测 · 实验结论与泛化边界','实验边界'),
}


def recordings():
    path=Path(__file__).parents[1]/'assets/research-dialogs.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def install_into(db):
    items=recordings()
    if not items:return False
    db.execute('CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)')
    if db.execute('SELECT 1 FROM demo_sample_updates WHERE version=?',(VERSION,)).fetchone():return True
    for cid,(old,new) in RENAMES.items():
        db.execute('UPDATE research_conversations SET title=? WHERE id=? AND workspace_id=? AND title=?',(new,cid,WID,old))
    # Remove only the exact, unmodified old templates. Appended user messages,
    # renamed conversations and other workspaces are never removed.
    from app.infrastructure.demo_samples import RAG
    for index,(start,count,label) in enumerate([(0,1,'阅读起点'),(1,3,'方法解读'),(4,5,'证据边界')],1):
        cid=f'demo-reviewed-{index}-{WID}'
        row=db.execute('SELECT title FROM research_conversations WHERE id=? AND workspace_id=?',(cid,WID)).fetchone()
        if not row or row[0]!=f'示例 · {label} · {count} 轮':continue
        messages=[json.loads(r[0]) for r in db.execute('SELECT payload FROM research_messages WHERE conversation_id=? ORDER BY rowid',(cid,))]
        expected=[q for _,q,_ in RAG[start:start+count]]
        if len(messages)!=count or not all(m.get('demo_example') and m['question']==q for m,q in zip(messages,expected)):continue
        db.execute('DELETE FROM research_messages WHERE conversation_id=?',(cid,))
        db.execute('DELETE FROM research_conversations WHERE id=?',(cid,))
    members={json.loads(raw)['id']:lid for lid,raw in db.execute('SELECT p.local_id,p.data FROM papers p JOIN workspace_papers wp ON wp.local_id=p.local_id WHERE wp.workspace_id=?',(WID,))}
    for item in items:
        cid=item['id']
        if db.execute('SELECT 1 FROM research_conversations WHERE id=?',(cid,)).fetchone():continue
        if not all(ref in members for ref in item['selected']):continue
        messages=copy.deepcopy(item['messages'])
        assert messages and all(m['status']=='completed' and m.get('response') for m in messages)
        for message in messages:
            for paper in message['papers']:paper['id']=members[paper['paper_id']]
            message['recorded_demo']={'kind':'recorded_live_model_run','model':item['model'],'publication':VERSION}
        db.execute('INSERT INTO research_conversations VALUES(?,?,?,?,?,?)',(cid,WID,item['title'],json.dumps([members[r] for r in item['selected']]),messages[0]['created_at'],messages[-1]['created_at']))
        for message in messages:db.execute('INSERT INTO research_messages VALUES(?,?,?)',(message['id'],cid,json.dumps(message,ensure_ascii=False)))
    db.execute('INSERT INTO demo_sample_updates VALUES(?)',(VERSION,))
    return True


def install():
    from app.infrastructure.db import connection
    with connection() as db,db:
        db.execute('BEGIN IMMEDIATE')
        return install_into(db)

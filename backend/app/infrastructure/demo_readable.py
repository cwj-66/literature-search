"""Refresh only exact unchanged public demo responses; never touch user turns."""
import hashlib
import json
from pathlib import Path

VERSION='readable-research-20261024-v1'
WID='pmvcf313hr4xt'

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def recordings():
    path=Path(__file__).parents[1]/'assets/research-readable.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

def install_into(db):
    items=recordings()
    if not items:return False
    db.execute('CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)')
    if db.execute('SELECT 1 FROM demo_sample_updates WHERE version=?',(VERSION,)).fetchone():return True
    for item in items:
        row=db.execute('''SELECT m.payload FROM research_messages m JOIN research_conversations c ON c.id=m.conversation_id
            WHERE c.workspace_id=? AND c.id=? AND m.id=?''',(WID,item['conversation_id'],item['message_id'])).fetchone()
        if not row:continue
        message=json.loads(row[0])
        if message.get('status')!='completed' or message.get('question')!=item['question']:continue
        if {p['paper_id'] for p in message['papers']}!=set(item['paper_ids']):continue
        if digest(message.get('response'))!=item['expected_response_hash']:continue
        response=item['response']
        presentation=response.get('presentation',{})
        if presentation.get('mode')=='regenerated':
            assert response['evidence'] and all(e['paper_id'] in item['paper_ids'] for e in response['evidence'])
            assert any(r['verdict']=='supported' for r in response['reviews'])
            import re
            assert set(re.findall(r'E\d{4,}',response['answer']))<={e['evidence_id'] for e in response['evidence']}
        else:
            assert presentation.get('composed') and response['evidence']==message['response']['evidence']
        message['response']=response
        message.setdefault('recorded_demo',{})['presentation']=VERSION
        if presentation.get('mode')=='regenerated':
            metadata=message['recorded_demo']
            metadata['original_model']=metadata.get('model')
            metadata['model']=presentation.get('model')
        db.execute('UPDATE research_messages SET payload=? WHERE conversation_id=? AND id=?',
                   (json.dumps(message,ensure_ascii=False),item['conversation_id'],item['message_id']))
    # Do not bump conversation timestamps: these remain historical demo turns.
    db.execute('INSERT INTO demo_sample_updates VALUES(?)',(VERSION,))
    return True

def install():
    from app.infrastructure.db import connection
    with connection() as db,db:
        db.execute('BEGIN IMMEDIATE')
        return install_into(db)

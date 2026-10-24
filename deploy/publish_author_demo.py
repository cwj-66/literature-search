"""Publish only the author's two public demo workspaces, never their whole DB.

Run inside the API container. Backups stay on the server. Existing visitor edits,
deleted workspaces and conversation tombstones are preserved.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sqlite3

VERSION = 'author-defaults-20261024-v1'
AUTHOR = os.getenv('PAPER_DEMO_AUTHOR', '')
WORKSPACES = ('pmvcf313hr4xt', 'pmvci88eig1qi')
EXPECTED = {
    WORKSPACES[0]: {'arxiv:2404.08189','doi:10.1145/3797161.3797180','arxiv:2407.02485','arxiv:2506.08938','arxiv:2401.15391','arxiv:2511.22858'},
    WORKSPACES[1]: {'arxiv:2601.03052','arxiv:2401.00396','arxiv:2503.21676','arxiv:2409.11353'},
}


def members(db, wid):
    return [dict(r) for r in db.execute('SELECT p.*,wp.added_at FROM papers p JOIN workspace_papers wp ON wp.local_id=p.local_id WHERE wp.workspace_id=? ORDER BY wp.added_at,p.local_id',(wid,))]


def refs(rows):
    return {json.loads(r['data'])['id'] for r in rows}


def unchanged_workspace(current, previous):
    return refs(current) == refs(previous)


def unchanged_chat(current, previous):
    if current.get('archived') or current.get('pinned'):
        return False
    if previous and all(current.get(k) == previous.get(k) for k in ('title','turns','selectedIds')):
        return True
    turns=current.get('turns',[])
    # The old reviewed templates are explicitly marked per turn. Never replace
    # renamed templates, or a conversation that includes a new user question.
    return (current.get('title','').startswith('示例 · ') and bool(turns)
            and not current.get('selectedIds') and all(t.get('demo_example') for t in turns))


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def insert(db, table, row):
    data=dict(row)
    columns=list(data)
    db.execute('INSERT OR IGNORE INTO "'+table+'" ('+','.join('"'+c+'"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(data.values()))


def copy_paper(source, target, row):
    lid=row['local_id']; ref=json.loads(row['data'])['id']
    existing=target.execute("SELECT local_id FROM papers WHERE json_extract(data,'$.id')=?",(ref,)).fetchone()
    if existing and existing[0]!=lid:
        raise ValueError('Canonical paper has a different local ID; refusing unsafe merge: '+ref)
    insert(target,'papers',{k:row[k] for k in ('local_id','data','imported_at')})
    common=tables(source)&tables(target)
    # Explicit allowlist: no config, operation history, credentials or pending jobs.
    for name in ['paper_keys','paper_documents','paper_fields','relation_snapshots','citation_queries']:
        if name not in common:continue
        if target.execute('SELECT 1 FROM '+name+' WHERE local_id=? LIMIT 1',(lid,)).fetchone():continue
        for value in source.execute('SELECT * FROM '+name+' WHERE local_id=?',(lid,)):
            insert(target,name,value)
    if 'analysis_results' in common:
        result=source.execute('SELECT * FROM analysis_results WHERE local_id=? ORDER BY generated_at DESC LIMIT 1',(lid,)).fetchone()
        if result:
            task=source.execute("SELECT * FROM analysis_tasks WHERE task_id=? AND status='success'",(result['task_id'],)).fetchone()
            if task:insert(target,'analysis_tasks',task);insert(target,'analysis_results',result)
    if 'paper_chunks' in common and not target.execute("SELECT 1 FROM paper_chunks WHERE local_id=? AND basis='full_text'",(lid,)).fetchone():
        source_doc=source.execute('SELECT text,structure FROM paper_documents WHERE local_id=?',(lid,)).fetchone()
        target_doc=target.execute('SELECT text,structure FROM paper_documents WHERE local_id=?',(lid,)).fetchone()
        if source_doc and target_doc and tuple(source_doc)==tuple(target_doc):
            job=source.execute("SELECT * FROM qa_index_jobs WHERE local_id=? AND status='ready'",(lid,)).fetchone()
            if job:
                target.execute('DELETE FROM paper_fts WHERE paper_id=?',(ref,))
                target.execute('DELETE FROM paper_chunks WHERE local_id=?',(lid,))
                target.execute('DELETE FROM qa_index_jobs WHERE local_id=?',(lid,))
                for name in ['qa_parent_chunks','paper_chunks']:
                    for value in source.execute('SELECT * FROM '+name+' WHERE local_id=?',(lid,)):
                        insert(target,name,value)
                for chunk in target.execute('SELECT chunk_id,text FROM paper_chunks WHERE local_id=?',(lid,)).fetchall():
                    target.execute('INSERT INTO paper_fts(chunk_id,paper_id,body) VALUES(?,?,?)',(chunk[0],ref,chunk[1]))
                insert(target,'qa_index_jobs',job)


def publish(base, apply=False):
    if not re.fullmatch(r'[0-9a-f]{32}', AUTHOR):
        raise ValueError('Set PAPER_DEMO_AUTHOR to the audited author visitor ID before publication')
    seed=base/'demo-seed/papers.sqlite3'
    author=base/'demo-visitors'/AUTHOR/'papers.sqlite3'
    with closing(sqlite3.connect(author.resolve().as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(seed.resolve().as_uri()+'?mode=ro',uri=True)) as old:
        source.row_factory=old.row_factory=sqlite3.Row
        source.execute('BEGIN');old.execute('BEGIN')
        previous={wid:members(old,wid) for wid in WORKSPACES}
        desired={wid:members(source,wid) for wid in WORKSPACES}
        for wid in WORKSPACES:
            assert refs(desired[wid])==EXPECTED[wid], 'Author contents changed; audit before publication'
        old_names={r['id']:r['name'] for r in old.execute('SELECT * FROM workspaces')}
        old_chats={(r['workspace_id'],r['id']):json.loads(r['payload']) for r in old.execute('SELECT * FROM assistant_chats')}
        old.rollback()  # Release the seed read lock before publishing its update.
        recordings={}
        for wid in WORKSPACES:
            for index,count in [(1,1),(2,3),(3,5)]:
                cid='demo-reviewed-'+str(index)
                source_id=('demo-live-20261023-'+str(index)) if wid==WORKSPACES[0] else cid
                row=source.execute('SELECT payload FROM assistant_chats WHERE workspace_id=? AND id=? AND deleted=0',(wid,source_id)).fetchone()
                chat=json.loads(row[0]);assert len(chat['turns'])==count and all(t.get('task',{}).get('status')=='done' for t in chat['turns'])
                chat['id']=cid
                chat.pop('version',None)
                chat['recorded_demo']={'kind':'recorded_live_model_run','publication':VERSION}
                for turn in chat['turns']:turn['demo_example']=True
                recordings[wid,cid]=chat
        plans=[]
        paths=[seed]+[p for p in sorted((base/'demo-visitors').glob('*/papers.sqlite3')) if p.parent.name!=AUTHOR]
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup=base/'demo-default-backups'/stamp
        for path in paths:
            with closing(sqlite3.connect(path,timeout=30)) as target:
                target.row_factory=sqlite3.Row
                target.execute('PRAGMA foreign_keys=ON')
                done=target.execute("SELECT 1 FROM sqlite_master WHERE name='demo_sample_updates'").fetchone()
                if done and target.execute('SELECT 1 FROM demo_sample_updates WHERE version=?',(VERSION,)).fetchone():continue
                eligible=[wid for wid in WORKSPACES if target.execute('SELECT 1 FROM workspaces WHERE id=?',(wid,)).fetchone() and (path==seed or unchanged_workspace(members(target,wid),previous[wid]))]
                plans.append({'kind':'seed' if path==seed else 'visitor','upgrade_workspaces':eligible})
                if not apply:continue
                snapshot=backup/path.relative_to(base)
                snapshot.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with closing(sqlite3.connect(snapshot)) as copy:target.backup(copy)
                snapshot.chmod(0o600)
                with target:
                    target.execute('BEGIN IMMEDIATE')
                    for wid in eligible:
                        # Recheck under the write lock, preserving concurrent visitor edits.
                        if path!=seed and not unchanged_workspace(members(target,wid),previous[wid]):continue
                        for row in desired[wid]:copy_paper(source,target,row)
                        target.execute('DELETE FROM workspace_papers WHERE workspace_id=?',(wid,))
                        for row in desired[wid]:target.execute('INSERT INTO workspace_papers VALUES(?,?,?)',(wid,row['local_id'],row['added_at']))
                        name=source.execute('SELECT name FROM workspaces WHERE id=?',(wid,)).fetchone()[0]
                        target.execute('UPDATE workspaces SET name=? WHERE id=? AND name=?',(name,wid,old_names[wid]))
                    for (wid,cid),chat in recordings.items():
                        if wid not in eligible:continue
                        row=target.execute('SELECT payload,deleted FROM assistant_chats WHERE workspace_id=? AND id=?',(wid,cid)).fetchone()
                        if not row or row['deleted']:continue  # Preserve visitor deletions.
                        current=json.loads(row['payload'])
                        if path!=seed and not unchanged_chat(current,old_chats.get((wid,cid))):continue
                        target.execute('UPDATE assistant_chats SET payload=?,version=version+1 WHERE workspace_id=? AND id=?',(json.dumps(chat,ensure_ascii=False),wid,cid))
                    target.execute('CREATE TABLE IF NOT EXISTS demo_sample_updates(version TEXT PRIMARY KEY)')
                    target.execute('INSERT INTO demo_sample_updates VALUES(?)',(VERSION,))
                    assert target.execute('PRAGMA foreign_key_check').fetchone() is None
        print(json.dumps({'apply':apply,'default_paper_counts':{wid:len(rows) for wid,rows in desired.items()},'plans':plans,'backup':str(backup) if apply else None}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--base',type=Path,default=Path('/app/data'))
    args=parser.parse_args();publish(args.base,args.apply)

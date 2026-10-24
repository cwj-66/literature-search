import importlib.util
import json
from pathlib import Path
import sqlite3

spec=importlib.util.spec_from_file_location('publish_author_demo',Path(__file__).resolve().parents[2]/'deploy/publish_author_demo.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


def create(path, author=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE papers(local_id TEXT PRIMARY KEY,data TEXT,imported_at TEXT);
        CREATE TABLE workspaces(id TEXT PRIMARY KEY,name TEXT,created_at TEXT);
        CREATE TABLE workspace_papers(workspace_id TEXT,local_id TEXT,added_at TEXT);
        CREATE TABLE assistant_chats(workspace_id TEXT,id TEXT,payload TEXT,version INTEGER,deleted INTEGER,updated_at TEXT,PRIMARY KEY(workspace_id,id));''')
        for wid in mod.WORKSPACES:
            db.execute('INSERT INTO workspaces VALUES(?,?,?)',(wid,'author name' if author else 'old name','2026'))
            refs=sorted(mod.EXPECTED[wid]) if author else ['arxiv:2407.02485']
            for i,ref in enumerate(refs):
                db.execute('INSERT OR IGNORE INTO papers VALUES(?,?,?)',(ref,json.dumps({'id':ref,'title':ref}),'2026'))
                db.execute('INSERT INTO workspace_papers VALUES(?,?,?)',(wid,ref,str(i)))
            for i,count in [(1,1),(2,3),(3,5)]:
                cid=('demo-live-20261023-' if author and wid==mod.WORKSPACES[0] else 'demo-reviewed-')+str(i)
                chat={'id':cid,'title':'fresh '+str(i) if author else '示例 · old '+str(i),'selectedIds':[],
                      'turns':[{'demo_example':True,'task':{'status':'done'}} for _ in range(count)]}
                db.execute('INSERT INTO assistant_chats VALUES(?,?,?,1,0,?)',(wid,cid,json.dumps(chat),'2026'))


def test_publication_updates_defaults_preserves_edits_deletions_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, 'AUTHOR', 'c'*32)
    seed=tmp_path/'demo-seed/papers.sqlite3';author=tmp_path/'demo-visitors'/mod.AUTHOR/'papers.sqlite3'
    old=tmp_path/'demo-visitors'/('a'*32)/'papers.sqlite3';edited=tmp_path/'demo-visitors'/('b'*32)/'papers.sqlite3'
    create(seed);create(author,True);create(old);create(edited)
    with sqlite3.connect(edited) as db:
        db.execute('DELETE FROM workspace_papers WHERE workspace_id=?',(mod.WORKSPACES[0],))
        db.execute("UPDATE assistant_chats SET deleted=1 WHERE id='demo-reviewed-1'")
        row=db.execute("SELECT payload FROM assistant_chats WHERE workspace_id=? AND id='demo-reviewed-2'",(mod.WORKSPACES[1],)).fetchone()
        chat=json.loads(row[0]);chat['turns'].append({'question':'visitor question'})
        db.execute("UPDATE assistant_chats SET payload=? WHERE workspace_id=? AND id='demo-reviewed-2'",(json.dumps(chat),mod.WORKSPACES[1]))
    mod.publish(tmp_path,True)
    for path in [seed,old]:
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row
            for wid in mod.WORKSPACES:assert mod.refs(mod.members(db,wid))==mod.EXPECTED[wid]
            assert json.loads(db.execute('SELECT payload FROM assistant_chats LIMIT 1').fetchone()[0])['title'].startswith('fresh')
    with sqlite3.connect(edited) as db:
        assert db.execute('SELECT COUNT(*) FROM workspace_papers WHERE workspace_id=?',(mod.WORKSPACES[0],)).fetchone()[0]==0
        assert db.execute("SELECT deleted FROM assistant_chats WHERE id='demo-reviewed-1' LIMIT 1").fetchone()[0]==1
        raw=db.execute("SELECT payload FROM assistant_chats WHERE workspace_id=? AND id='demo-reviewed-2'",(mod.WORKSPACES[1],)).fetchone()[0]
        assert json.loads(raw)['turns'][-1]['question']=='visitor question'
    mod.publish(tmp_path,True)
    with sqlite3.connect(seed) as db:assert db.execute('SELECT COUNT(*) FROM demo_sample_updates').fetchone()[0]==1

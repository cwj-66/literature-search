"""Private paper assignments; public, cross-workspace field reports in one global DB."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
import unicodedata
from uuid import uuid4

from app.infrastructure.db import connection
from app.infrastructure.demo import base_path
from app.services.research_fields.prompts import VERSION

def now():
    return datetime.now(timezone.utc).isoformat()

def norm(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('-', ' ').split()).strip()

# Identity aliases, not a classifier: the LLM must first ground field membership.
ALIASES = {
    'rag': 'retrieval augmented generation', '检索增强生成': 'retrieval augmented generation',
    'knowledge graph': 'knowledge graphs', '知识图谱': 'knowledge graphs',
    'llm agents': 'large language model agents', 'large language model agent': 'large language model agents',
}

def identity(value):
    key = norm(value)
    return ALIASES.get(key, key)

@contextmanager
def global_db():
    path = base_path().parent / 'research-fields.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS research_fields (
            id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, name TEXT NOT NULL, parent TEXT,
            created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS field_aliases (alias TEXT PRIMARY KEY, field_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS field_reports (
            field_id TEXT NOT NULL, cache_key TEXT NOT NULL, status TEXT NOT NULL,
            report TEXT, error TEXT, stage TEXT NOT NULL DEFAULT '', request_id TEXT NOT NULL,
            requested_at TEXT NOT NULL, started_at REAL, computed_at TEXT,
            model TEXT NOT NULL, PRIMARY KEY(field_id,cache_key));''')
        yield db
    finally:
        db.close()

def register(field):
    key = identity(field.canonical_name)
    if len(key) < 2 or not field.name.strip():
        return None
    with global_db() as db, db:
        row = db.execute('SELECT field_id FROM field_aliases WHERE alias=?', (key,)).fetchone()
        fid = row[0] if row else 'rf_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        db.execute('INSERT OR IGNORE INTO research_fields VALUES(?,?,?,?,?)',
                   (fid, field.canonical_name.strip(), field.name.strip(), field.parent, now()))
        # Never merge different fields just because an acronym happens to collide.
        for alias in {key, norm(field.name), *[norm(a) for a in field.aliases if 2 <= len(a) <= 140]}:
            if alias:
                db.execute('INSERT OR IGNORE INTO field_aliases VALUES(?,?)', (alias, fid))
        return fid

def get_field(fid):
    with global_db() as db:
        row = db.execute('SELECT * FROM research_fields WHERE id=?', (fid,)).fetchone()
        return dict(row) if row else None

def save_assignments(db, local_id, fields):
    assignments = []
    for field in fields[:3]:
        fid = register(field)
        if fid and all(fid != f[0] for f in assignments):
            assignments.append((fid, field.model_dump()))
    db.execute('DELETE FROM paper_fields WHERE local_id=?', (local_id,))
    db.executemany('INSERT INTO paper_fields VALUES(?,?,?)',
                   [(local_id, fid, json.dumps(payload, ensure_ascii=False)) for fid, payload in assignments])
    db.execute('''INSERT INTO field_identifications VALUES(?, 'completed', NULL, ?)
        ON CONFLICT(local_id) DO UPDATE SET status='completed',error=NULL,updated_at=excluded.updated_at''', (local_id, now()))

def cache_key():
    return VERSION + ':' + str(datetime.now(timezone.utc).year)

def report(fid):
    with global_db() as db:
        row = db.execute('SELECT * FROM field_reports WHERE field_id=? AND cache_key=?', (fid, cache_key())).fetchone()
        # Older-year reports remain readable, with an explicit stale state.
        if not row:
            row = db.execute('SELECT * FROM field_reports WHERE field_id=? AND report IS NOT NULL ORDER BY computed_at DESC LIMIT 1', (fid,)).fetchone()
        if not row:
            return {'status': 'idle', 'report': None, 'stage': '', 'error': None, 'computed_at': None, 'stale': False}
        result = dict(row)
        result['report'] = json.loads(result['report']) if result['report'] else None
        result['stale'] = result['cache_key'] != cache_key()
        return result

def request_report(fid, model, refresh=False):
    if not get_field(fid):
        raise ValueError('研究领域不存在')
    with global_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        key = cache_key()
        row = db.execute('SELECT * FROM field_reports WHERE field_id=? AND cache_key=?', (fid, key)).fetchone()
        if row and (row['status'] in {'queued', 'running'} or row['report'] and not refresh):
            return False
        prior = row['report'] if row else None
        computed_at = row['computed_at'] if row else None
        if not prior:
            previous = db.execute('SELECT report,computed_at FROM field_reports WHERE field_id=? AND report IS NOT NULL ORDER BY computed_at DESC LIMIT 1', (fid,)).fetchone()
            if previous:
                prior, computed_at = previous['report'], previous['computed_at']
        db.execute('''INSERT INTO field_reports(field_id,cache_key,status,report,error,stage,request_id,requested_at,model)
            VALUES(?,?,'queued',?,NULL,'等待分析',?,?,?) ON CONFLICT(field_id,cache_key) DO UPDATE SET
            status='queued',error=NULL,stage='等待分析',request_id=excluded.request_id,
            requested_at=excluded.requested_at,started_at=NULL,model=excluded.model''',
            (fid, key, prior, uuid4().hex, now(), model))
        db.execute('UPDATE field_reports SET report=?,computed_at=? WHERE field_id=? AND cache_key=?', (prior, computed_at, fid, key))
        return True

def claim_report():
    with global_db() as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE field_reports SET status='queued',stage='恢复任务' WHERE status='running' AND started_at<?", (time.time()-1500,))
        row = db.execute("SELECT * FROM field_reports WHERE status='queued' ORDER BY requested_at LIMIT 1").fetchone()
        if not row:
            return None
        db.execute("UPDATE field_reports SET status='running',started_at=?,stage='规划检索' WHERE request_id=?", (time.time(), row['request_id']))
        return dict(row)

def stage(job, message):
    with global_db() as db, db:
        db.execute("UPDATE field_reports SET stage=? WHERE request_id=? AND status='running'", (message, job['request_id']))

def finish(job, payload=None, error=None, cancelled=False):
    with global_db() as db, db:
        if payload is not None:
            db.execute("UPDATE field_reports SET report=?,status=?,stage='分析完成',error=NULL,computed_at=? WHERE request_id=? AND status='running'",
                       (json.dumps(payload, ensure_ascii=False), 'partial' if payload.get('partial') else 'completed', now(), job['request_id']))
        else:
            db.execute("UPDATE field_reports SET status=?,error=?,stage=? WHERE request_id=? AND status='running'",
                       ('queued' if cancelled else 'failed', error, '等待恢复' if cancelled else '本次分析未完成', job['request_id']))

def workspace_fields(workspace_id):
    from app.services.papers import workspaces
    workspaces.require(workspace_id)
    with connection() as db:
        rows = db.execute('''SELECT pf.field_id, pf.local_id, pf.payload, p.data FROM paper_fields pf
            JOIN workspace_papers wp ON wp.local_id=pf.local_id JOIN papers p ON p.local_id=pf.local_id
            WHERE wp.workspace_id=? ORDER BY pf.field_id''', (workspace_id,)).fetchall()
        missing = db.execute('''SELECT p.local_id, fi.status FROM workspace_papers wp JOIN papers p ON p.local_id=wp.local_id
            LEFT JOIN field_identifications fi ON fi.local_id=p.local_id
            WHERE wp.workspace_id=? AND (fi.local_id IS NULL OR fi.status!='completed')
            AND NOT EXISTS (SELECT 1 FROM paper_fields pf WHERE pf.local_id=p.local_id)''', (workspace_id,)).fetchall()
        pending_analysis = db.execute("SELECT COUNT(DISTINCT a.local_id) FROM analysis_tasks a JOIN workspace_papers wp ON wp.local_id=a.local_id WHERE wp.workspace_id=? AND a.status IN ('pending','preparing','analyzing')", (workspace_id,)).fetchone()[0]
        analyzed = {r[0] for r in db.execute('SELECT DISTINCT local_id FROM analysis_results')}
    groups = {}
    for r in rows:
        field = get_field(r['field_id'])
        if not field:
            continue
        item = groups.setdefault(r['field_id'], {**field, 'papers': [], 'analysis': report(r['field_id'])})
        paper = json.loads(r['data'])
        item['papers'].append({'id':r['local_id'], 'title':paper['title'], 'year':paper.get('year')})
    return {'fields': sorted(groups.values(), key=lambda f: (-len(f['papers']), f['name'])),
            'unidentified_count':len(missing), 'pending_analysis_count':pending_analysis, 'legacy_count':sum(r['local_id'] in analyzed and r['status'] not in {'queued','running'} for r in missing),
            'failed_count':sum(r['status']=='failed' for r in missing),
            'identifying_count':sum(r['status'] in {'queued','running'} for r in missing)}

def require_membership(workspace_id, fid):
    from app.services.papers import workspaces
    workspaces.require(workspace_id)
    with connection() as db:
        found = db.execute('''SELECT 1 FROM paper_fields pf JOIN workspace_papers wp ON wp.local_id=pf.local_id
            WHERE wp.workspace_id=? AND pf.field_id=? LIMIT 1''', (workspace_id, fid)).fetchone()
    if not found:
        raise ValueError('该研究领域不属于当前工作区')

def queue_legacy(workspace_id, retry_failed=True):
    from app.services.papers import workspaces
    workspaces.require(workspace_id)
    with connection() as db, db:
        ids = db.execute('''SELECT DISTINCT a.local_id FROM analysis_results a JOIN workspace_papers wp ON wp.local_id=a.local_id
            LEFT JOIN field_identifications f ON f.local_id=a.local_id
            WHERE wp.workspace_id=? AND (f.local_id IS NULL OR (? AND f.status='failed'))
            AND NOT EXISTS (SELECT 1 FROM paper_fields pf WHERE pf.local_id=a.local_id)''', (workspace_id, int(retry_failed))).fetchall()
        for r in ids:
            db.execute('''INSERT INTO field_identifications VALUES(?,'queued',NULL,?) ON CONFLICT(local_id)
                DO UPDATE SET status='queued',error=NULL,updated_at=excluded.updated_at''', (r[0], now()))
        return len(ids)

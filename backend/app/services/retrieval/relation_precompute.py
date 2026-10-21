"""Durable, serial precomputation. Graph reads never call search, embeddings or an LLM."""

import asyncio
import copy
import json
import logging
import time
from datetime import datetime, timezone

from app.infrastructure.db import connection
from app.services.papers import library
from app.services.retrieval import recommend, relation_score

log = logging.getLogger(__name__)
VERSION = "relation-snapshot-v1:" + relation_score.ALGORITHM
MAX_ATTEMPTS = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact(payload: dict, previous: dict | None = None) -> dict:
    result = copy.deepcopy(payload)
    recs = result["recommendations"]
    prior = {g["key"]: g for g in (previous or {}).get("recommendations", {}).get("groups", [])}
    groups = []
    for group in recs.get("groups", []):
        group["items"] = group.get("items", [])[:1]
        # A failed refresh cannot erase a previously discovered edge in this dimension.
        if group.get("status") in {"partial", "unavailable"} and not group["items"] and prior.get(group["key"], {}).get("items"):
            group["items"] = copy.deepcopy(prior[group["key"]]["items"])
        groups.append(group)
    recs["groups"] = groups
    recs["items"] = list({i["id"]: i for g in groups for i in g["items"]}.values())
    result.pop("network", None)
    return result


def ensure_job(local_id: str, force: bool = False) -> None:
    with connection() as db, db:
        db.execute("INSERT OR IGNORE INTO relation_jobs (local_id, requested_at) VALUES (?, ?)", (local_id, now()))
        if force:
            # Repeated refresh while a job is running is coalesced into that same job.
            db.execute("""UPDATE relation_jobs SET status='queued', revision=revision+1, attempts=0,
                next_attempt=0, requested_at=?, error=NULL WHERE local_id=? AND status NOT IN ('queued','running')""", (now(), local_id))


def read(local_id: str) -> dict:
    with connection() as db:
        row = db.execute("SELECT payload, computed_at FROM relation_snapshots WHERE local_id=?", (local_id,)).fetchone()
        job = db.execute("SELECT status, attempts FROM relation_jobs WHERE local_id=?", (local_id,)).fetchone()
    payload = None
    if row:
        try:
            payload = json.loads(row["payload"])
        except (ValueError, TypeError):
            log.warning("Invalid persisted relation snapshot for %s", local_id)
    return {"payload": payload, "status": job["status"] if job else "queued",
            "computed_at": row["computed_at"] if row else None}


def recover() -> None:
    """Backfill imported papers, adopt existing cache, and recover interrupted jobs at startup."""
    with connection() as db, db:
        db.execute("UPDATE relation_jobs SET status='queued' WHERE status='running'")
        db.execute("""INSERT OR IGNORE INTO relation_jobs (local_id, requested_at)
            SELECT local_id, ? FROM papers""", (now(),))
        cached = db.execute("""SELECT r.source_local_id, r.payload, r.computed_at FROM paper_relations r
            JOIN papers p ON p.local_id=r.source_local_id
            LEFT JOIN relation_snapshots s ON s.local_id=r.source_local_id WHERE s.local_id IS NULL
            ORDER BY r.computed_at DESC""").fetchall()
        for row in cached:
            try:
                payload = json.loads(row["payload"])
                if not payload.get("recommendations", {}).get("groups"):
                    continue
                db.execute("INSERT OR IGNORE INTO relation_snapshots VALUES (?, ?, ?, ?)",
                           (row["source_local_id"], json.dumps(compact(payload), ensure_ascii=False), VERSION, row["computed_at"]))
            except (ValueError, TypeError, KeyError):
                continue
        # Fresh snapshots need no calls on restart; stale ones stay readable during refresh.
        fresh = db.execute("SELECT local_id, payload, version FROM relation_snapshots").fetchall()
        for row in fresh:
            try:
                payload = json.loads(row["payload"])
                complete = all(g.get("status") == "completed" for g in payload["recommendations"].get("groups", []))
                if row["version"] == VERSION and complete and recommend.cache_fresh(payload):
                    db.execute("UPDATE relation_jobs SET status='ready' WHERE local_id=? AND status='queued' AND attempts=0 AND revision=1", (row["local_id"],))
                elif row["version"] != VERSION or not recommend.cache_fresh(payload):
                    db.execute("UPDATE relation_jobs SET status='queued', attempts=0, next_attempt=0 WHERE local_id=? AND status='ready'", (row["local_id"],))
            except (ValueError, TypeError, KeyError):
                continue


def claim() -> dict | None:
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT * FROM relation_jobs WHERE status='queued' AND next_attempt<=?
            ORDER BY requested_at, local_id LIMIT 1""", (time.time(),)).fetchone()
        if row is None:
            return None
        db.execute("UPDATE relation_jobs SET status='running', attempts=attempts+1 WHERE local_id=?", (row["local_id"],))
        return {**dict(row), "attempts": row["attempts"] + 1}


def finish(job: dict, payload: dict | None, error: str | None = None) -> None:
    incomplete = payload is None or any(g.get("status") in {"unavailable", "partial"}
                                        for g in payload["recommendations"].get("groups", []))
    retry = incomplete and job["attempts"] < MAX_ATTEMPTS
    with connection() as db, db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT revision FROM relation_jobs WHERE local_id=?", (job["local_id"],)).fetchone()
        if row is None or row["revision"] != job["revision"]:
            return
        if payload is not None:
            old = db.execute("SELECT payload FROM relation_snapshots WHERE local_id=?", (job["local_id"],)).fetchone()
            previous = json.loads(old["payload"]) if old else None
            saved = compact(payload, previous)
            stamp = saved["recommendations"].get("computed_at") or now()
            db.execute("INSERT OR REPLACE INTO relation_snapshots VALUES (?, ?, ?, ?)",
                       (job["local_id"], json.dumps(saved, ensure_ascii=False), VERSION, stamp))
        status = "queued" if retry else "partial" if incomplete and payload is not None else "failed" if incomplete else "ready"
        db.execute("UPDATE relation_jobs SET status=?, next_attempt=?, error=? WHERE local_id=?",
                   (status, time.time() + 60 * (2 ** (job["attempts"] - 1)) if retry else 0, error, job["local_id"]))


async def run_one() -> bool:
    job = await asyncio.to_thread(claim)
    if job is None:
        return False
    try:
        seed = await asyncio.to_thread(library.get_paper, job["local_id"])
        if seed is None:
            return True
        payload = await asyncio.wait_for(recommend.bundle(seed.local_id, True, seed=seed), timeout=240)
        await asyncio.to_thread(finish, job, payload)
    except asyncio.CancelledError:
        # recover() requeues this durable running record after a restart.
        raise
    except Exception as exc:
        log.exception("Relation precomputation failed for %s", job["local_id"])
        await asyncio.to_thread(finish, job, None, type(exc).__name__)
    return True


async def worker() -> None:
    while True:
        try:
            worked = await run_one()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Relation queue iteration failed")
            worked = False
        if not worked:
            await asyncio.sleep(1)

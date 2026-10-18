"""SQLite connection for papers, analysis, embeddings, and relation cache."""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connection():
    path = Path(os.getenv("PAPER_DB_PATH", str(Path(__file__).resolve().parents[2] / "data" / "papers.sqlite3")))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                local_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_keys (
                key TEXT PRIMARY KEY,
                local_id TEXT NOT NULL REFERENCES papers(local_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_papers (
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                local_id TEXT NOT NULL REFERENCES papers(local_id) ON DELETE CASCADE,
                added_at TEXT NOT NULL,
                PRIMARY KEY (workspace_id, local_id)
            );
            CREATE TABLE IF NOT EXISTS analysis_tasks (
                task_id TEXT PRIMARY KEY,
                paper_ref TEXT NOT NULL,
                local_id TEXT REFERENCES papers(local_id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                error TEXT,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                reanalyze INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS analysis_results (
                result_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE REFERENCES analysis_tasks(task_id) ON DELETE CASCADE,
                local_id TEXT NOT NULL REFERENCES papers(local_id) ON DELETE CASCADE,
                payload TEXT NOT NULL,
                analysis_basis TEXT NOT NULL,
                degradation_reason TEXT,
                source_kind TEXT NOT NULL,
                source_url TEXT,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                evidence TEXT NOT NULL,
                generated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_analysis_tasks_ref ON analysis_tasks(paper_ref, status);
            CREATE INDEX IF NOT EXISTS idx_analysis_tasks_local ON analysis_tasks(local_id, status);
            CREATE INDEX IF NOT EXISTS idx_analysis_results_local ON analysis_results(local_id, generated_at);
            CREATE TABLE IF NOT EXISTS paper_embeddings (
                cache_key TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                vector TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_relations (
                source_local_id TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (source_local_id, cache_key)
            );
        """)
        yield db
    finally:
        db.close()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.infrastructure.db import connection
from app.main import app
from app.schemas import PaperOut
from app.services.knowledge_graph import knowledge_graph
from app.services.papers import library


class KnowledgeGraphTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "graph.sqlite3"), "ANALYSIS_WORKER": "0"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def paper(self, i=1, year=2023, **kwargs):
        ref = f"arxiv:2301.{i:05d}"
        return library.save_paper(PaperOut(id=ref, title=f"Paper {i}", year=year,
            authors=["Alice", "Bob"], venue="Test Conference", abstract="Real saved abstract", **kwargs), ref)[0]

    def analyze(self, paper, payload, stamp="2026-10-17T00:00:00Z", result="r1"):
        with connection() as db, db:
            db.execute("INSERT INTO analysis_tasks (task_id,paper_ref,local_id,status,model_name,prompt_version,created_at) VALUES (?,?,?,'success','test-model','v1',?)", (result, paper.id, paper.local_id, stamp))
            db.execute("INSERT INTO analysis_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (result, result, paper.local_id, json.dumps(payload), "abstract", "Full text unavailable", "abstract", paper.url, "test-model", "v1", "{}", stamp))

    def test_metadata_and_analysis_are_traceable_and_keywords_normalized(self):
        p = self.paper()
        self.analyze(p, {"keywords": [" RAG ", "rag", "ＲＡＧ", "论文未明确说明", 123, ""], "technical_method": "Retrieval", "innovations": ["A grounded contribution"]})
        graph = knowledge_graph.build(references=[p.id])
        paper = next(n for n in graph["nodes"] if n["kind"] == "paper")
        self.assertEqual(paper["abstract"], "Real saved abstract")
        self.assertEqual(paper["analysis"]["technical_method"], "Retrieval")
        self.assertEqual(paper["analysis"]["result_id"], "r1")
        self.assertEqual(paper["analysis"]["analysis_basis"], "abstract")
        self.assertEqual(len([n for n in graph["nodes"] if n["kind"] == "keyword"]), 1)
        keyword = next(e for e in graph["edges"] if e["relation"] == "has_keyword")
        self.assertEqual(keyword["provenance"], "model")
        self.assertEqual(keyword["result_id"], "r1")
        self.assertFalse(any(e["relation"] == "cites" for e in graph["edges"]))

    def test_old_workspace_paper_is_not_lost_after_global_recent_limit_and_aliases_dedupe(self):
        old = self.paper(1, doi="10.1234/old")
        for i in range(2, 85):
            self.paper(i)
        graph = knowledge_graph.build(references=[old.local_id, old.id, "doi:10.1234/old", "missing"])
        self.assertEqual(graph["total_papers"], 1)
        self.assertEqual(graph["missing_references"], ["missing"])
        self.assertEqual(next(n for n in graph["nodes"] if n["kind"] == "paper")["local_id"], old.local_id)
        self.assertIn("doi:10.1234/old", next(n for n in graph["nodes"] if n["kind"] == "paper")["aliases"])
        self.assertEqual(knowledge_graph.build()["total_papers"], 84)

    def test_only_cached_real_citations_with_both_endpoints_in_scope_are_used(self):
        a, b, c = self.paper(1, citation_count=999), self.paper(2), self.paper(3)
        edges = [{"source": a.id, "target": b.id, "type": "cites"},
                 {"source": b.id, "target": a.id, "type": "similar"},
                 {"source": a.id, "target": c.id, "type": "cites"}]
        with connection() as db, db:
            db.execute("INSERT INTO paper_relations VALUES (?,?,?,?)", (a.local_id, "test", json.dumps({"network": {"edges": edges}}), "2026-10-17"))
        graph = knowledge_graph.build(references=[a.id, b.id])
        cites = [e for e in graph["edges"] if e["relation"] == "cites"]
        self.assertEqual(len(cites), 1)
        self.assertEqual((cites[0]["source"], cites[0]["target"]), ("paper:" + a.local_id, "paper:" + b.local_id))
        self.assertEqual(cites[0]["provenance"], "citation_cache")
        self.assertFalse(any(e["relation"] == "similar" for e in graph["edges"]))

    def test_latest_analysis_unknown_year_and_malformed_payload(self):
        a, b = self.paper(1, year=None), self.paper(2)
        self.analyze(a, {"keywords": ["Old"]})
        self.analyze(a, {"keywords": ["New"]}, "2026-10-18", "r2")
        self.analyze(b, ["invalid payload"], result="r3")
        graph = knowledge_graph.build(references=[a.id, b.id])
        self.assertEqual([n["label"] for n in graph["nodes"] if n["kind"] == "keyword"], ["New"])
        self.assertIsNone(next(n for n in graph["nodes"] if n["id"] == "paper:" + a.local_id)["year"])
        self.assertEqual(knowledge_graph.build(references=[])["nodes"], [])

    async def test_endpoint_validation_and_scoping(self):
        p = self.paper()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/api/knowledge-graph", json=[p.id])
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["total_papers"], 1)
            self.assertEqual((await client.post("/api/knowledge-graph", json=[])).json()["nodes"], [])
            for value in ([""], ["x" * 301], ["x"] * 2001, [None]):
                self.assertEqual((await client.post("/api/knowledge-graph", json=value)).status_code, 422)
            self.assertEqual((await client.get("/api/knowledge-graph?limit=2001")).status_code, 422)
            self.assertEqual((await client.get("/api/knowledge-graph?limit=2000")).status_code, 200)
        self.assertEqual(library.list_papers(1, 20)["total"], 1)


if __name__ == "__main__":
    unittest.main()

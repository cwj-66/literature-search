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
        values = dict(id=ref, title=f"Paper {i}", year=year, authors=["Alice", "Bob"], venue="Test Conference", abstract="Real saved abstract")
        values.update(kwargs)
        return library.save_paper(PaperOut(**values), ref)[0]

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
        similars = [e for e in graph["edges"] if e["relation"] == "similar"]
        self.assertEqual(len(cites), 1)
        self.assertEqual((cites[0]["source"], cites[0]["target"]), ("paper:" + a.local_id, "paper:" + b.local_id))
        self.assertEqual(cites[0]["provenance"], "citation_cache")
        self.assertEqual(len(similars), 1)
        self.assertEqual(set((similars[0]["source"], similars[0]["target"])), {"paper:" + a.local_id, "paper:" + b.local_id})
        self.assertNotEqual(similars[0]["relation"], "cites")
        self.assertFalse(any(node["kind"] == "paper" and node["paper_id"] == c.id for node in graph["nodes"]))

    async def test_opening_graph_does_not_compute_recommendations(self):
        paper = self.paper()
        with patch("app.services.retrieval.recommend.bundle", side_effect=AssertionError("must read cache only")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/knowledge-graph", json=[paper.id])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_papers"], 1)

    def test_latest_analysis_unknown_year_and_malformed_payload(self):
        a, b = self.paper(1, year=None), self.paper(2, title="Note", abstract="")
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


    def test_aliases_share_one_topic_and_analysis_adds_keywords(self):
        first, second = self.paper(1, authors=["Ada"], author_ids=["author-ada"]), self.paper(2, authors=["Ada"], author_ids=["author-other"])
        plain = self.paper(3, authors=["Sam"], venue="Workshop", title="Note", abstract="")
        self.analyze(first, {"keywords": ["RAG"]}, result="rag")
        self.analyze(second, {"keywords": ["Retrieval-Augmented Generation", "Hallucinations"]}, result="alias")
        before = knowledge_graph.build(references=[plain.id])
        self.assertFalse(any(edge["relation"] == "has_keyword" for edge in before["edges"]))
        self.assertTrue(any(edge["relation"] == "authored_by" for edge in before["edges"]))
        self.analyze(plain, {"keywords": ["hallucination"]}, result="hall")
        graph = knowledge_graph.build(references=[first.id, second.id, plain.id])
        keywords = [node for node in graph["nodes"] if node["kind"] == "keyword"]
        labels = {node["label"] for node in keywords}
        self.assertEqual(labels, {"RAG", "Hallucination"})
        rag = next(node["id"] for node in keywords if node["label"] == "RAG")
        self.assertEqual(sum(edge["relation"] == "has_keyword" and edge["target"] == rag for edge in graph["edges"]), 2)
        authors = [node["id"] for node in graph["nodes"] if node["kind"] == "author" and node["label"] == "Ada"]
        self.assertEqual(len(authors), 2)
        self.assertEqual(len({edge["target"] for edge in graph["edges"] if edge["relation"] == "authored_by" and edge["source"] in {f"paper:{first.local_id}", f"paper:{second.local_id}"}}), 2)
        same = self.paper(4, authors=["A. Ada"], author_ids=["author-ada"])
        merged = knowledge_graph.build(references=[first.id, same.id])
        ada_targets = {edge["target"] for edge in merged["edges"] if edge["relation"] == "authored_by"}
        self.assertEqual(len(ada_targets), 1)
        self.assertEqual(knowledge_graph.build(references=[first.id])["total_papers"], 1)

    def test_failed_citation_lookup_is_not_reported_as_no_citations(self):
        paper = self.paper()
        failed = {"recommendations": {"warnings": ["引用信息未知／暂不可用"]}, "network": {"edges": []}}
        with connection() as db, db:
            db.execute("INSERT INTO paper_relations VALUES (?,?,?,?)", (paper.local_id, "failed", json.dumps(failed), "2026-10-17T00:00:00+00:00"))
        graph = knowledge_graph.build(references=[paper.id])
        node = next(item for item in graph["nodes"] if item["kind"] == "paper")
        self.assertEqual(node["citation_status"], "failed")
        self.assertIn("暂不可用", node["citation_note"])
        self.assertNotEqual(node["citation_status"], "none_in_scope")
        other = self.paper(2)
        with connection() as db, db:
            db.execute("INSERT INTO paper_relations VALUES (?,?,?,?)", (other.local_id, "empty", json.dumps({"network": {"edges": []}}), "2026-10-17T00:00:00+00:00"))
        checked = knowledge_graph.build(references=[other.id])
        checked_node = next(item for item in checked["nodes"] if item["kind"] == "paper")
        self.assertEqual(checked_node["citation_status"], "none_in_scope")
        self.assertIn("不表示", checked_node["citation_note"])

    def test_unanalyzed_rag_title_shares_keyword_with_analyzed_paper(self):
        analyzed = self.paper(1, title="Corrective Retrieval Augmented Generation")
        self.analyze(analyzed, {"keywords": ["RAG"]}, result="rag")
        other = self.paper(2, title="MultiHop-RAG: Benchmarking Retrieval-Augmented Generation for Multi-hop Queries")
        graph = knowledge_graph.build(references=[analyzed.id, other.id])
        rag = next(node["id"] for node in graph["nodes"] if node["kind"] == "keyword" and node["label"] == "RAG")
        sources = {edge["source"] for edge in graph["edges"] if edge["relation"] == "has_keyword" and edge["target"] == rag}
        self.assertEqual(sources, {f"paper:{analyzed.local_id}", f"paper:{other.local_id}"})
        fallback = next(edge for edge in graph["edges"] if edge["source"] == f"paper:{other.local_id}" and edge["relation"] == "has_keyword")
        self.assertEqual(fallback["provenance"], "metadata")
        self.assertEqual(library.list_papers(1, 20)["total"], 2)

    def test_same_name_authors_without_ids_merge(self):
        left = self.paper(1, authors=["Alice Chen"])
        right = self.paper(2, authors=["Alice Chen"])
        graph = knowledge_graph.build(references=[left.id, right.id])
        authors = [node for node in graph["nodes"] if node["kind"] == "author"]
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0]["provenance"], "name_match")
        self.assertEqual(len({edge["target"] for edge in graph["edges"] if edge["relation"] == "authored_by"}), 1)

    def test_fallback_keywords_keep_alias_and_drop_generic_tokens(self):
        terms = knowledge_graph.fallback_keywords("MultiHop-RAG system overview", None, set())
        self.assertIn("RAG", terms)
        self.assertNotIn("system", {term.casefold() for term in terms})

    async def test_fill_similar_writes_cache_without_adding_nodes(self):
        left, right = self.paper(1), self.paper(2)
        outside = {"id": "arxiv:9999.00001", "title": "Outside"}

        async def bundle(raw, refresh):
            if raw != left.local_id:
                return {}
            with connection() as db, db:
                db.execute("INSERT INTO paper_relations VALUES (?,?,?,?)", (left.local_id, "sim", json.dumps({
                    "recommendations": {"computed_at": "2026-10-19T00:00:00+00:00", "items": [outside]},
                    "network": {"edges": [{"source": left.id, "target": right.id, "type": "similar"}]},
                }), "2026-10-19T00:00:00+00:00"))
            return {}

        with patch("app.services.retrieval.recommend.bundle", new=bundle):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                filled = await client.post("/api/knowledge-graph/similar", json={"paper_ids": [left.id, right.id]})
        self.assertEqual(filled.status_code, 200, filled.text)
        self.assertIsNone(library.get_paper("arxiv:9999.00001"))
        graph = knowledge_graph.build(references=[left.id, right.id])
        similars = [edge for edge in graph["edges"] if edge["relation"] == "similar"]
        self.assertEqual(len(similars), 1)
        self.assertEqual(library.list_papers(1, 20)["total"], 2)

    async def test_fill_citations_keeps_direction_and_does_not_import_or_call_model(self):
        from app.infrastructure import llm
        from app.services.retrieval import search

        left, right = self.paper(1), self.paper(2)
        outside = PaperOut(id="arxiv:9999.00001", title="Outside", arxiv_id="9999.00001")
        cited = PaperOut(id=right.id, title=right.title, arxiv_id=right.arxiv_id)

        async def links(_client, _ref, kind):
            if kind == "references":
                return [cited, outside]
            raise search.SourceFailure("被引列表暂不可用")

        with patch("app.infrastructure.llm.complete", new=unittest.mock.AsyncMock(side_effect=AssertionError("graph must not call the model"))), \
                patch("app.services.retrieval.search.fetch_s2_links", new=links), \
                patch("app.services.retrieval.search.semantic_enabled", return_value=True):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                opened = await client.post("/api/knowledge-graph", json=[left.id, right.id])
                self.assertEqual(opened.status_code, 200)
                llm.complete.assert_not_called()
                filled = await client.post("/api/knowledge-graph/citations", json={"paper_ids": [left.id, right.id]})
        self.assertEqual(filled.status_code, 200, filled.text)
        self.assertIsNone(library.get_paper(outside.id))
        self.assertEqual(library.list_papers(1, 20)["total"], 2)
        graph = knowledge_graph.build(references=[left.id, right.id])
        cites = [edge for edge in graph["edges"] if edge["relation"] == "cites"]
        self.assertEqual([(edge["source"], edge["target"]) for edge in cites], [(f"paper:{left.local_id}", f"paper:{right.local_id}")])
        left_node = next(node for node in graph["nodes"] if node.get("local_id") == left.local_id)
        self.assertEqual(left_node["citation_status"], "partial")
        self.assertIn("失败", left_node["citation_note"])


if __name__ == "__main__":
    unittest.main()

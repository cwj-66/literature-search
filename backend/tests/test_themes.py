import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure.db import connection
from app.main import app
from app.schemas import PaperOut
from app.services.knowledge_graph import themes
from app.services.papers import library


class ThemeAlgorithmTests(unittest.TestCase):
    def test_cooccurrence_counts_pairs_and_frequencies(self):
        edges, freq = themes.cooccurrence([["a", "b", "a"], ["b", "c"], ["a", "b"]])
        self.assertEqual(freq, {"a": 2, "b": 3, "c": 1})
        self.assertEqual(edges[("a", "b")], 2)
        self.assertEqual(edges[("b", "c")], 1)
        self.assertNotIn(("a", "c"), edges)

    def test_salton_cosine(self):
        self.assertAlmostEqual(themes.salton_cosine(3, 4, 9), 0.5)
        self.assertEqual(themes.salton_cosine(1, 0, 5), 0.0)

    def test_quadrants_follow_bibliometrix_median_split(self):
        items = [
            {"centrality": 9.0, "density": 8.0},   # high cen, high den
            {"centrality": 1.0, "density": 7.0},   # low cen, high den
            {"centrality": 8.0, "density": 1.0},   # high cen, low den
            {"centrality": 0.5, "density": 0.5},   # low, low
        ]
        themes.assign_quadrants(items)
        self.assertEqual([item["quadrant"] for item in items], ["motor", "basic", "emerging", "niche"])

    def test_split_periods_two_for_short_span_three_for_long(self):
        self.assertEqual(themes.split_periods([2022, 2023, 2024, 2025]), [(2022, 2023), (2024, 2025)])
        self.assertEqual(themes.split_periods([2018, 2025]), [(2018, 2020), (2021, 2023), (2024, 2025)])
        self.assertEqual(themes.split_periods([2023]), [(2023, 2023)])
        self.assertEqual(themes.split_periods([]), [])

    def test_period_links_use_jaccard_threshold(self):
        period_themes = [
            [{"keywords": ["a", "b", "c"], "name": "T1"}, {"keywords": ["x"], "name": "T2"}],
            [{"keywords": ["a", "b", "d"], "name": "T1"}, {"keywords": ["y", "z"], "name": "T3"}],
        ]
        links = themes.period_links(period_themes)
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertAlmostEqual(link["weight"], 2 / 4)
        self.assertEqual((link["from"], link["to"]), ("0:0", "1:0"))
        self.assertEqual(link["from_label"], "T1")

    def test_analyze_themes_finds_two_communities_and_by_year(self):
        papers = [
            {"local_id": f"p{i}", "year": 2023 + (i % 2), "keywords": ["retrieval", "llm"]}
            for i in range(3)
        ] + [
            {"local_id": f"q{i}", "year": 2023, "keywords": ["diffusion", "image"]}
            for i in range(3)
        ]
        result = themes.analyze_themes(papers)
        self.assertEqual(len(result), 2)
        keyword_sets = {frozenset(t["keywords"]) for t in result}
        self.assertEqual(keyword_sets, {frozenset({"retrieval", "llm"}), frozenset({"diffusion", "image"})})
        retrieval = next(t for t in result if "retrieval" in t["keywords"])
        self.assertEqual(retrieval["size"], 3)
        self.assertEqual(retrieval["by_year"], {"2023": 2, "2024": 1})
        self.assertTrue(all(t["quadrant"] in {"motor", "basic", "emerging", "niche"} for t in result))

    def test_analyze_themes_degrades_to_single_theme_without_structure(self):
        papers = [{"local_id": f"p{i}", "year": 2023, "keywords": [f"k{i}"]} for i in range(4)]
        result = themes.analyze_themes(papers)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["keywords"]), {"k0", "k1", "k2", "k3"})
        self.assertEqual(result[0]["density"], 0.0)

    def test_analyze_themes_empty_input(self):
        self.assertEqual(themes.analyze_themes([]), [])
        self.assertEqual(themes.analyze_themes([{"local_id": "p", "year": 2023, "keywords": []}]), [])


class ThemeRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "themes.sqlite3"), "ANALYSIS_WORKER": "0"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def paper(self, i=1, year=2023, **kwargs):
        ref = f"arxiv:2301.{i:05d}"
        values = dict(id=ref, title=f"Paper {i}", year=year, authors=["Alice"], venue="Test Conference", abstract="Abstract")
        values.update(kwargs)
        return library.save_paper(PaperOut(**values), ref)[0]

    def analyze(self, paper, payload, result="r1"):
        stamp = "2026-10-17T00:00:00Z"
        with connection() as db, db:
            db.execute("INSERT INTO analysis_tasks (task_id,paper_ref,local_id,status,model_name,prompt_version,created_at) VALUES (?,?,?,'success','test-model','v1',?)", (result, paper.id, paper.local_id, stamp))
            db.execute("INSERT INTO analysis_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (result, result, paper.local_id, json.dumps(payload), "abstract", None, "abstract", paper.url, "test-model", "v1", "{}", stamp))

    async def test_empty_library_returns_empty_result(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/knowledge-graph/themes", json=[])
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["themes"], [])
        self.assertEqual(data["evolution"], {"periods": [], "links": []})
        self.assertEqual(data["analyzed_papers"], 0)
        self.assertIn("不代表整个研究领域", data["scope_note"])

    async def test_themes_are_named_via_llm_and_cached_per_cluster(self):
        for i in range(3):
            p = self.paper(i + 1, year=2022 + i)
            self.analyze(p, {"keywords": ["retrieval", "llm"]}, result=f"r{i}")
        for i in range(3, 6):
            p = self.paper(i + 1, year=2022 + i % 4)
            self.analyze(p, {"keywords": ["diffusion", "image"]}, result=f"r{i}")
        llm_answer = '{"name": "检索增强生成", "description": "基于大模型的检索增强研究"}'
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value=llm_answer)) as complete:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/knowledge-graph/themes")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["analyzed_papers"], 6)
        self.assertEqual(len(data["themes"]), 2)
        names = {theme["name"] for theme in data["themes"]}
        self.assertEqual(names, {"检索增强生成"})
        self.assertEqual(data["themes"][0]["description"], "基于大模型的检索增强研究")
        self.assertEqual(len(data["evolution"]["periods"]), 2)
        with connection() as db:
            cached = db.execute("SELECT COUNT(*) FROM theme_names").fetchone()[0]
        self.assertEqual(cached, 2)
        # Second run: naming comes from the cache, the LLM is not called again.
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=AssertionError("must use cache"))) as complete:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                again = await client.get("/api/knowledge-graph/themes")
        self.assertEqual(again.status_code, 200)
        self.assertEqual({t["name"] for t in again.json()["themes"]}, {"检索增强生成"})

    async def test_empty_workspace_does_not_read_library_or_call_model(self):
        paper = self.paper(1)
        self.analyze(paper, {"keywords": ["retrieval", "llm"]})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=AssertionError("no model call"))) as complete:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/knowledge-graph/themes", json=[])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_papers"], 0)
        self.assertEqual(response.json()["themes"], [])
        self.assertIn("当前工作区", response.json()["scope_note"])
        complete.assert_not_awaited()

    async def test_llm_failure_degrades_to_top_keyword(self):
        p = self.paper(1)
        self.analyze(p, {"keywords": ["retrieval", "llm"]})
        with patch("app.infrastructure.llm.complete", new=AsyncMock(side_effect=Exception("no model"))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/knowledge-graph/themes", json=[p.id])
        self.assertEqual(response.status_code, 200, response.text)
        theme = response.json()["themes"][0]
        self.assertIn(theme["name"], {"retrieval", "llm"})
        self.assertEqual(theme["description"], "")

    async def test_references_scoping_validation_and_missing_analysis(self):
        with_keywords = self.paper(1)
        self.analyze(with_keywords, {"keywords": ["retrieval", "llm"]})
        without = self.paper(2)
        with patch("app.infrastructure.llm.complete", new=AsyncMock(return_value='{"name": "检索增强生成", "description": "x"}')):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/knowledge-graph/themes", json=[with_keywords.id, without.id, "missing"])
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["total_papers"], 2)
                self.assertEqual(data["analyzed_papers"], 1)
                theme = data["themes"][0]
                self.assertEqual(theme["paper_ids"], [with_keywords.local_id])
                self.assertIn("当前工作区", data["scope_note"])
                self.assertEqual((await client.post("/api/knowledge-graph/themes", json=[""])).status_code, 422)
                self.assertEqual((await client.post("/api/knowledge-graph/themes", json=["x" * 301])).status_code, 422)


if __name__ == "__main__":
    unittest.main()

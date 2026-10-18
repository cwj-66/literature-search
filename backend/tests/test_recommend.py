import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import httpx

from app.infrastructure import embeddings
from app.services.papers import library
from app.services.retrieval import recommend, relation_score, search
from app.main import app
from app.services.retrieval.relation_score import DEFAULT_WEIGHTS
from app.schemas import PaperOut
from app.services.retrieval.search import SourceFailure


def paper(**extra) -> PaperOut:
    values = dict(id="arxiv:1", arxiv_id="1", title="Retrieval Augmented Generation", year=2020, sources=["arxiv"])
    values.update(extra)
    return PaperOut(**values)


class ScoreTests(unittest.TestCase):
    def test_weights_are_normalized(self):
        with patch.dict(os.environ, {"RELATION_WEIGHT_SEMANTIC": "2", "RELATION_WEIGHT_KEYWORD": "2", "RELATION_WEIGHT_CITATION": "0"}):
            self.assertEqual(relation_score.load_weights(), {"semantic": 0.5, "keyword": 0.5, "citation": 0.0})
        with patch.dict(os.environ, {"RELATION_WEIGHT_SEMANTIC": "0", "RELATION_WEIGHT_KEYWORD": "0", "RELATION_WEIGHT_CITATION": "0"}):
            self.assertEqual(relation_score.load_weights(), DEFAULT_WEIGHTS)

    def test_cosine_clamps_negative_values(self):
        self.assertEqual(relation_score.cosine([1, 0], [1, 0]), 1)
        self.assertEqual(relation_score.cosine([1, 0], [0, 1]), 0)
        self.assertEqual(relation_score.cosine([1, 0], [-1, 0]), 0)

    def test_keywords_prefer_analysis_and_match_phrase(self):
        terms = relation_score.seed_terms(["检索增强", "论文未明确说明"], "Retrieval Augmented Generation", None)
        self.assertEqual(terms, ["检索增强"])
        self.assertEqual(relation_score.match_terms(terms, "关于检索增强生成", None), ["检索增强"])
        fallback = relation_score.seed_terms([], "Retrieval Augmented Generation", None)
        self.assertEqual(fallback, ["retrieval", "augmented", "generation"])

    def test_high_similarity_is_not_a_citation(self):
        result = relation_score.judge(0.92, ["retrieval", "generation"], 2, set(), DEFAULT_WEIGHTS)
        self.assertEqual(result["relations"], ["similar"])
        self.assertEqual(result["citation_score"], 0)
        self.assertTrue(all(item["type"] != "citation" for item in result["reasons"]))
        self.assertNotIn("引用", "".join(item["text"] for item in result["reasons"]))

    def test_citation_without_similarity_stays_a_citation(self):
        result = relation_score.judge(0.1, [], 3, {"out"}, DEFAULT_WEIGHTS)
        self.assertEqual(result["relations"], ["cites"])
        self.assertEqual(result["strength"], 26)
        self.assertEqual(result["reasons"][-1]["type"], "citation")
        self.assertIn("参考文献", result["reasons"][-1]["text"])

    def test_both_relations_can_exist_together(self):
        result = relation_score.judge(0.8, ["retrieval", "generation"], 2, {"in"}, DEFAULT_WEIGHTS)
        self.assertEqual(result["relations"], ["cites", "similar"])
        self.assertIn("被引列表", result["reasons"][-1]["text"])

    def test_missing_semantic_score_is_not_invented(self):
        result = relation_score.judge(None, ["retrieval"], 1, set(), DEFAULT_WEIGHTS)
        self.assertIsNone(result["semantic_score"])
        self.assertEqual(result["strength"], 56)
        self.assertNotIn("similar", result["relations"])
        self.assertTrue(all(item["type"] != "semantic" for item in result["reasons"]))

    def test_unknown_citation_is_not_scored_as_zero(self):
        unknown = relation_score.judge(0.9, ["retrieval"], 1, set(), DEFAULT_WEIGHTS, citation_known=False)
        self.assertIsNone(unknown["citation_score"])
        self.assertIn("未知／暂不可用", unknown["reasons"][-1]["text"])
        known = relation_score.judge(0.9, ["retrieval"], 1, set(), DEFAULT_WEIGHTS)
        self.assertEqual(known["citation_score"], 0)
        recorded = relation_score.judge(0.1, [], 1, {"out"}, DEFAULT_WEIGHTS, citation_known=False)
        self.assertEqual(recorded["citation_score"], 1)

    def test_candidate_cap_prefers_external_search(self):
        groups = [{"remote": True, "directions": set(), "shared": [], "paper": paper(id=f"s{index}", arxiv_id=None, title=f"Search {index}")} for index in range(25)]
        groups.extend({"remote": False, "directions": set(), "shared": ["retrieval"] if index == 0 else [], "paper": paper(id=f"l{index}", arxiv_id=None, title=f"Local {index}")} for index in range(10))
        chosen = recommend.limit_candidates(groups, 40)
        self.assertLessEqual(len(chosen), 40)
        self.assertEqual(sum(1 for item in chosen if item["remote"] and not item["directions"]), 20)
        local = [item for item in chosen if not item["remote"]]
        self.assertLessEqual(len(local), 20)
        self.assertEqual(local[0]["paper"].title, "Local 0")

    def test_embedding_budget_keeps_room_for_non_citations(self):
        groups = [{"directions": {"out"}, "shared": [], "remote": True, "name": index} for index in range(3)]
        groups.append({"directions": set(), "shared": ["retrieval"], "remote": False, "name": "keyword"})
        groups.append({"directions": set(), "shared": [], "remote": False, "name": "local"})
        chosen = recommend.select_for_embedding(groups, 2)
        self.assertEqual([item["name"] for item in chosen], [0, "keyword"])

    def test_link_payload_keeps_real_papers_only(self):
        payload = {"data": [
            {"citedPaper": None},
            {"citedPaper": {"paperId": "abc", "title": "Cited Paper", "year": 2019}},
            {"citedPaper": {"title": "missing id"}},
        ]}
        papers = search.papers_from_links(payload, "citedPaper")
        self.assertEqual([item.semantic_scholar_id for item in papers], ["abc"])
        with self.assertRaises(SourceFailure):
            search.papers_from_links({"data": {}}, "citedPaper")


class RecommendApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
            "ANALYSIS_WORKER": "0",
            "RELATION_WEIGHT_SEMANTIC": "0.55",
            "RELATION_WEIGHT_KEYWORD": "0.25",
            "RELATION_WEIGHT_CITATION": "0.20",
            "EMBEDDING_MODEL": "test-embed",
        })
        self.env.start()
        recommend._LOCKS.clear()
        self.cited = paper(
            id="s2:abc", arxiv_id=None, semantic_scholar_id="abc", title="Graph Theory Notes",
            year=2010, sources=["semantic_scholar"],
        )
        self.embed_calls = 0
        self.recall = patch.object(
            recommend, "recall_remote", new=AsyncMock(return_value=([], [self.cited], [], [], True)),
        )
        self.embed = patch.object(embeddings, "embed_many", self.vectors)
        self.recall.start()
        self.embed.start()

    def tearDown(self):
        self.embed.stop()
        self.recall.stop()
        self.env.stop()
        self.temp.cleanup()

    async def vectors(self, texts, persist=True):
        self.embed_calls += 1
        self.persisted = persist
        mapped = {}
        for text in texts:
            folded = text.casefold()
            mapped[text] = [0.0, 1.0] if "graph theory" in folded or "chocolate" in folded else [1.0, 0.0]
        return mapped

    def store(self):
        library.save_paper(paper(abstract="A retrieval augmented generation approach."), "arxiv:1")
        library.save_paper(paper(
            id="arxiv:9999.00001", arxiv_id="9999.00001", title="Retrieval Augmented Generation Survey",
            year=2021, abstract="Survey of retrieval augmented generation.",
        ), "arxiv:9999.00001")
        library.save_paper(paper(
            id="arxiv:1111.22222", arxiv_id="1111.22222", title="Chocolate Cake Baking",
            year=2018, abstract="A chocolate cake recipe.",
        ), "arxiv:1111.22222")

    async def get(self, path: str):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path)

    async def test_recommendation_keeps_citation_and_similarity_apart(self):
        self.store()
        path = f"/api/papers/{quote('arxiv:1', safe='')}/recommendations"
        response = await self.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        by_title = {item["title"]: item for item in body["items"]}
        self.assertEqual(set(by_title), {"Retrieval Augmented Generation Survey", "Graph Theory Notes"})
        similar = by_title["Retrieval Augmented Generation Survey"]
        cited = by_title["Graph Theory Notes"]
        self.assertEqual(similar["relations"], ["similar"])
        self.assertEqual(similar["strength"], 80)
        self.assertTrue(all(item["type"] != "citation" for item in similar["reasons"]))
        self.assertEqual(cited["relations"], ["cites"])
        self.assertEqual(cited["citation_score"], 1)
        self.assertEqual(cited["reasons"][-1]["type"], "citation")
        self.assertIn("候选论文不足 5 篇", body["warnings"][-1])
        network = await self.get(f"/api/papers/{quote('arxiv:1', safe='')}/network")
        self.assertEqual(network.status_code, 200, network.text)
        edges = network.json()["edges"]
        self.assertIn(
            {"source": "arxiv:1", "target": "arxiv:9999.00001", "type": "similar", "label": "主题相似", "directed": False},
            edges,
        )
        self.assertIn(
            {"source": "arxiv:1", "target": "s2:abc", "type": "cites", "label": "引用", "directed": True},
            edges,
        )
        self.assertFalse(any(edge["type"] == "similar" and edge["target"] == "s2:abc" for edge in edges))
        self.assertFalse(any(edge["type"] == "cites" and "9999.00001" in edge["target"] for edge in edges))
        self.assertEqual(self.embed_calls, 1)
        self.assertFalse(self.persisted)
        self.assertEqual(library.list_papers(1, 50)["total"], 3)
        self.assertIsNone(library.get_paper("s2:abc"))
        cached = await self.get(path)
        self.assertEqual(cached.status_code, 200)
        self.assertEqual(self.embed_calls, 1)
        self.assertEqual(library.list_papers(1, 50)["total"], 3)

    async def test_expired_cache_is_recomputed(self):
        self.store()
        path = f"/api/papers/{quote('arxiv:1', safe='')}/recommendations"
        first = await self.get(path)
        self.assertEqual(first.status_code, 200, first.text)
        saved = library.get_paper("arxiv:1")
        key = recommend.cache_key(saved.local_id, relation_score.load_weights(), embeddings.model_name())
        payload = recommend.read_cache(saved.local_id, key)
        payload["recommendations"]["computed_at"] = "2020-01-01T00:00:00+00:00"
        recommend.write_cache(saved.local_id, key, payload)
        self.assertIsNone(recommend.read_cache(saved.local_id, key))
        again = await self.get(path)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self.embed_calls, 2)

    async def test_unknown_citation_and_failed_vectors_are_not_zero(self):
        self.store()
        path = f"/api/papers/{quote('arxiv:1', safe='')}/recommendations"
        self.recall.stop()
        self.embed.stop()
        try:
            with patch.object(recommend, "recall_remote", new=AsyncMock(return_value=([], [], [], ["引用信息未知／暂不可用"], False))), \
                    patch.object(embeddings, "embed_many", new=AsyncMock(side_effect=embeddings.EmbeddingError("失败"))):
                response = await self.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            items = response.json()["items"]
            self.assertTrue(items)
            self.assertNotIn("Graph Theory Notes", {item["title"] for item in items})
            for item in items:
                self.assertIsNone(item["semantic_score"])
                self.assertIsNone(item["citation_score"])
                self.assertTrue(any("未知／暂不可用" in reason["text"] for reason in item["reasons"]))
                self.assertFalse(any("没有引用" in reason["text"] for reason in item["reasons"]))
            self.assertEqual(library.list_papers(1, 50)["total"], 3)
        finally:
            self.recall.start()
            self.embed.start()

    async def test_recall_uses_search_hits_and_reports_disabled_source(self):
        self.recall.stop()
        hit = paper(id="arxiv:2", arxiv_id="2", title="Another Paper")
        try:
            with patch.object(recommend, "semantic_enabled", return_value=True), \
                    patch.object(search, "search_semantic_scholar", new=AsyncMock(return_value=([hit], 1))), \
                    patch.object(search, "fetch_s2_links", new=AsyncMock(return_value=[])):
                found, outgoing, incoming, warnings, citation_known = await recommend.recall_remote(paper(), ["retrieval"])
            self.assertEqual([item.id for item in found], ["arxiv:2"])
            self.assertEqual(outgoing, [])
            self.assertEqual(incoming, [])
            self.assertEqual(warnings, [])
            self.assertTrue(citation_known)
            with patch.object(recommend, "semantic_enabled", return_value=False):
                _, _, _, disabled, disabled_known = await recommend.recall_remote(paper(), ["retrieval"])
            self.assertIn("已停用", disabled[0])
            self.assertFalse(disabled_known)
        finally:
            self.recall.start()

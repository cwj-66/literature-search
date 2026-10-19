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
    async def test_cached_results_and_network_share_one_computation(self):
        from app.services.retrieval import similarity
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "PAPER_DB_PATH": str(Path(directory) / "papers.sqlite3"), "ANALYSIS_WORKER": "0",
        }):
            recommend._LOCKS.clear()
            seed, _ = library.save_paper(paper(abstract="Retrieval augmented generation."), "arxiv:1")
            candidate = paper(id="arxiv:2", arxiv_id="2", title="Graph Theory Notes")
            recalled = {"pools": {"citation": [candidate], "semantic": [], "keyword": []},
                        "records": [{"paper": candidate, "relation": "out", "via": "arxiv:1"}],
                        "sources": [{"category": "citation", "source": "semantic_scholar", "status": "completed", "count": 1}], "warnings": []}
            plan = {"terms": ["retrieval"], "semantic": ["retrieval"], "keyword": ["retrieval"], "planner": "literal"}
            async def vectors(texts, **kwargs):
                self.assertFalse(kwargs["persist"])
                return "BAAI/bge-m3", {text: [0, 1] if "Graph" in text else [1, 0] for text in texts}
            with patch.object(similarity, "make_plan", new=AsyncMock(return_value=plan)), \
                 patch.object(similarity, "recall", new=AsyncMock(return_value=recalled)), \
                 patch.object(embeddings, "embed_siliconflow", new=AsyncMock(side_effect=vectors)) as embed:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.get("/api/papers/arxiv:1/recommendations")
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()
                    self.assertEqual([g["key"] for g in data["groups"]], ["citation", "semantic", "keyword"])
                    self.assertEqual(data["groups"][0]["items"][0]["id"], "arxiv:2")
                    self.assertEqual(data["vector_policy"], "ephemeral")
                    graph = await client.get("/api/papers/arxiv:1/network")
                    self.assertEqual(graph.status_code, 200, graph.text)
                    self.assertEqual(graph.json()["edges"][0]["category"], "citation")
                    self.assertEqual(embed.await_count, 1)
                    from app.infrastructure.db import connection
                    with connection() as db:
                        self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_embeddings").fetchone()[0], 0)
                    self.assertEqual(library.list_papers(1, 50)["total"], 1)
                    cache = recommend.read_cache(seed.local_id, recommend.cache_key(seed.local_id, relation_score.load_weights(), embeddings.siliconflow_model()))
                    cache["recommendations"]["computed_at"] = "2020-01-01T00:00:00+00:00"
                    recommend.write_cache(seed.local_id, recommend.cache_key(seed.local_id, relation_score.load_weights(), embeddings.siliconflow_model()), cache)
                    again = await client.get("/api/papers/arxiv:1/recommendations")
                    self.assertEqual(again.status_code, 200)
                    self.assertEqual(embed.await_count, 2)

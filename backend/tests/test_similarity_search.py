import copy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import embeddings
from app.schemas import PaperOut
from app.services.retrieval import similarity as sim
from app.services.retrieval import recommend, search
from app.services.retrieval import tools as relation_tools
from app.tools.research_scope import ToolContext


def paper(index, title="Retrieval augmented generation", **extra):
    return PaperOut(id=f"arxiv:{index}", arxiv_id=str(index), title=title,
                    abstract=extra.pop("abstract", "Retrieval augmented generation for multi-hop questions."),
                    sources=extra.pop("sources", ["arxiv"]), **extra)


def recalled(pools, records=None):
    return {"pools": {k: pools.get(k, []) for k in sim.LABELS}, "records": records or [],
            "sources": [{"category": k, "source": "arxiv" if k != "citation" else "semantic_scholar",
                         "status": "completed", "count": len(pools.get(k, []))} for k in sim.LABELS], "warnings": []}


PLAN = {"terms": ["retrieval", "multi-hop"], "semantic": ["multi-hop question answering"],
        "keyword": ["retrieval multi-hop"], "planner": "literal"}


class SimilarityTests(unittest.IsolatedAsyncioTestCase):
    async def test_siliconflow_bge_uses_explicit_provider_and_discards_vectors(self):
        seen = []
        def respond(request):
            seen.append(request)
            payload = json.loads(request.content)
            self.assertEqual(str(request.url), "https://api.siliconflow.cn/v1/embeddings")
            self.assertEqual(request.headers["Authorization"], "Bearer silicon-test")
            self.assertEqual(payload["model"], "BAAI/bge-m3")
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1., 0.]}]})
        client_factory = httpx.AsyncClient
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "silicon-test", "SILICONFLOW_BASE_URL": "https://api.siliconflow.cn/v1",
                                     "SILICONFLOW_EMBEDDING_MODEL": "BAAI/bge-m3", "EMBEDDING_MODEL": "text-embedding-v3", "DASHSCOPE_API_KEY": "dashscope-test"}), \
             patch.object(embeddings.httpx, "AsyncClient", side_effect=lambda **kw: client_factory(transport=httpx.MockTransport(respond), **kw)), \
             patch.object(embeddings, "load_cached", side_effect=AssertionError("must not read stored vectors")), \
             patch.object(embeddings, "store_vectors", side_effect=AssertionError("must not store vectors")):
            model, vectors = await embeddings.embed_siliconflow(["public text"], persist=False)
        self.assertEqual(model, "BAAI/bge-m3")
        self.assertEqual(vectors, {"public text": [1., 0.]})
        self.assertEqual(len(seen), 1)

    async def test_missing_siliconflow_key_never_falls_back_to_dashscope(self):
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "", "DASHSCOPE_API_KEY": "dashscope-test"}), \
             patch.object(embeddings.httpx, "AsyncClient", side_effect=AssertionError("must not use another provider")):
            with self.assertRaisesRegex(embeddings.EmbeddingError, "硅基流动密钥"):
                await embeddings.embed_siliconflow(["public text"], persist=False)
            self.assertEqual(embeddings.model_name(), embeddings.siliconflow_model())
            with self.assertRaisesRegex(embeddings.EmbeddingError, "硅基流动密钥"):
                await embeddings.embed_many(["public text"], persist=False)

    async def test_free_quota_error_is_actionable_and_not_retried(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(400, json={"error": {"message": "Free quota exhausted. Disable use free tier only mode."}})
        transport = httpx.MockTransport(respond)
        client_factory = httpx.AsyncClient
        with patch.object(embeddings.httpx, "AsyncClient", side_effect=lambda **kwargs: client_factory(transport=transport, **kwargs)):
            with self.assertRaisesRegex(embeddings.EmbeddingError, "免费额度已用尽"):
                await embeddings.fetch_vectors("test", ["public text"], key="test", base="https://example.test")
        self.assertEqual(len(requests), 1)

    async def compute(self, data, vector_error=False):
        self.vector_store = {}
        async def vectors(texts, **kwargs):
            self.assertFalse(kwargs["persist"])
            if vector_error:
                raise embeddings.EmbeddingError("offline")
            self.vector_store.update({text: [0.0, 1.0] if "unrelated" in text.lower() else [1.0, 0.0] for text in texts})
            return "BAAI/bge-m3", self.vector_store
        with patch.object(sim, "make_plan", new=AsyncMock(return_value=copy.deepcopy(PLAN))), \
             patch.object(sim, "recall", new=AsyncMock(return_value=data)), \
             patch.object(embeddings, "embed_siliconflow", new=AsyncMock(side_effect=vectors)):
            return await sim.compute(paper("seed"), {})

    async def test_three_groups_survive_unbalanced_pool_and_vectors_are_discarded(self):
        cites = [paper(i, "Unrelated foundational work", abstract="Graph theory") for i in range(24)]
        topics = [paper(100+i, "A different research task", abstract="Semantic material") for i in range(24)]
        terms = [paper(200+i) for i in range(24)]
        data = recalled({"citation": cites, "semantic": topics, "keyword": terms},
                        [{"paper": p, "relation": "out", "via": "arxiv:seed"} for p in cites])
        result = await self.compute(data)
        groups = {g["key"]: g for g in result["recommendations"]["groups"]}
        self.assertEqual(set(groups), set(sim.LABELS))
        self.assertTrue(all(len(g["items"]) == 10 for g in groups.values()))
        self.assertTrue(all(i["semantic_score"] == 0 for i in groups["citation"]["items"]))
        self.assertTrue(all("cites" not in i["relations"] for i in groups["semantic"]["items"]))
        self.assertEqual(result["recommendations"]["candidate_count"], 72)
        self.assertEqual(self.vector_store, {})
        self.assertNotIn('"vector":', json.dumps(result))
        graph_ids = {n["id"] for n in result["network"]["nodes"]}
        for g in groups.values():
            self.assertTrue({i["id"] for i in g["items"]} <= graph_ids)
        self.assertEqual({e["category"] for e in result["network"]["edges"]}, set(sim.LABELS))

    async def test_missing_vectors_do_not_remove_citations_or_keywords(self):
        candidate = paper(1)
        result = await self.compute(recalled({"citation": [candidate], "keyword": [candidate]},
                                   [{"paper": candidate, "relation": "in", "via": "arxiv:seed"}]), True)
        groups = {g["key"]: g for g in result["recommendations"]["groups"]}
        self.assertTrue(groups["citation"]["items"])
        self.assertTrue(groups["keyword"]["items"])
        self.assertEqual(groups["semantic"]["status"], "unavailable")
        self.assertEqual(groups["semantic"]["items"], [])
        self.assertIsNone(groups["keyword"]["items"][0]["semantic_score"])

    async def test_indirect_citation_evidence_does_not_create_direct_citation(self):
        p = paper(1)
        result = await self.compute(recalled({"citation": [p]}, [
            {"paper": p, "relation": "bibliographic_coupling", "via": "arxiv:shared"},
            {"paper": p, "relation": "co_citation", "via": "arxiv:citing"},
        ]))
        item = result["recommendations"]["groups"][0]["items"][0]
        self.assertNotIn("cites", item["relations"])
        self.assertEqual(item["shared_reference_ids"], ["arxiv:shared"])
        self.assertEqual(item["common_citing_ids"], ["arxiv:citing"])
        self.assertTrue(all(e["type"] != "cites" for e in result["network"]["edges"]))

    async def test_seed_removed_and_cross_source_duplicates_preserve_membership(self):
        a = paper(1, doi="10.1/example")
        b = PaperOut(id="s2:other", semantic_scholar_id="other", doi="10.1/example", title=a.title, sources=["semantic_scholar"])
        groups = sim.merge_candidates(paper("seed"), recalled({"citation": [a], "keyword": [b, paper("seed")]}))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["recalled_by"], {"citation", "keyword"})
        self.assertEqual(set(groups[0]["paper"].sources), {"arxiv", "semantic_scholar"})

    async def test_dual_source_recall_reserves_each_pool_and_expands_real_links(self):
        ref, cite, indirect = paper("ref"), paper("cite"), paper("indirect")
        async def links(client, target, kind):
            if target == "ARXIV:seed":
                return [ref] if kind == "references" else [cite]
            return [indirect]
        async def no_sleep(*args):
            return None
        with patch.object(search, "semantic_enabled", return_value=True), \
             patch.object(search, "search_arxiv", new=AsyncMock(return_value=([paper(i) for i in range(20)], 20))) as arxiv, \
             patch.object(search, "search_semantic_scholar", new=AsyncMock(return_value=([paper(100+i) for i in range(20)], 20))) as s2, \
             patch.object(search, "fetch_s2_links", new=AsyncMock(side_effect=links)), \
             patch.object(sim.asyncio, "sleep", new=no_sleep):
            result = await sim.recall(paper("seed"), PLAN)
        self.assertEqual(arxiv.await_count, 2)
        self.assertEqual(s2.await_count, 2)
        for key in ("semantic", "keyword"):
            self.assertEqual(len(result["pools"][key]), 24)
            self.assertEqual({p.arxiv_id for p in result["pools"][key][:2]}, {"0", "100"})
        self.assertEqual({e["relation"] for e in result["records"]}, {"out", "in", "bibliographic_coupling", "co_citation"})

    async def test_s2_failure_still_searches_arxiv_and_reports_all_three_groups(self):
        with patch.object(search, "semantic_enabled", return_value=True), \
             patch.object(search, "search_arxiv", new=AsyncMock(return_value=([paper(1)], 1))), \
             patch.object(search, "search_semantic_scholar", new=AsyncMock(side_effect=search.SourceFailure("429"))), \
             patch.object(search, "fetch_s2_links", new=AsyncMock(side_effect=search.SourceFailure("429"))), \
             patch.object(sim.asyncio, "sleep", new=AsyncMock()):
            data = await sim.recall(paper("seed"), PLAN)
        self.assertTrue(data["pools"]["semantic"])
        self.assertTrue(data["pools"]["keyword"])
        self.assertEqual(data["pools"]["citation"], [])
        result = await self.compute(data)
        self.assertEqual(result["recommendations"]["groups"][0]["status"], "unavailable")

    async def test_llm_plan_is_bounded_and_invented_keywords_are_rejected(self):
        output = {"semantic_queries": ["multi hop retrieval"]*4, "keyword_queries": ["generation retrieval"],
                  "keywords": ["fabricated concept", "retrieval"]}
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}), \
             patch.object(sim.llm, "complete", new=AsyncMock(return_value=json.dumps(output))):
            plan = await sim.make_plan(paper(1), [])
        self.assertEqual(plan["planner"], "llm")
        self.assertNotIn("fabricated concept", plan["terms"])
        self.assertLessEqual(len(plan["semantic"]), 2)

    async def test_invalid_planner_output_falls_back(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}), \
             patch.object(sim.llm, "complete", new=AsyncMock(return_value="not json")):
            plan = await sim.make_plan(paper(1), [])
        self.assertEqual(plan["planner"], "literal_fallback")
        self.assertTrue(plan["semantic"])
        self.assertTrue(plan["keyword"])

    async def test_relationship_module_uses_same_service_in_restricted_context(self):
        p = paper(1)
        response = {"items": [], "groups": [{"key": key, "items": []} for key in sim.LABELS], "warnings": []}
        with patch.object(relation_tools, "_paper", new=AsyncMock(return_value=p)), \
             patch.object(recommend, "discover", new=AsyncMock(return_value=response)) as discover, \
             patch.object(relation_tools.search, "search_papers", new=AsyncMock(side_effect=AssertionError("must not degrade to title search"))):
            result = await relation_tools.execute("find_related_papers", {"paper_id": p.id}, ToolContext(accessible_paper_ids=[p.id]))
        discover.assert_awaited_once_with(p, 10)
        self.assertEqual(len(result["groups"]), 3)

    def test_weighted_keyword_overlap_is_symmetric_and_empty_is_zero(self):
        a, b = ["retrieval", "multi-hop"], ["retrieval", "ranking"]
        self.assertEqual(sim.keyword_overlap(a, b, {"multi-hop": 3}), sim.keyword_overlap(b, a, {"multi-hop": 3}))
        self.assertEqual(sim.keyword_overlap([], [], {}), (0, []))
        self.assertIn("retrieval augmented generation", sim.keywords("Retrieval Augmented Generation"))
        self.assertFalse(sim.literal_match("rag", "a paragraph"))
        self.assertTrue(sim.literal_match("rag", "A RAG-based system"))

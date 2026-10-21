import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.infrastructure import embeddings
from app.schemas import LocalPaperOut, PaperOut
from app.services.analysis.analysis import ResolveError
from app.services.retrieval import recommend, relate, relation_precompute

CATEGORIES = ("citation", "semantic", "keyword")


def seed(arxiv: str, title: str) -> LocalPaperOut:
    return LocalPaperOut(id=f"arxiv:{arxiv}", arxiv_id=arxiv, title=title, year=2024,
                         sources=["arxiv"], abstract=f"Abstract of {title}", local_id=f"local-{arxiv}",
                         imported_at="2024-01-01T00:00:00+00:00")


def item(item_id: str, *, relations=(), categories=(), strength: int = 50) -> dict:
    return {"id": item_id, "title": f"Paper {item_id}", "authors": ["A"], "year": 2023,
            "relations": list(relations), "categories": list(categories), "strength": strength,
            "semantic_score": 0.8, "keyword_score": 0.7, "citation_score": 1.0, "reasons": []}


def payload(group_items: dict[str, list[dict]], fused: list[dict] | None = None, warnings=None, status=None) -> dict:
    groups = [{"key": key, "label": key, "status": (status or {}).get(key, "completed"),
               "recall_count": len(group_items.get(key, [])), "matched_count": len(group_items.get(key, [])),
               "items": group_items.get(key, [])} for key in CATEGORIES]
    fused = fused if fused is not None else [i for key in CATEGORIES for i in group_items.get(key, [])]
    return {"recommendations": {"groups": groups, "items": fused, "warnings": warnings or []}, "network": {}}


class RelateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patchers = [
            patch.object(relate.library, "get_paper"),
            patch.object(recommend, "bundle", new=AsyncMock()),
            patch.object(relation_precompute, "read"),
            patch.object(relation_precompute, "ensure_job"),
            patch.object(embeddings, "embed_siliconflow", new=AsyncMock(side_effect=self._vectors)),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.seeds = {"local-1": seed("1", "Alpha"), "local-2": seed("2", "Beta")}
        relate.library.get_paper.side_effect = lambda raw: self.seeds[raw]

    @staticmethod
    async def _vectors(texts, **kwargs):
        return "test-model", {text: [1.0, 0.0] for text in texts}

    def use_payloads(self, mapping):
        relation_precompute.read.side_effect = lambda raw: {"payload": mapping[raw], "status": "ready", "computed_at": None}

    async def test_top_one_per_category_without_changing_candidate_ranking(self):
        groups = {key: [item(f"c1-{key}-{n}", relations=[key], categories=[key], strength=60 - n) for n in range(1, 8)] for key in CATEGORIES}
        self.use_payloads({"local-1": payload(groups)})
        result = await relate.relate(["local-1"])
        self.assertEqual(len(result["centers"]), 1)
        self.assertEqual(result["centers"][0]["id"], "arxiv:1")
        self.assertEqual(result["centers"][0]["color_index"], 0)
        for group in result["satellites"]:
            self.assertEqual(len(group["items"]), 1)
            self.assertEqual(group["items"][0]["id"], f"c1-{group['category']}-1")
        total = sum(len(group["items"]) for group in result["satellites"])
        self.assertEqual(total, 3)

    async def test_cross_category_item_kept_in_each_category(self):
        shared = item("multi", relations=["cites", "similar"], categories=["citation", "semantic"])
        groups = {"citation": [shared], "semantic": [shared], "keyword": []}
        self.use_payloads({"local-1": payload(groups)})
        result = await relate.relate(["local-1"])
        by_category = {group["category"]: group for group in result["satellites"]}
        self.assertIn("multi", [i["id"] for i in by_category["citation"]["items"]])
        self.assertIn("multi", [i["id"] for i in by_category["semantic"]["items"]])

    async def test_satellite_groups_carry_status_and_include_empty(self):
        groups = {"citation": [item("c1", categories=["citation"])], "semantic": [], "keyword": []}
        self.use_payloads({"local-1": payload(groups, status={"citation": "partial", "semantic": "unavailable"})})
        result = await relate.relate(["local-1"])
        by_category = {group["category"]: group for group in result["satellites"]}
        self.assertEqual(set(by_category), set(CATEGORIES))
        self.assertEqual(by_category["citation"]["status"], "partial")
        self.assertEqual(by_category["citation"]["matched_count"], 1)
        self.assertEqual(by_category["semantic"]["status"], "unavailable")
        self.assertEqual(by_category["semantic"]["items"], [])

    async def test_source_named_warnings_not_forwarded(self):
        groups = {key: [] for key in CATEGORIES}
        warnings = ["主题相似 · semantic_scholar 暂不可用，本次候选可能不全",
                    "检索规划模型暂不可用，已使用标题摘要中的原文词项检索"]
        self.use_payloads({"local-1": payload(groups, warnings=warnings)})
        with self.assertLogs("app.services.retrieval.relate", level="INFO"):
            result = await relate.relate(["local-1"])
        self.assertTrue(any("检索规划模型" in warning for warning in result["warnings"]))
        self.assertFalse(any("semantic_scholar" in warning.lower() for warning in result["warnings"]))

    async def test_overlap_two_centers(self):
        first = {"citation": [item("shared", relations=["cites"], categories=["citation"]), item("a-only", relations=["cites"], categories=["citation"])]}
        second = {"semantic": [item("shared", relations=["similar"], categories=["semantic"]), item("b-only", relations=["similar"], categories=["semantic"])]}
        for key in ("semantic", "keyword"):
            first.setdefault(key, [item(f"a-{key}-1", categories=[key]), item(f"a-{key}-2", categories=[key])])
            second.setdefault(key, [item(f"b-{key}-1", categories=[key]), item(f"b-{key}-2", categories=[key])])
        self.use_payloads({"local-1": payload(first), "local-2": payload(second)})
        result = await relate.relate(["local-1", "local-2"])
        centers = [group["center_id"] for group in result["satellites"] if any(i["id"] == "shared" for i in group["items"])]
        self.assertEqual(sorted(centers), ["arxiv:1", "arxiv:2"])
        overlap = result["overlaps"]
        self.assertEqual(len(overlap), 1)
        self.assertEqual(overlap[0]["paper_id"], "shared")
        self.assertEqual(overlap[0]["center_ids"], ["arxiv:1", "arxiv:2"])

    async def test_center_as_candidate_draws_center_edge(self):
        groups = {key: [item(f"a-{key}-1", categories=[key]), item(f"a-{key}-2", categories=[key])] for key in CATEGORIES}
        groups["citation"] = [item("arxiv:2", relations=["cites"], categories=["citation"], strength=90), item("a-cit-2", categories=["citation"])]
        groups["citation"][0]["evidence"] = [{"type": "out", "via": "arxiv:2", "source": "semantic_scholar"}]
        self.use_payloads({"local-1": payload(groups), "local-2": payload({key: [item(f"b-{key}-1", categories=[key])] for key in CATEGORIES})})
        result = await relate.relate(["local-1", "local-2"])
        edges = [e for e in result["center_edges"] if e["source"] == "arxiv:1" and e["target"] == "arxiv:2"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["type"], "cites")
        self.assertTrue(edges[0]["directed"])
        self.assertEqual(edges[0]["category"], "citation")

    async def test_shared_child_does_not_connect_centers(self):
        self.use_payloads({"local-1": payload({"semantic": [item("shared", categories=["semantic"])]}),
                           "local-2": payload({"keyword": [item("shared", categories=["keyword"])]})})
        result = await relate.relate(["local-1", "local-2"])
        self.assertEqual(result["center_edges"], [])
        self.assertEqual(result["overlaps"], [{"paper_id": "shared", "center_ids": ["arxiv:1", "arxiv:2"]}])

    async def test_discovery_failure_keeps_selected_center(self):
        relation_precompute.read.return_value = {"payload": None, "status": "failed", "computed_at": None}
        result = await relate.relate(["local-1"])
        self.assertEqual([c["id"] for c in result["centers"]], ["arxiv:1"])
        self.assertEqual([g["status"] for g in result["satellites"]], ["unavailable"] * 3)

    async def test_incoming_citation_points_from_other_center(self):
        candidate = item("arxiv:2", relations=["cites"], categories=["citation"])
        candidate["evidence"] = [{"type": "in", "via": "arxiv:2", "source": "semantic_scholar"}]
        self.use_payloads({"local-1": payload({"citation": [candidate]}), "local-2": payload({})})
        result = await relate.relate(["local-1", "local-2"])
        self.assertEqual([(e["source"], e["target"]) for e in result["center_edges"]], [("arxiv:2", "arxiv:1")])

    async def test_promoted_paper_matches_its_provider_alias(self):
        self.seeds["local-2"].semantic_scholar_id = "provider-beta"
        self.use_payloads({"local-1": payload({"semantic": [item("s2:provider-beta", categories=["semantic"])]}),
                           "local-2": payload({})})
        result = await relate.relate(["local-1", "local-2"])
        self.assertEqual(result["satellites"][1]["items"][0]["id"], "arxiv:2")
        self.assertEqual(len(result["center_edges"]), 1)

    async def test_relation_category_does_not_invent_citation_arrow(self):
        self.use_payloads({"local-1": payload({"semantic": [item("arxiv:2", relations=["cites", "similar"], categories=["semantic"])]}),
                           "local-2": payload({})})
        result = await relate.relate(["local-1", "local-2"])
        self.assertEqual(result["center_edges"][0]["type"], "similar")
        self.assertFalse(result["center_edges"][0]["directed"])

    async def test_partial_failure_keeps_successful_centers(self):
        groups = {key: [item(f"a-{key}-1", categories=[key]), item(f"a-{key}-2", categories=[key])] for key in CATEGORIES}
        self.use_payloads({"local-1": payload(groups)})

        def load(raw):
            if raw == "bad":
                raise ResolveError(404, "没有找到这篇论文")
            return self.seeds[raw]

        relate.library.get_paper.side_effect = load
        result = await relate.relate(["local-1", "bad"])
        self.assertEqual([c["id"] for c in result["centers"]], ["arxiv:1"])
        self.assertTrue(any("bad" in warning for warning in result["warnings"]))

    async def test_graph_read_does_not_compute_embeddings_or_discovery(self):
        groups = {key: [item(f"a-{key}-1", categories=[key]), item(f"a-{key}-2", categories=[key])] for key in CATEGORIES}
        self.use_payloads({"local-1": payload(groups), "local-2": payload({key: [item(f"b-{key}-1", categories=[key]), item(f"b-{key}-2", categories=[key])] for key in CATEGORIES})})
        embeddings.embed_siliconflow.side_effect = embeddings.EmbeddingError("无可用向量")
        result = await relate.relate(["local-1", "local-2"])
        for center in result["centers"]:
            self.assertEqual(center["similarities"], {})
        embeddings.embed_siliconflow.assert_not_awaited()
        recommend.bundle.assert_not_awaited()

    async def test_pending_snapshot_returns_main_node_without_waiting(self):
        relation_precompute.read.return_value = {"payload": None, "status": "queued", "computed_at": None}
        result = await relate.relate(["local-1"])
        self.assertEqual(result["pending_ids"], ["local-1"])
        self.assertTrue(all(g["status"] == "pending" for g in result["satellites"]))
        recommend.bundle.assert_not_awaited()

    async def test_refresh_only_enqueues_and_keeps_saved_results(self):
        self.use_payloads({"local-1": payload({"semantic": [item("B", categories=["semantic"])]})})
        result = await relate.relate(["local-1"], True)
        relation_precompute.ensure_job.assert_called_once_with("local-1", True)
        self.assertEqual(result["satellites"][1]["items"][0]["id"], "B")
        recommend.bundle.assert_not_awaited()

    async def test_no_backfill_from_fused_items(self):
        groups = {key: [item(f"chosen-{key}", categories=[key])] for key in CATEGORIES}
        fused = [item(f"extra-{n}", categories=["semantic"], strength=100 - n) for n in range(4)] + [item(f"chosen-{key}", categories=[key]) for key in CATEGORIES]
        self.use_payloads({"local-1": payload(groups, fused=fused)})
        result = await relate.relate(["local-1"])
        chosen = {i["id"] for group in result["satellites"] for i in group["items"]}
        self.assertEqual(chosen, {f"chosen-{key}" for key in CATEGORIES})


class RelateApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_accepts_more_than_five_ids(self):
        from app.main import app
        ids = [str(i) for i in range(12)]
        with patch.object(relate, "relate", new=AsyncMock(return_value={"centers": []})) as mocked:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/papers/relate", json={"ids": ids})
        self.assertEqual(response.status_code, 200)
        mocked.assert_awaited_once_with(ids, False)

    async def test_endpoint_returns_relate_payload(self):
        from app.main import app
        fixed = {"centers": [{"id": "arxiv:1", "local_id": "local-1", "title": "T", "year": 2024,
                              "authors": [], "venue": None, "citation_count": None, "color_index": 0, "similarities": {}}],
                 "satellites": [], "center_edges": [], "overlaps": [], "warnings": ["w"],
                 "computed_at": "2024-01-01T00:00:00+00:00"}
        with patch.object(relate, "relate", new=AsyncMock(return_value=fixed)) as mocked:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/papers/relate", json={"ids": ["local-1"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["centers"][0]["id"], "arxiv:1")
        mocked.assert_awaited_once_with(["local-1"], False)

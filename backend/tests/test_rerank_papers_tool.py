"""rerank_papers tool: deterministic filtering + LLM scoring, honest shortfall."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.tools import rerank_papers as tool


def paper(pid, title, abstract="", year=2024):
    return {"id": pid, "title": title, "abstract": abstract, "year": year, "url": f"https://example.test/{pid}"}


def score_all(papers, query, *, min_candidates=20):
    return [{**p, "rank_score": 9} for p in papers], 1


EMBEDDING_PAPER = paper("arxiv:emb1", "Text Embedding Models for Dense Retrieval",
                        "We study text embedding models and sentence embeddings for retrieval.", 2024)
EMBEDDING_PAPER_2 = paper("arxiv:emb2", "Improving Sentence Embeddings with Contrastive Training",
                          "Our sentence embedding model outperforms baselines on dense embedding benchmarks.", 2023)
IR_PAPER = paper("arxiv:ir1", "A Survey of Web Search Ranking",
                 "This survey covers information retrieval and web search ranking algorithms.", 2022)
RAG_APP_PAPER = paper("arxiv:rag1", "Retrieval-Augmented Generation for Customer Support Applications",
                      "We deploy retrieval-augmented generation in a real-world customer support application.", 2024)
RAG_IMMUNOLOGY = paper("pubmed:rag9", "RAG gene deficiency in mice",
                       "RAG recombination defects impair V(D)J recombination in lymphocytes.", 2021)


class RerankPapersTests(unittest.IsolatedAsyncioTestCase):
    async def test_relevance_filter_excludes_off_topic(self):
        candidates = [RAG_APP_PAPER, RAG_IMMUNOLOGY]
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找3篇有关RAG应用的论文", candidates, target_count=3)
        self.assertEqual(result["recommended_ids"], ["arxiv:rag1"])
        self.assertEqual(result["filtered_out_count"], 1)

    async def test_embedding_topic_rejects_generic_ir(self):
        candidates = [EMBEDDING_PAPER, EMBEDDING_PAPER_2, IR_PAPER]
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文", candidates, target_count=5)
        self.assertEqual(result["recommended_ids"], ["arxiv:emb1", "arxiv:emb2"])

    async def test_shortfall_not_filled_with_unrelated(self):
        candidates = [EMBEDDING_PAPER, EMBEDDING_PAPER_2, IR_PAPER,
                      paper("arxiv:ir2", "Classical IR Models", "Boolean retrieval and inverted indexes.", 2020),
                      paper("arxiv:ir3", "Search Engine Architectures", "Crawling and indexing for web search.", 2019),
                      paper("arxiv:ir4", "Query Expansion Techniques", "Information retrieval query expansion.", 2018)]
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文", candidates, target_count=5)
        self.assertEqual(len(result["recommended"]), 2)
        self.assertFalse(result["enough"])
        self.assertIn("仅 2 篇", result["shortfall_reason"])

    async def test_no_abstract_marked_in_reason(self):
        no_abstract = paper("arxiv:emb3", "Dense Embedding Models for Biomedical Text", "", 2024)
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文", [no_abstract], target_count=5)
        self.assertEqual(result["recommended_ids"], ["arxiv:emb3"])
        self.assertIn("无摘要", result["reasons"]["arxiv:emb3"])

    async def test_year_constraint_filter(self):
        old = paper("arxiv:emb0", "Early Text Embedding Methods", "Old text embedding work.", 2020)
        constraints = {"year_from": 2023, "year_to": 2024, "exclusions": []}
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文", [old, EMBEDDING_PAPER],
                                              target_count=5, constraints=constraints)
        self.assertEqual(result["recommended_ids"], ["arxiv:emb1"])

    async def test_low_rank_score_rejected(self):
        def score_low(papers, query, *, min_candidates=20):
            return [{**p, "rank_score": 4 if p["id"] == "arxiv:emb1" else 9} for p in papers], 1

        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_low)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文", [EMBEDDING_PAPER, EMBEDDING_PAPER_2],
                                              target_count=5)
        self.assertEqual(result["recommended_ids"], ["arxiv:emb2"])

    async def test_remaining_and_consistency(self):
        extra1 = paper("arxiv:emb4", "Multilingual Text Embeddings", "Multilingual sentence embedding models.", 2023)
        extra2 = paper("arxiv:emb5", "Embedding Models for Code Search", "Code search with dense embeddings.", 2022)
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文",
                                              [EMBEDDING_PAPER, EMBEDDING_PAPER_2, extra1, extra2], target_count=2)
        self.assertEqual(len(result["recommended_ids"]), 2)
        self.assertEqual(len(result["recommended"]), 2)
        for item in result["recommended"]:
            self.assertIn("score", item)
            self.assertIn("reason", item)
        remaining = set(result["remaining_candidates"])
        self.assertEqual(len(remaining), 2)
        self.assertTrue(remaining.isdisjoint(result["recommended_ids"]))

    async def test_fewer_candidates_than_target_still_filtered(self):
        with patch("app.services.retrieval.ranking.llm_rank", new=AsyncMock(side_effect=score_all)):
            result = await tool.rerank_papers("帮我找5篇有关向量大模型的论文", [EMBEDDING_PAPER, IR_PAPER],
                                              target_count=5)
        self.assertEqual(result["recommended_ids"], ["arxiv:emb1"])
        self.assertFalse(result["enough"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3"),
                                           "RESEARCH_CHECKPOINT": "memory"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

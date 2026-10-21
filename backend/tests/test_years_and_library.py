import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.services.papers import library
from app.services.retrieval import search as provider
from app.main import app
from app.schemas import PaperOut


def paper(ref="2005.11401", year=2023):
    return PaperOut(id=f"arxiv:{ref}", arxiv_id=ref, title="Retrieval-Augmented Generation", year=year,
                    authors=["Author"], abstract="Abstract", sources=["arxiv"], venue="arXiv（预印本）")


class YearsAndLibraryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"PAPER_DB_PATH": str(Path(self.temp.name) / "papers.sqlite3")})
        self.env.start()
        for cache in (provider._CACHE, provider._PAPERS, provider._GATES, provider._NEXT, provider._COOLDOWN):
            cache.clear()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_multiword_query_groups_all_terms_before_date_and_covers_whole_day(self):
        self.assertEqual(provider.arxiv_search_query("Retrieval-Augmented Generation", 2022, 2023),
            '(all:"Retrieval-Augmented" AND all:"Generation") AND submittedDate:[202201010000 TO 202312312359]')
        self.assertEqual(provider.arxiv_search_query("transformer", None, 2023),
            '(all:"transformer") AND submittedDate:[190001010000 TO 202312312359]')

    async def test_out_of_range_and_unknown_years_cannot_escape_api_even_if_provider_leaks(self):
        bad = [paper(f"2401.{i:05}", year=year) for i, year in enumerate([2024, 2025, 2026, None])]
        valid = [paper(f"2201.{i:05}", year=2022 + i % 2) for i in range(40)]
        async def fetch(*args):
            return bad + valid, 44
        with patch.object(provider, "search_arxiv", side_effect=fetch), patch.object(provider, "search_semantic_scholar", side_effect=fetch), patch.dict(os.environ, {"SEMANTIC_SCHOLAR_ENABLED": "true"}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for query in ("Retrieval-Augmented Generation", "transformer"):
                    for source in ("arxiv", "semantic_scholar", "all"):
                        ids = []
                        for page in (1, 2):
                            response = await client.get("/api/papers/search", params={"q": query, "source": source, "year_from": 2022, "year_to": 2023, "page": page, "page_size": 20})
                            self.assertEqual(response.status_code, 200)
                            rows = response.json()["papers"]
                            self.assertEqual(len(rows), 20)
                            self.assertTrue(all(row["year"] in (2022, 2023) for row in rows))
                            ids.extend(row["id"] for row in rows)
                        self.assertEqual(len(ids), len(set(ids)))

    async def test_filter_refills_after_a_whole_rejected_batch(self):
        async def fetch(offset, size):
            return ([paper(f"2401.{i:05}", 2025) for i in range(size)] if offset == 0
                    else [paper(f"2201.{i:05}", 2022) for i in range(size)]), 200
        rows, total = await provider.unique_prefix(fetch, 20, 1000, 2022, 2023)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(row.year == 2022 for row in rows))

    def test_reference_normalization_and_untrusted_urls(self):
        for ref in ("2005.11401v2", "arxiv:2005.11401v2", "https://arxiv.org/pdf/2005.11401v2.pdf?download=1"):
            self.assertEqual(library.normalize_reference(ref), "arxiv:2005.11401")
        self.assertEqual(library.normalize_reference("https://doi.org/10.1234/TEST"), "doi:10.1234/test")
        self.assertEqual(library.normalize_reference("https://arxiv.org/abs/cs/9901001"), "arxiv:cs/9901001")
        for ref in ("http://127.0.0.1/admin", "https://arxiv.org.evil.test/abs/2005.11401", "file:///etc/passwd", "garbage"):
            with self.assertRaises(ValueError):
                library.normalize_reference(ref)

    async def test_import_round_trip_duplicate_and_reopen_without_upstream(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("app.main.fetch_paper", new=AsyncMock(return_value=paper())) as fetch:
                result = await client.post("/api/papers/import", json={"external_id": "arxiv:2005.11401"})
                self.assertEqual(result.status_code, 201)
                imported = result.json()["paper"]
                self.assertTrue(imported["local_id"])
                self.assertTrue(imported["imported_at"])
                duplicate = await client.post("/api/papers/import", json={"url": "https://arxiv.org/pdf/2005.11401v2.pdf"})
                self.assertEqual(duplicate.status_code, 200)
                self.assertFalse(duplicate.json()["created"])
                self.assertEqual(duplicate.json()["paper"]["local_id"], imported["local_id"])
                fetch.assert_awaited_once()
            provider._PAPERS.clear()
            with patch("app.main.fetch_paper", new=AsyncMock(side_effect=AssertionError("must read SQLite"))):
                for ref in (imported["local_id"], imported["id"]):
                    response = await client.get(f"/api/papers/{ref}")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["abstract"], "Abstract")
                listing = (await client.get("/api/papers")).json()
                self.assertEqual(listing["total"], 1)
            # A new connection with no process cache retrieves the persisted row.
            self.assertEqual(library.get_paper(imported["local_id"]).title, imported["title"])

    async def test_concurrent_import_and_doi_alias_merge(self):
        original = paper()
        extra = PaperOut(id="s2:" + "a" * 40, semantic_scholar_id="a" * 40, doi="10.1234/test", arxiv_id=original.arxiv_id,
                         title=original.title, citation_count=5, sources=["semantic_scholar"])
        results = await asyncio.gather(*[asyncio.to_thread(library.save_paper, original, original.id) for _ in range(6)])
        self.assertEqual(sum(created for _, created in results), 1)
        merged, created = library.save_paper(extra, "doi:10.1234/test")
        self.assertFalse(created)
        self.assertEqual(merged.citation_count, 5)
        self.assertEqual(library.list_papers(1, 20)["total"], 1)
        self.assertEqual(library.get_paper("doi:10.1234/test").local_id, results[0][0].local_id)

    async def test_import_with_snapshot_skips_upstream_fetch(self):
        snapshot = {"title": "Snapshot Title", "authors": ["A", "B"], "year": 2026, "venue": "arXiv（预印本）",
                    "abstract": "S", "url": "https://arxiv.org/abs/2604.11209", "citation_count": 3}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("app.main.fetch_paper", new=AsyncMock(side_effect=AssertionError("must not fetch upstream"))):
                result = await client.post("/api/papers/import", json={"external_id": "arxiv:2604.11209", "snapshot": snapshot})
                self.assertEqual(result.status_code, 201)
                saved = result.json()["paper"]
                self.assertEqual(saved["title"], "Snapshot Title")
                self.assertEqual(saved["authors"], ["A", "B"])
                self.assertEqual(saved["arxiv_id"], "2604.11209")
                self.assertEqual(saved["citation_count"], 3)
                duplicate = await client.post("/api/papers/import", json={"external_id": "2604.11209v2", "snapshot": snapshot})
                self.assertEqual(duplicate.status_code, 200)
                self.assertFalse(duplicate.json()["created"])
                self.assertEqual(duplicate.json()["paper"]["local_id"], saved["local_id"])
                doi = await client.post("/api/papers/import", json={"external_id": "doi:10.1234/snap", "snapshot": {**snapshot, "url": None}})
                self.assertEqual(doi.status_code, 201)
                self.assertEqual(doi.json()["paper"]["doi"], "10.1234/snap")
                self.assertEqual(doi.json()["paper"]["url"], "https://doi.org/10.1234/snap")
                bad = await client.post("/api/papers/import", json={"external_id": "arxiv:2604.11210", "snapshot": {**snapshot, "title": "  "}})
                self.assertEqual(bad.status_code, 400)
                self.assertIsInstance(bad.json()["detail"], str)

    async def test_semantic_key_is_stored_server_side_and_used(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            status = (await client.get("/api/sources")).json()["semantic_scholar"]
            self.assertFalse(status["key_configured"])
            saved = await client.post("/api/config/semantic-scholar", json={"api_key": "secret-key-1"})
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()["key_configured"])
            self.assertNotIn("secret-key-1", saved.text)
            status = (await client.get("/api/sources")).json()["semantic_scholar"]
            self.assertTrue(status["key_configured"])
            self.assertNotIn("secret-key-1", status["message"])
            self.assertEqual(provider.s2_headers()["x-api-key"], "secret-key-1")
            cleared = await client.post("/api/config/semantic-scholar", json={"api_key": ""})
            self.assertFalse(cleared.json()["key_configured"])
            self.assertNotIn("x-api-key", provider.s2_headers())

    async def test_import_errors_do_not_write_and_allow_retry(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for payload, status in [({}, 422), ({"external_id": "invalid"}, 400), ({"url": "http://localhost/a"}, 400)]:
                response = await client.post("/api/papers/import", json=payload)
                self.assertEqual(response.status_code, status)
                self.assertIsInstance(response.json()["detail"], str)
            with patch("app.main.fetch_paper", new=AsyncMock(side_effect=httpx.ReadTimeout("upstream"))):
                response = await client.post("/api/papers/import", json={"arxiv_id": "2005.11401"})
                self.assertEqual(response.status_code, 504)
                self.assertEqual((await client.get("/api/papers")).json()["total"], 0)
            with patch("app.main.fetch_paper", new=AsyncMock(return_value=paper())):
                self.assertEqual((await client.post("/api/papers/import", json={"arxiv_id": "2005.11401"})).status_code, 201)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import metadata
from app.schemas import PaperOut


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    def test_crossref_and_unpaywall_fill_missing_fields(self):
        paper = PaperOut(id="doi:10.1/x", title="Old", doi="10.1/x", sources=["semantic_scholar"])
        work = {
            "title": ["New Title"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "published": {"date-parts": [[2024, 1, 1]]},
            "container-title": ["NeurIPS"],
            "DOI": "10.1/x",
            "is-referenced-by-count": 12,
            "URL": "https://doi.org/10.1/x",
        }
        filled = metadata.apply_crossref(paper, work)
        self.assertEqual(filled.authors, ["Ada Lovelace"])
        self.assertEqual(filled.year, 2024)
        oa = metadata.apply_unpaywall(filled, {"best_oa_location": {"url_for_pdf": "https://arxiv.org/pdf/1"}})
        self.assertEqual(oa.pdf_url, "https://arxiv.org/pdf/1")
        self.assertIn("@article", metadata.bibtex_from_paper(oa))

    async def test_lookups_go_through_cached_request(self):
        work = httpx.Response(200, json={"status": "ok", "message": {"DOI": "10.1/x", "title": ["T"]}})
        with patch("app.search.request", new=AsyncMock(return_value=work)) as request:
            async with httpx.AsyncClient() as client:
                result = await metadata.crossref_work(client, "10.1/x")
        self.assertEqual(result["DOI"], "10.1/x")
        self.assertEqual(request.await_args.args[4], "Crossref")
        self.assertTrue(callable(request.await_args.kwargs["accept"]))

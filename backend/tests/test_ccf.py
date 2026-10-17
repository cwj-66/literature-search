import unittest

from app import ccf
from app.schemas import PaperOut


class CcfTests(unittest.TestCase):
    def test_catalog_is_versioned(self):
        info = ccf.info()
        self.assertEqual(info["license"], "MIT")
        self.assertGreater(info["venue_count"], 50)
        self.assertTrue(info["catalog_version"])

    def test_conference_alias_and_full_name(self):
        rank, status, version = ccf.lookup("AAAI")
        self.assertEqual(rank, "A")
        self.assertEqual(status, "verified")
        self.assertTrue(version)
        rank, status, _ = ccf.lookup("Proceedings of the AAAI Conference on Artificial Intelligence")
        self.assertEqual((rank, status), ("A", "verified"))

    def test_preprint_workshop_and_unknown_stay_unranked(self):
        self.assertEqual(ccf.lookup("arXiv（预印本）")[1], "preprint")
        self.assertEqual(ccf.lookup("AAAI Workshop on Something")[:2], (None, "pending"))
        self.assertEqual(ccf.lookup("Unknown Letters")[:2], (None, "unmatched"))

    def test_annotate_does_not_invent_cas_partition(self):
        paper = ccf.annotate(PaperOut(id="arxiv:1", title="t", venue="NeurIPS", sources=["arxiv"]))
        self.assertEqual(paper.ccf_rank, "A")
        self.assertEqual(paper.ccf_status, "verified")
        self.assertIsNone(paper.cas_partition)


if __name__ == "__main__":
    unittest.main()

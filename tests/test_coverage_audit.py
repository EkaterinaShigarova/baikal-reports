"""Audit annotations may lower confidence, never fabricate a scientific review."""

import copy
import unittest
from baikal.core import ROOT, read_json
from baikal.coverage_audit import audit_statuses


class Audit(unittest.TestCase):
    def setUp(self):
        self.r = read_json(ROOT / "datasets/selenga_claim_registry_v2.json")
        self.a = read_json(ROOT / "datasets/selenga_coverage_audit.json")

    def test_inference_is_not_promoted_to_a_direct_source_observation(self):
        self.assertEqual(
            audit_statuses(self.r, self.a)["effective_source_checked_cards"], 14
        )
        self.assertEqual(
            sum(
                c["review_status"] == "source_checked_non_expert"
                for c in self.r["cards"]
            ),
            15,
        )

    def test_false_expertise_promotion_or_wrong_snapshot_rejected(self):
        for mutation in ["expert", "promotion", "snapshot"]:
            a = copy.deepcopy(self.a)
            if mutation == "expert":
                a["expert_review"] = True
            elif mutation == "promotion":
                a["overrides"][0]["effective_status"] = "specialist_verified"
            else:
                a["overrides"][0]["card_sha256"] = "wrong"
            with self.assertRaises(ValueError):
                audit_statuses(self.r, a)


if __name__ == "__main__":
    unittest.main()

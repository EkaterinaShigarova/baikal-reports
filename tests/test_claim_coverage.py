"""Coverage, independent source controls and explicit synthetic safety tests."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from baikal.core import ROOT, read_json, sha256
from baikal.updates import digest
from baikal.claim_coverage import (
    validate_registry,
    validate_cases,
    compare_pair,
    scan_registry,
    article_presence,
)
from scripts.evaluate_coverage import execute


class Coverage(unittest.TestCase):
    def setUp(self):
        self.r = read_json(ROOT / "datasets/selenga_claim_registry_v2.json")
        self.b = read_json(ROOT / "datasets/selenga_comparison_cases_v2.json")
        self.v = read_json(ROOT / "datasets/selenga_term_vocabulary.json")
        self.c = {c["case_id"]: c for c in self.b["examples"]}

    def test_sentence_inventory_does_not_claim_semantic_completeness(self):
        result = validate_registry(self.r, ROOT)
        self.assertEqual(
            (result["cards"], result["represented_units"], result["sentence_units"]),
            (27, 23, 26),
        )
        self.assertEqual(result["source_checked_cards"], 15)
        self.assertFalse(result["semantic_completeness_established"])
        self.assertEqual(
            {d["unit_id"] for d in self.r["deferred"]}, {"U23", "U24", "U26"}
        )

    def test_original_seven_records_preserved_and_interpretations_not_authorities(self):
        old = read_json(ROOT / "datasets/selenga_claim_registry.json")["claims"]
        self.assertEqual(
            [c["legacy_record"] for c in self.r["cards"] if "legacy_record" in c], old
        )
        self.assertEqual(sum(c["matchable"] for c in self.r["cards"]), 9)
        self.assertTrue(
            all(
                c["review_status"] == "source_checked_non_expert"
                for c in self.r["cards"]
                if c["matchable"]
            )
        )

    def test_eleven_new_controls_structural_one_semantic(self):
        checks = validate_cases(self.b, self.r, self.v, ROOT)
        self.assertEqual(checks["semantic_cases"], ["V07"])
        for case in self.b["examples"]:
            relation = compare_pair(case["fact"], case["target"], self.v)["relation"]
            self.assertEqual(
                relation,
                "semantic_required"
                if case["case_id"] == "V07"
                else case["expected"]["relation"],
            )

    def test_absence_never_inferred_from_coverage_even_if_percentage_is_inflated(self):
        r = copy.deepcopy(self.r)
        r["claimed_coverage_percent"] = 100
        fact = self.c["V02"]["fact"]
        scan = scan_registry(fact, r, self.v)
        self.assertEqual(scan["matches"], [])
        self.assertFalse(scan["absence_proven"])
        self.assertEqual(
            article_presence(fact, [], r)["status"],
            "unknown_requires_full_article_review",
        )

    def test_present_passage_is_bound_to_fact_not_just_a_valid_card_id(self):
        case = self.c["V01"]
        cid = case["target"]["target_id"]
        self.assertEqual(
            article_presence(case["fact"], [cid], self.r, vocabulary=self.v)["status"],
            "present_in_registered_passage",
        )
        with self.assertRaisesRegex(ValueError, "bound semantic"):
            article_presence(self.c["V02"]["fact"], [cid], self.r, vocabulary=self.v)

    def test_semantic_equivalence_requires_separate_fact_and_card_bound_review(self):
        case = self.c["V07"]
        cid = case["target"]["target_id"]
        card = next(c for c in self.r["cards"] if c["card_id"] == cid)
        with self.assertRaises(ValueError):
            article_presence(case["fact"], [cid], self.r)
        review = {
            "fact_sha256": digest(case["fact"]),
            "card_sha256": digest(card),
            "relation": "semantic_equivalent",
            "expert_review": False,
            "reason": "Synthetic fixture of a separately reviewed equivalence.",
        }
        result = article_presence(
            case["fact"], [cid], self.r, equivalence_reviews={cid: review}
        )
        self.assertFalse(result["automatic_publication"])
        review["fact_sha256"] = "wrong"
        with self.assertRaises(ValueError):
            article_presence(
                case["fact"], [cid], self.r, equivalence_reviews={cid: review}
            )

    def test_absence_review_is_fact_specific_and_not_editorial_approval(self):
        fact = self.c["V02"]["fact"]
        review = {
            "fact_sha256": digest(fact),
            "article_sha256": self.r["article"]["sha256"],
            "status": "reviewed_absent_for_this_fact",
            "expert_review": False,
            "reason": "Synthetic isolated certificate fixture.",
        }
        out = article_presence(fact, [], self.r, review)
        self.assertTrue(out["candidate_only"])
        self.assertFalse(out["automatic_publication"])
        with self.assertRaises(ValueError):
            article_presence(self.c["V12"]["fact"], [], self.r, review)

    def test_unknown_date_and_missing_value_do_not_turn_into_equality(self):
        case = copy.deepcopy(self.c["V04"])
        case["fact"]["features"]["period"] = None
        self.assertEqual(
            compare_pair(case["fact"], case["target"], self.v)["relation"],
            "insufficient",
        )
        self.assertEqual(
            compare_pair(self.c["V09"]["fact"], self.c["V09"]["target"], self.v)[
                "relation"
            ],
            "insufficient",
        )

    def test_arithmetic_does_not_repair_source_or_cross_bindings(self):
        case = copy.deepcopy(self.c["V08"])
        self.assertFalse(
            compare_pair(case["fact"], case["target"], self.v)["repair_source"]
        )
        case["fact"]["arithmetic_check"]["operands"][0] = "456.49"
        with self.assertRaisesRegex(ValueError, "arithmetic check"):
            compare_pair(case["fact"], case["target"], self.v)

    def test_cross_statistic_value_or_source_cell_corruption_rejected(self):
        b = copy.deepcopy(self.b)
        case = next(c for c in b["examples"] if c["case_id"] == "V03")
        case["fact"]["features"]["value"] = "10,34"
        case["fact_sha256"] = digest(case["fact"])
        with self.assertRaisesRegex(ValueError, "statistic"):
            validate_cases(b, self.r, self.v, ROOT)

    def test_nonempty_manual_notes_cannot_be_excluded_from_inventory_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            r = copy.deepcopy(self.r)
            p = root / r["article"]["path"]
            p.parent.mkdir(parents=True)
            p.write_text(
                (ROOT / r["article"]["path"]).read_text() + "\nSynthetic manual note."
            )
            r["article"]["sha256"] = sha256(p)
            with self.assertRaisesRegex(ValueError, "manual notes"):
                validate_registry(r, root)

    def test_used_slot_stops_before_starting_a_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid = "synthetic"
            marker = root / "data/llm-evaluation" / f"coverage-{pid}.V07.attempt.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}")
            with (
                patch("scripts.evaluate_coverage.load", return_value={}),
                patch("scripts.evaluate_coverage.local_server") as server,
            ):
                with self.assertRaisesRegex(ValueError, "consumed"):
                    execute(SimpleNamespace(experiment=pid), root)
                server.assert_not_called()


if __name__ == "__main__":
    unittest.main()

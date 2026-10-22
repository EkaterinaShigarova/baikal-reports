"""Real cross-topic controls and explicitly synthetic negative integrity fixtures."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from baikal.core import ROOT, read_json, sha256
from baikal.cross_topic import validate, validate_examples, compare, presence, numeric_support
from baikal.updates import digest
from scripts.evaluate_cross_topic import execute


class CrossTopic(unittest.TestCase):
    def setUp(self):
        self.bundle = read_json(ROOT / "datasets/cross_topic_claims.json")
        self.benchmark = read_json(ROOT / "datasets/cross_topic_benchmark.json")
        self.cases = {c["id"]: c for c in self.benchmark["examples"]}

    def test_real_statements_remain_partial_and_source_bound(self):
        result = validate(self.bundle, ROOT)
        self.assertEqual(result["claims"], 27)
        self.assertEqual([a["claims"] for a in result["articles"]], [13, 14])
        self.assertTrue(all(not a["semantic_completeness_established"] for a in result["articles"]))
        self.assertEqual(validate_examples(self.benchmark, self.bundle, sha256(ROOT / "datasets/cross_topic_claims.json"))["examples"], 12)

    def test_new_gold_routes_keep_one_semantic_case(self):
        for case in self.cases.values():
            actual = compare(case["fact"], case["target"])["relation"]
            self.assertEqual(actual, "semantic_required" if case["id"] == "T11" else case["expected"])

    def test_missing_method_and_empty_period_do_not_establish_equality(self):
        original = self.cases["T01"]["fact"]
        for field, value in [("method", None), ("period", {})]:
            incomplete = copy.deepcopy(original)
            if field == "period": incomplete["features"]["period"] = value
            else: incomplete[field] = value
            self.assertEqual(compare(incomplete, original)["relation"], "insufficient")

    def test_different_method_descriptions_are_not_proven_incompatible(self):
        fact = copy.deepcopy(self.cases["T01"]["fact"])
        fact["method"] = {"kind": "synthetic alternative description"}
        self.assertEqual(compare(fact, self.cases["T01"]["target"])["relation"], "comparability_unconfirmed")

    def test_report_discrepancy_is_not_made_a_physical_conflict(self):
        case = self.cases["T05"]
        self.assertFalse(case["documented_source_disagreement"]["same_measurement_conflict_established"])
        self.assertEqual(compare(case["fact"],case["target"])["relation"], "incomparable")
        self.assertEqual(case["fact"]["observation_year"], 2024)
        self.assertEqual(case["target"]["report_year"], 2025)

    def test_wrong_atomic_value_or_year_rejected_even_after_rehashing(self):
        for field in ["value", "observation_year"]:
            bundle = copy.deepcopy(self.bundle)
            card = bundle["articles"][0]["claims"][6]
            if field == "value": card["features"]["value"] = "999"
            else: card["observation_year"] = 2024
            card["sha256"] = digest({k:v for k,v in card.items() if k!='sha256'})
            with self.assertRaises(ValueError): validate(bundle, ROOT)

    def test_numeric_tokens_and_controlled_number_words(self):
        self.assertFalse(numeric_support("70", "147 проб"))
        self.assertTrue(numeric_support("5", "пять нарушений", "пять"))
        self.assertFalse(numeric_support("7", "пять нарушений", "пять"))
        self.assertFalse(numeric_support("5", "непять нарушений", "пять"))

    def test_new_detail_needs_whole_article_review_not_an_empty_scan(self):
        fact = self.cases["T02"]["fact"]
        registry = self.bundle["articles"][0]
        self.assertEqual(presence(fact, registry), "unknown_requires_full_article_review")
        certificate = {"fact_sha256":digest(fact), "article_sha256":registry["article"]["sha256"],
                       "reviewed_block_ids":[b["id"] for b in registry["blocks"]],
                       "expert_review":False,"reason":"Explicitly synthetic certificate fixture"}
        self.assertEqual(presence(fact, registry, absence_review=certificate), "reviewed_new_detail_candidate_only")
        certificate["reviewed_block_ids"].pop()
        with self.assertRaises(ValueError): presence(fact, registry, absence_review=certificate)

    def test_used_model_slot_prevents_server_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pid="synthetic"
            p=root / "data/llm-evaluation" / f"cross-topic-{pid}.T11.attempt.json"
            p.parent.mkdir(parents=True);p.write_text("{}")
            with patch("scripts.evaluate_cross_topic.load",return_value={}), patch("scripts.evaluate_cross_topic.local_server") as server:
                with self.assertRaisesRegex(ValueError,"consumed"):execute(SimpleNamespace(experiment=pid),root)
                server.assert_not_called()


if __name__ == "__main__": unittest.main()

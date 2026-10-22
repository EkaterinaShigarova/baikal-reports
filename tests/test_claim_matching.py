"""Source-bound checks and explicit synthetic adversarial cases; no inference."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from baikal.core import ROOT, read_json
from baikal.claim_matching import (
    validate_registry,
    validate_benchmark,
    structural_compare,
    comparison_input,
    check_response,
    BASES,
)
from scripts.evaluate_claim_matching import call, execute, marker


class ClaimMatching(unittest.TestCase):
    def setUp(self):
        self.registry = read_json(ROOT / "datasets/selenga_claim_registry.json")
        self.benchmark = read_json(ROOT / "datasets/claim_comparison_benchmark.json")
        self.cases = {c["case_id"]: c for c in self.benchmark["examples"]}

    def test_registry_and_balanced_source_bound_cases(self):
        validate_registry(self.registry, ROOT)
        self.assertEqual(
            validate_benchmark(self.benchmark, self.registry, ROOT),
            {label: 2 for label in BASES},
        )
        self.assertEqual(len(self.registry["claims"]), 7)

    def test_presence_precedes_already_documented_opposing_variant(self):
        result = structural_compare(self.cases["A1"], self.registry)
        self.assertEqual(result["classification"], "already_present")
        for key in ["C1", "C2"]:
            result = structural_compare(self.cases[key], self.registry)
            self.assertEqual(result["classification"], "contradiction")
            self.assertFalse(result["new_wiki_conflict"])

    def test_no_structural_match_is_never_automatic_novelty(self):
        for key in ["A2", "N1", "N2"]:
            result = structural_compare(self.cases[key], self.registry)
            self.assertEqual(result["route"], "llm_required")
            self.assertIsNone(result["classification"])

    def test_statistics_units_territories_periods_plans_and_forecasts_differ(self):
        mutations = [
            ("statistic", "mean"),
            ("statistic", "minimum"),
            ("unit", "мг/кг"),
            ("territory", "другая протока"),
            ("period", {"start": 2014, "end": 2015}),
            ("statement_kind", "plan"),
            ("statement_kind", "forecast"),
            ("object", "поверхностная вода реки"),
            ("indicator", "другой показатель"),
        ]
        for key, value in mutations:
            with self.subTest(dimension=key, value=value):
                case = copy.deepcopy(self.cases["A1"])
                case["fact"]["features"][key] = value
                result = structural_compare(case, self.registry)
                self.assertIsNone(result["classification"])

    def test_missing_period_and_unqualified_disputed_measurement_abstain(self):
        for key in ["I1", "I2"]:
            result = structural_compare(self.cases[key], self.registry)
            self.assertEqual(result["classification"], "insufficient_evidence")
            self.assertEqual(result["route"], "deterministic")

    def test_arbitrary_unequal_number_is_not_a_verified_conflict(self):
        case = copy.deepcopy(self.cases["C1"])
        case["fact"]["features"]["value"] = "8,8"  # Explicit synthetic corruption.
        self.assertIsNone(structural_compare(case, self.registry)["classification"])

    def test_reference_context_excludes_expected_answers_and_other_fact_records(self):
        supplied = comparison_input(self.cases["N1"], self.registry)
        self.assertNotIn("expected", supplied)
        self.assertEqual(
            supplied["fact"]["fact_id"], self.cases["N1"]["fact"]["fact_id"]
        )
        self.assertEqual(len(supplied["fact"]["source_context"]["cells"]), 1)
        self.assertEqual(supplied["fact"]["source_context"]["cells"][0]["mean"], "2,1")
        self.assertNotIn("examples", supplied)
        self.assertTrue(
            all(not c["article_quote"].startswith("#") for c in supplied["claims"])
        )

    def test_no_free_prose_foreign_fact_ids_or_heading_citations(self):
        supplied = comparison_input(self.cases["N1"], self.registry)
        result = {
            "fact_id": supplied["fact"]["fact_id"],
            "classification": "new_detail",
            "basis": BASES["new_detail"],
            "supporting_claim_ids": [],
            "preserved_conflict_ids": [c["id"] for c in supplied["known_conflicts"]],
        }
        self.assertEqual(check_response(result, supplied), result)
        for key, value in [
            ("reason", "В 2022 году среднее 61,7 из-за промышленности"),
            ("fact_id", "FOREIGN"),
            ("supporting_claim_ids", ["W9"]),
            ("preserved_conflict_ids", []),
            ("basis", BASES["contradiction"]),
        ]:
            bad = copy.deepcopy(result)
            bad[key] = value
            with self.assertRaises(ValueError):
                check_response(bad, supplied)

    def test_changed_fact_and_cell_fail_provenance_checks(self):
        bad = copy.deepcopy(self.benchmark)
        bad["examples"][2]["fact"]["features"]["value"] = "61,7"
        with self.assertRaisesRegex(ValueError, "Immutable benchmark fact"):
            validate_benchmark(bad, self.registry, ROOT)
        bad = copy.deepcopy(self.benchmark)
        f = bad["examples"][2]["fact"]
        f["source_context"]["cells"][0]["column_index"] = 4
        from baikal.updates import digest

        f["binding_sha256"] = digest(
            {k: v for k, v in f.items() if k != "binding_sha256"}
        )
        with self.assertRaisesRegex(ValueError, "cell/header/column"):
            validate_benchmark(bad, self.registry, ROOT)

    def test_compact_budget_fails_before_reserving_a_model_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "allowed_cases": ["N1"],
                "max_tokens": 512,
                "input_limit_tokens": 3500,
                "prompt": "synthetic",
            }
            job = {
                "case_id": "N1",
                "input": comparison_input(self.cases["N1"], self.registry),
            }
            with patch(
                "scripts.evaluate_claim_matching.preflight",
                return_value={"prompt_tokens_native": 3501},
            ):
                with self.assertRaisesRegex(ValueError, "Compact context"):
                    call(
                        root,
                        "synthetic",
                        manifest,
                        job,
                        "http://127.0.0.1:1/v1/chat/completions",
                        None,
                        {},
                    )
            self.assertFalse((root / "data").exists())

    def test_consumed_experiment_cannot_start_a_runtime_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slot = marker(root, "synthetic", "A2")
            slot.parent.mkdir(parents=True)
            slot.write_text("{}")
            args = SimpleNamespace(experiment="synthetic")
            with (
                patch(
                    "scripts.evaluate_claim_matching.load",
                    return_value={"allowed_cases": ["A2", "N1", "N2"]},
                ),
                patch("scripts.evaluate_claim_matching.local_server") as server,
            ):
                with self.assertRaisesRegex(ValueError, "already used"):
                    execute(args, root)
                server.assert_not_called()


if __name__ == "__main__":
    unittest.main()

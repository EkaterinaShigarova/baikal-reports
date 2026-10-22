"""Synthetic adversarial relation checks, without inference or publication."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from baikal.novelty import (
    check_result,
    render_decisions,
    save_reviewed_draft,
    make_input,
)
from baikal.updates import digest
from scripts.evaluate_novelty import call


class Novelty(unittest.TestCase):
    def setUp(self):
        self.facts = [
            {
                "fact_id": "F1",
                "value": "36,9",
                "observation_year": 2015,
                "territory": "весь полигон",
                "evidence": [{"source_id": "synthetic", "page": 26}],
            },
            {
                "fact_id": "F2",
                "value": "61,7",
                "observation_year": 2015,
                "territory": "протока Усть-Харауз",
                "evidence": [{"source_id": "synthetic", "page": 26}],
            },
        ]
        self.supported = {
            "facts": self.facts,
            "source_conflicts": [{"conflict_id": "C1"}],
        }
        self.wire = {
            "facts": [
                {
                    **{k: v for k, v in f.items() if k != "evidence"},
                    "binding_sha256": digest(f),
                    "source_refs": ["S2"],
                    "source_review_limitations": ["Synthetic limitation"],
                }
                for f in self.facts
            ],
            "source_fragments": [{"id": "S2", **self.facts[0]["evidence"][0]}],
            "wiki": {
                "paragraphs": [{"id": "W1", "text": "Synthetic maximum, not a mean."}]
            },
            "source_conflicts": [{"conflict_id": "C1"}],
        }
        self.result = {
            "decisions": [
                {
                    "fact_id": f["fact_id"],
                    "classification": "new_detail",
                    "explanation": {
                        "fact_id": f["fact_id"],
                        "relation": "observation_not_stated",
                        "wiki_paragraph_ids": ["W1"],
                    },
                }
                for f in self.facts
            ],
            "source_conflicts": [
                {
                    "conflict_id": "C1",
                    "status": "documented_in_wiki",
                    "wiki_paragraph_ids": ["W1"],
                }
            ],
        }

    def test_reference_only_explanation_and_rendering_preserve_facts(self):
        before = copy.deepcopy(self.supported)
        rows = render_decisions(self.result, self.wire, self.supported)
        self.assertEqual([r["fact"] for r in rows], self.facts)
        self.assertEqual(self.supported, before)
        self.assertEqual(
            rows[0]["reason_origin"],
            "deterministic_rendering_of_model_relation_not_model_prose",
        )

    def test_foreign_numbers_periods_and_unsupported_conclusions_rejected(self):
        for explanation in [
            "Среднее 61,7",
            "Наблюдение 2022 года",
            "Это доказывает влияние промышленности",
        ]:
            with self.subTest(explanation=explanation):
                bad = copy.deepcopy(self.result)
                bad["decisions"][0]["explanation"]["reason"] = explanation
                with self.assertRaises(ValueError):
                    check_result(bad, self.wire)

    def test_cross_fact_reason_duplicate_and_false_relation_rejected(self):
        for field, value in [
            ("fact_id", "F2"),
            ("relation", "same_observation_stated"),
            ("wiki_paragraph_ids", ["S2"]),
        ]:
            bad = copy.deepcopy(self.result)
            bad["decisions"][0]["explanation"][field] = value
            with self.assertRaises(ValueError):
                check_result(bad, self.wire)
        bad = copy.deepcopy(self.result)
        bad["decisions"][1] = bad["decisions"][0]
        with self.assertRaises(ValueError):
            check_result(bad, self.wire)

    def test_changed_immutable_value_year_territory_source_rejected(self):
        for field, value in [
            ("value", "61,7"),
            ("observation_year", 2022),
            ("territory", "протока Усть-Харауз"),
            ("source_refs", ["S9"]),
        ]:
            bad = copy.deepcopy(self.wire)
            bad["facts"][0][field] = value
            with self.assertRaises(ValueError):
                render_decisions(self.result, bad, self.supported)

    def test_repeat_and_contradiction_need_wiki_evidence_but_remain_semantic(self):
        for label, relation in [
            ("already_present", "same_observation_stated"),
            ("contradiction", "incompatible_same_observation"),
        ]:
            bad = copy.deepcopy(self.result)
            d = bad["decisions"][0]
            d["classification"] = label
            d["explanation"].update(relation=relation, wiki_paragraph_ids=[])
            with self.assertRaises(ValueError):
                check_result(bad, self.wire)
            d["explanation"]["wiki_paragraph_ids"] = ["W1"]
            # Structure cannot prove that a real Wiki claim is equivalent/contradictory.
            self.assertEqual(check_result(bad, self.wire), bad)

    def test_preflight_failure_does_not_consume_the_single_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "scripts.evaluate_novelty.preflight", side_effect=ValueError("budget")
            ):
                with self.assertRaisesRegex(ValueError, "budget"):
                    call(
                        root,
                        "fixture",
                        {"input": self.wire, "max_tokens": 1200, "prompt": "synthetic"},
                        "http://127.0.0.1:1/v1/chat/completions",
                        None,
                        {},
                    )
            self.assertFalse((root / "data").exists())

    def test_structural_pass_alone_never_creates_a_draft(self):
        manifest = {"supported": self.supported}
        run = {"experiment_id": digest(manifest), "checked": self.result, "errors": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "semantic review required"):
                save_reviewed_draft(root, manifest, digest(run), run, {})
            self.assertFalse((root / "data").exists())

    def test_reviewed_draft_preserves_originals_and_never_writes_wiki(self):
        manifest = {
            "supported": self.supported,
            "input": self.wire,
            "source_review": {"expert_review": False},
        }
        run = {
            "experiment_id": digest(manifest),
            "checked": self.result,
            "parsed": self.result,
            "errors": [],
        }
        rid = digest(run)
        review = {
            "run_id": rid,
            "review_kind": "semantic_comparison_non_expert",
            "expert_review": False,
            "decisions": {
                f["fact_id"]: {
                    "status": "supported" if f["fact_id"] == "F1" else "rejected",
                    "reason": "Synthetic review only.",
                }
                for f in self.facts
            },
            "conflicts": {
                "C1": {"status": "supported", "reason": "Synthetic conflict review."}
            },
        }
        case = {
            "article": {
                "text": "Synthetic article\n<!-- HUMAN NOTES -->\nKeep notes.",
                "sha256": "synthetic",
            },
            "preceding_version": {"path": "synthetic"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("baikal.novelty.adopt", return_value=(case, self.supported, {})),
                patch("baikal.novelty.make_input", return_value=self.wire),
            ):
                out = save_reviewed_draft(root, manifest, rid, run, review)
                from baikal.core import read_json

                packet = read_json(root / out["path"])
                self.assertEqual(out["additions"], 1)
                self.assertEqual(packet["additions"][0]["fact"], self.facts[0])
                self.assertEqual(packet["article"], case["article"])
                self.assertFalse(packet["published"])
                self.assertFalse(packet["expert_review"])
                self.assertFalse((root / "wiki").exists())
                self.assertFalse((root / "datasets").exists())
                self.assertEqual(
                    save_reviewed_draft(root, manifest, rid, run, review), out
                )

    def test_wrong_run_and_expert_claim_cannot_open_the_draft_gate(self):
        manifest = {"supported": self.supported}
        run = {"experiment_id": digest(manifest), "checked": self.result, "errors": []}
        with tempfile.TemporaryDirectory() as directory:
            for review in [
                {
                    "run_id": "wrong",
                    "review_kind": "semantic_comparison_non_expert",
                    "expert_review": False,
                },
                {
                    "run_id": digest(run),
                    "review_kind": "semantic_comparison_non_expert",
                    "expert_review": True,
                },
            ]:
                with self.assertRaisesRegex(ValueError, "semantic review required"):
                    save_reviewed_draft(
                        Path(directory), manifest, digest(run), run, review
                    )

    def test_full_article_and_manual_notes_are_not_selected_by_expected_label(self):
        text = "# Synthetic article\n\nAn unrelated paragraph.\n\n<!-- HUMAN NOTES -->\nPreserve this note.\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "wiki").mkdir()
            (root / "wiki/article.md").write_text(text)
            case = {
                "article": {"path": "article.md"},
                "typed_view": {
                    "table_view": {"cells": [], "caption_quote": "Synthetic caption"},
                    "ambiguity_groups": [],
                },
                "known_issues": [],
            }
            wire = make_input(root, case, {"facts": [], "source_conflicts": []}, {})
            self.assertEqual(
                "\n\n".join(p["text"] for p in wire["wiki"]["paragraphs"]), text
            )
            self.assertEqual(wire["facts"], [])


if __name__ == "__main__":
    unittest.main()

"""Strict synthetic protocol tests, not model-quality evidence."""

import copy
import unittest
from baikal.assessment_v2 import resolve, wire_context


class EvidenceProtocol(unittest.TestCase):
    def fixture(self):
        e = {
            "source_id": "source",
            "sha256": "a" * 64,
            "page": 3,
            "quote": "В 2016 выпущено 1029 тыс. рыб навеской 3,0 г.",
            "report_year": 2016,
        }
        supplied = {
            "proposed_topic": "fish",
            "known_issues": [],
            "evidence": [e],
            "existing_article": {"text": "# Рыбы\nИзвестный факт.", "truncated": False},
            "candidate_topics": [{"id": "fish"}],
        }
        out = {
            "title": "Рыбы",
            "selected_topic": "fish",
            "selection_reason": "Тема выпуска.",
            "claims": [
                {
                    "text": "В 2016 выпущено 1029 тыс. рыб.",
                    "novelty": "new_detail",
                    "observation_period": "2016",
                    "uncertainties": [],
                    "evidence_ids": ["S1"],
                }
            ],
            "potential_conflicts": [],
            "unsupported_candidates": [],
            "limitations": ["Выпуск не измеряет выживаемость."],
        }
        return supplied, out

    def test_binding_preserves_exact_source_and_does_not_mutate_input(self):
        supplied, out = self.fixture()
        original = copy.deepcopy(out)
        result = resolve(out, supplied)
        self.assertEqual(result["claims"][0]["evidence"], supplied["evidence"])
        self.assertEqual(out, original)
        self.assertNotIn("evidence_ids", result["claims"][0])

    def test_unknown_id_or_wiki_reference_rejected(self):
        for ids in [["W1"], [], [7], ["S2"]]:
            supplied, out = self.fixture()
            out["claims"][0]["evidence_ids"] = ids
            with self.assertRaisesRegex(ValueError, "evidence ID"):
                resolve(out, supplied)

    def test_model_cannot_supply_its_own_citation(self):
        supplied, out = self.fixture()
        out["claims"][0]["evidence"] = [{"source_id": "wiki", "quote": "not a source"}]
        with self.assertRaisesRegex(ValueError, "evidence_ids only"):
            resolve(out, supplied)

    def test_heading_and_obvious_repeat_cannot_be_new_claim(self):
        for text in ["Рыбы", "Известный факт."]:
            supplied, out = self.fixture()
            out["claims"][0]["text"] = text
            with self.assertRaises(ValueError):
                resolve(out, supplied)

    def test_catalog_title_is_rejected_when_reviewed_body_omits_h1(self):
        supplied, out = self.fixture()
        supplied["existing_article"]["text"] = "Известный факт."
        supplied["candidate_topics"][0]["title"] = "Рыбы"
        out["claims"][0]["text"] = "Рыбы"
        with self.assertRaisesRegex(ValueError, "heading is not a scientific claim"):
            resolve(out, supplied)

    def test_unsupported_number_not_approved_even_with_valid_reference(self):
        supplied, out = self.fixture()
        out["claims"][0]["text"] = "Выпущено 999 тыс. рыб."
        with self.assertRaisesRegex(ValueError, "numbers absent"):
            resolve(out, supplied)

    def test_blank_limitations_or_wrong_target_rejected(self):
        supplied, out = self.fixture()
        out["limitations"] = [""]
        with self.assertRaisesRegex(ValueError, "limitations"):
            resolve(out, supplied)
        supplied, out = self.fixture()
        out["selected_topic"] = "other"
        with self.assertRaisesRegex(ValueError, "topic changed"):
            resolve(out, supplied)

    def test_valid_abstention_is_not_a_new_claim(self):
        supplied, out = self.fixture()
        out["claims"] = []
        out["unsupported_candidates"] = [
            {"text": "Выживаемость выросла", "reason": "Нет данных."}
        ]
        self.assertEqual(resolve(out, supplied)["claims"], [])

    def test_wire_excludes_wiki_citations_but_preserves_source_text(self):
        supplied, _ = self.fixture()
        supplied["existing_article"]["text"] += (
            " [other:p9](../sources/other.md#page-9)"
        )
        wire = wire_context(supplied)
        self.assertNotIn("other:p9", wire["wiki_context"])
        self.assertEqual(
            wire["source_fragments"][0]["text"], supplied["evidence"][0]["quote"]
        )

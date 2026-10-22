"""Synthetic facts only: isolation, immutable citations, gates and bounded calls."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from baikal.source_first import (
    source_input,
    extract_checked,
    select_supported,
    comparison_input,
    compare_checked,
    save_draft,
)
from baikal.updates import digest
from scripts.evaluate_source_first import reserve_attempt, generate, phase


class SourceFirst(unittest.TestCase):
    def test_failed_extraction_blocks_comparison_before_runtime_or_http(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            run = {
                "experiment_id": "fixture",
                "stage": "extract",
                "errors": ["empty limitations"],
                "checked": None,
            }
            (root / "run.json").write_text(json.dumps(run))
            (root / "prior.json").write_text(
                json.dumps(
                    {
                        "experiment_id": "fixture",
                        "stage": "extract",
                        "results": [
                            {
                                "topic": "baikal-sturgeon",
                                "path": "run.json",
                                "run_id": digest(run),
                            }
                        ],
                    }
                )
            )
            (root / "review.json").write_text(
                json.dumps({"experiment_id": "fixture", "cases": {}})
            )
            args = SimpleNamespace(
                experiment="fixture",
                extract=False,
                compare=True,
                extraction=root / "prior.json",
                review=root / "review.json",
            )
            with (
                patch(
                    "scripts.evaluate_source_first.load_experiment",
                    return_value={"model_profile": {}, "cases": [self.case()]},
                ),
                patch("scripts.evaluate_source_first.local_server") as runtime,
            ):
                with self.assertRaisesRegex(ValueError, "Valid immutable extraction"):
                    phase(args, root)
                runtime.assert_not_called()

    def case(self):
        quote = "В 2012 году выпущено 25 тыс. рыб средней массой 2,0 г в реку Тест."
        ref = {
            "source_id": "synthetic",
            "sha256": "a" * 64,
            "page": 7,
            "report_year": 2014,
            "publication_year": None,
            "quote": quote,
        }
        return {
            "topic": "baikal-sturgeon",
            "evidence": [ref],
            "fragments": [{"id": "S1", "report_year": 2014, "text": quote}],
            "wiki_context": "В реку Тест выпускали молодь. Старый факт.",
            "known_issues": ["DO_NOT_SEND_TO_EXTRACTION"],
            "article": {"path": "test.md", "sha256": "b" * 64},
            "preceding_version": {"path": "archive.md"},
        }

    def fact(self):
        return {
            "text": "В 2012 году в реку Тест выпущено 25 тыс. рыб средней массой 2,0 г.",
            "evidence_ids": ["S1"],
            "observation_year": 2012,
            "indicator": "Выпуск молоди",
            "unit": "тыс. рыб",
            "territory": "река Тест",
            "statement_kind": "observed",
            "limitations": ["Выпуск не доказывает выживаемость."],
        }

    def result(self):
        return {
            "facts": [self.fact()],
            "source_conflicts": [],
            "limitations": ["Один фрагмент."],
        }

    def bundle(self):
        return extract_checked(self.result(), self.case())

    def review(self, bundle):
        return {
            "bundle_sha256": digest(bundle),
            "review_kind": "source_comparison_non_expert",
            "expert_review": False,
            "fact_decisions": {
                f["fact_id"]: {
                    "status": "supported",
                    "reason": "Synthetic quote supports release only.",
                    "limitations": ["Not expert review."],
                }
                for f in bundle["facts"]
            },
            "conflict_decisions": {},
        }

    def comparison(self, bundle):
        return {
            "decisions": [
                {
                    "fact_id": bundle["facts"][0]["fact_id"],
                    "classification": "new_detail",
                    "existing_excerpt": None,
                    "reason": "Количество отсутствует.",
                    "limitations": ["Не оценка выживаемости."],
                }
            ],
            "source_conflict_notes": [],
            "limitations": ["Не научная рецензия."],
        }

    def test_source_only_whitelist_does_not_leak_wiki_or_expected_answers(self):
        case = self.case()
        supplied = source_input(case)
        self.assertEqual(set(supplied), {"source_fragments"})
        self.assertNotIn(case["wiki_context"], json.dumps(supplied, ensure_ascii=False))
        self.assertNotIn("DO_NOT_SEND", json.dumps(supplied))
        supplied["source_fragments"][0]["text"] = "changed"
        self.assertNotEqual(supplied["source_fragments"], case["fragments"])

    def test_valid_fact_has_exact_citation_and_content_bound_id(self):
        case = self.case()
        bundle = self.bundle()
        f = bundle["facts"][0]
        self.assertEqual(f["evidence"], case["evidence"])
        self.assertEqual(bundle["rejected"], [])
        changed = self.result()
        changed["facts"][0]["text"] += " Это сообщение о выпуске."
        self.assertNotEqual(
            extract_checked(changed, case)["facts"][0]["fact_id"], f["fact_id"]
        )

    def test_bad_numbers_year_ids_and_free_quotes_rejected_individually(self):
        for edit in [
            lambda f: f.update(text=f["text"].replace("25", "26")),
            lambda f: f.update(
                observation_year=2014
            ),  # report year is not observation.
            lambda f: f.update(observation_year=True),
            lambda f: f.update(evidence_ids=["S99"]),
            lambda f: f.update(evidence_ids=[]),
            lambda f: f.update(quote="a reconstructed quote"),
        ]:
            r = self.result()
            bad = copy.deepcopy(r["facts"][0])
            edit(bad)
            r["facts"].append(bad)
            checked = extract_checked(r, self.case())
            self.assertEqual(len(checked["facts"]), 1)
            self.assertEqual(len(checked["rejected"]), 1)

    def test_duplicates_and_incomplete_limits_not_promoted(self):
        r = self.result()
        r["facts"] *= 2
        self.assertEqual(len(extract_checked(r, self.case())["facts"]), 1)
        r["limitations"] = []
        with self.assertRaises(ValueError):
            extract_checked(r, self.case())

    def test_semantic_review_is_required_bound_and_not_expert_status(self):
        b = self.bundle()
        r = self.review(b)
        self.assertEqual(len(select_supported(b, r)["facts"]), 1)
        for edit in [
            lambda x: x.update(bundle_sha256="wrong"),
            lambda x: x.update(expert_review=True),
            lambda x: x.update(fact_decisions={}),
        ]:
            bad = copy.deepcopy(r)
            edit(bad)
            with self.assertRaises(ValueError):
                select_supported(b, bad)
        r["fact_decisions"][b["facts"][0]["fact_id"]]["status"] = "rejected"
        selected = select_supported(b, r)
        with self.assertRaisesRegex(ValueError, "skipped"):
            comparison_input(self.case(), selected)

    def test_novelty_cannot_rewrite_fact_or_rebind_evidence(self):
        b = self.bundle()
        supplied = comparison_input(self.case(), b)
        result = self.comparison(b)
        before = copy.deepcopy(supplied)
        self.assertEqual(compare_checked(result, supplied), result)
        self.assertEqual(supplied, before)
        for edit in [
            lambda d: d.update(text="Changed fact"),
            lambda d: d.update(evidence_ids=["S2"]),
            lambda d: d.update(fact_id="unknown"),
            lambda d: d.update(
                classification="already_present", existing_excerpt="Not in Wiki"
            ),
        ]:
            bad = copy.deepcopy(result)
            edit(bad["decisions"][0])
            with self.assertRaises(ValueError):
                compare_checked(bad, supplied)
        with self.assertRaises(ValueError):
            compare_checked({**result, "decisions": []}, supplied)

    def test_existing_excerpt_and_exact_repeat_guard(self):
        b = self.bundle()
        s = comparison_input(self.case(), b)
        r = self.comparison(b)
        r["decisions"][0].update(
            classification="already_present", existing_excerpt="Старый факт."
        )
        compare_checked(r, s)
        r["decisions"][0]["existing_excerpt"] = None
        with self.assertRaises(ValueError):
            compare_checked(r, s)
        r = self.comparison(b)
        s["wiki_context"] += " " + b["facts"][0]["text"]
        with self.assertRaisesRegex(ValueError, "Existing text"):
            compare_checked(r, s)

    def test_source_conflicts_must_have_two_bindings_and_all_be_retained(self):
        case = self.case()
        case["evidence"].append(
            {
                **case["evidence"][0],
                "quote": "Таблица сообщает иной результат измерения.",
            }
        )
        r = self.result()
        r["source_conflicts"] = [
            {
                "description": "Текст и таблица дают разные результаты.",
                "evidence_ids": ["S1"],
                "limitations": ["Не исправлять."],
            }
        ]
        self.assertEqual(len(extract_checked(r, case)["rejected"]), 1)
        r["source_conflicts"][0]["evidence_ids"].append("S2")
        b = extract_checked(r, case)
        s = comparison_input(case, b)
        with self.assertRaisesRegex(ValueError, "conflict needs"):
            compare_checked(self.comparison(b), s)

    def test_four_slots_are_exclusive_no_retry_even_after_error(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for topic in ["baikal-sturgeon", "selenga-sediments"]:
                for stage in ["extract", "compare"]:
                    reserve_attempt(root, "fixture", topic, stage)
                    with self.assertRaises(FileExistsError):
                        reserve_attempt(root, "fixture", topic, stage)
            with self.assertRaises(ValueError):
                reserve_attempt(root, "fixture", "third", "extract")
            self.assertEqual(len(list(root.rglob("*.json"))), 4)

    def test_unpublished_draft_copies_original_fact_not_model_rewrite(self):
        b = self.bundle()
        case = self.case()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wiki = root / "wiki"
            wiki.mkdir()
            (wiki / "test.md").write_text("untouched")
            draft = save_draft(root, case, b, self.comparison(b), "run", "review")
            stored = json.loads((root / draft["path"]).read_text())
            self.assertEqual(stored["additions"][0]["fact"], b["facts"][0])
            self.assertFalse(stored["published"])
            self.assertFalse(stored["expert_review"])
            self.assertEqual((wiki / "test.md").read_text(), "untouched")
            self.assertFalse((root / "datasets/observations.json").exists())

    def test_bad_model_response_is_saved_and_consumes_slot_without_retry(self):
        class Opener:
            def open(self, *args, **kwargs):
                return io.BytesIO(
                    b'{"choices":[{"finish_reason":"stop","message":{"content":"not json"}}]}'
                )

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            outcome = generate(
                root,
                "fixture",
                self.case(),
                "extract",
                source_input(self.case()),
                "http://127.0.0.1:1/v1/chat/completions",
                Opener(),
                {},
                lambda x: x,
            )
            run = json.loads((root / outcome["path"]).read_text())
            self.assertTrue(outcome["errors"])
            self.assertIsNotNone(run["raw_response"])
            with self.assertRaises(FileExistsError):
                reserve_attempt(root, "fixture", "baikal-sturgeon", "extract")

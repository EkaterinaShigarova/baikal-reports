"""Synthetic context preflight and identity preservation; no real inference."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from baikal.typed_fact_context import (
    comparison_wire,
    expand_records,
    preflight,
    count_prompt,
    validate_reason_bindings,
)
from scripts.evaluate_typed_facts import call


class NativeOpener:
    def __init__(self, count):
        self.count = count
        self.generation_calls = 0

    def open(self, req, **kwargs):
        if req.full_url.endswith("/apply-template"):
            return io.BytesIO(b'{"prompt":"synthetic"}')
        if req.full_url.endswith("/tokenize"):
            return io.BytesIO(json.dumps({"tokens": [1] * self.count}).encode())
        self.generation_calls += 1
        raise AssertionError("No model generation expected")


class TypedContext(unittest.TestCase):
    def test_shared_citations_roundtrip_and_rejected_review_notes_removed(self):
        ref = {
            "source_id": "synthetic",
            "page": 1,
            "quote": "synthetic evidence",
            "sha256": "a" * 64,
        }
        facts = [
            {"fact_id": f"F{i}", "text": f"Synthetic fact {i}", "evidence": [ref]}
            for i in [1, 2]
        ]
        conflict = {
            "conflict_id": "C1",
            "description": "synthetic conflict",
            "evidence": [ref],
        }
        supported = {"facts": facts, "source_conflicts": [conflict]}
        before = copy.deepcopy(supported)
        case = {
            "evidence": [ref],
            "wiki_context": "synthetic article",
            "known_issues": [],
            "article": {"sha256": "b" * 64},
        }
        review = {
            "fact_decisions": {
                "F1": {"reason": "checked1"},
                "F2": {"reason": "checked2"},
                "REJECTED": {"reason": "must not leak"},
            },
            "conflict_decisions": {"C1": {"reason": "checked"}},
        }
        wire = comparison_wire(case, supported, review)
        self.assertEqual(expand_records(wire, "facts"), facts)
        self.assertEqual(expand_records(wire, "source_conflicts"), [conflict])
        self.assertEqual(len(wire["source_fragments"]), 1)
        self.assertEqual(supported, before)
        self.assertNotIn("REJECTED", json.dumps(wire))
        self.assertEqual(
            set(wire["source_review_notes"]["fact_decisions"]), {"F1", "F2"}
        )

    def test_native_budget_requires_full_output_and_margin(self):
        payload = {"max_tokens": 2400, "messages": []}
        with self.assertRaisesRegex(ValueError, "Context budget exceeded"):
            preflight(
                "http://127.0.0.1:1/v1/chat/completions", NativeOpener(7597), payload
            )
        checked = preflight(
            "http://127.0.0.1:1/v1/chat/completions", NativeOpener(4000), payload
        )
        self.assertEqual(checked["generation_requests"], 0)
        self.assertEqual(checked["prompt_tokens_native"], 4000)

    def test_metadata_diagnostics_cannot_send_documents_to_remote_host(self):
        with self.assertRaisesRegex(ValueError, "loopback-only"):
            count_prompt("https://example.org", NativeOpener(1), {})

    def test_other_facts_value_in_rationale_is_rejected(self):
        supplied = {
            "facts": [
                {
                    "fact_id": "F1",
                    "text": "В 2015 году среднее 36,9.",
                    "observation_year": 2015,
                }
            ],
            "wiki_context": "Наблюдения 2019 года.",
        }
        result = {
            "decisions": [{"fact_id": "F1", "reason": "Среднее 36,9 в 2015 году."}]
        }
        validate_reason_bindings(result, supplied)
        result["decisions"][0]["reason"] = "Среднее 61,7 в 2015 году."
        with self.assertRaisesRegex(ValueError, "not belonging"):
            validate_reason_bindings(result, supplied)

    def test_over_budget_call_never_consumes_generation_slot(self):
        opener = NativeOpener(7597)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(ValueError, "Context budget exceeded"):
                call(
                    root,
                    "fixture",
                    {"topic": "baikal-sturgeon"},
                    "extract",
                    {},
                    {},
                    "http://127.0.0.1:1/v1/chat/completions",
                    opener,
                    {},
                    lambda x: x,
                )
            self.assertEqual(opener.generation_calls, 0)
            self.assertFalse((root / "data").exists())

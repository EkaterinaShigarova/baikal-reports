"""Synthetic approval/transport fixtures. No real model or human review is claimed."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from baikal import editorial, llm
from baikal.assessment import context, validate
from baikal.core import atomic_json, atomic_text
from baikal.scientific_review import body_digest, effective_status
from baikal.updates import save_packet


class EditorialAssessment(unittest.TestCase):
    def fixture(self, root):
        topic = "test-topic"
        name = "syntheses/test.md"
        p = root / "wiki" / name
        p.parent.mkdir(parents=True)
        p.write_text(
            "# Existing knowledge\n\nPreviously checked statement.\n\n<!-- HUMAN NOTES -->\nKeep my manual annotation.\n"
        )
        source = {
            "id": "local-evidence",
            "sha256": "a" * 64,
            "pages": 2,
            "report_year": 2025,
            "publication_year": 2026,
        }
        atomic_json(root / "config/sources.json", {"sources": [source]})
        atomic_json(
            root / "config/encyclopedia.json",
            {
                "sections": [
                    {
                        "subtopics": [
                            {
                                "topics": [
                                    {"id": topic, "title": "Test", "article": name},
                                    {
                                        "id": "other-topic",
                                        "title": "Other",
                                        "article": None,
                                    },
                                ]
                            }
                        ]
                    }
                ]
            },
        )
        ref = {
            "source_id": source["id"],
            "sha256": source["sha256"],
            "page": 2,
            "quote": "Synthetic source detail.",
        }
        atomic_json(
            root / "data/processed" / source["sha256"] / "pages/0002.json",
            {"page": 2, "status": "ok", "text": ref["quote"]},
        )
        sp = root / "wiki/sources/local-evidence.md"
        sp.parent.mkdir(parents=True)
        sp.write_text('# Test source\n<a id="page-2"></a>')
        atomic_json(
            root / "datasets/observations.json", [{"synthetic": "do not change"}]
        )
        atomic_json(
            root / "datasets/scientific_review.json",
            {
                "articles": {
                    name: {
                        "status": "ai_source_checked",
                        "body_sha256": body_digest(p.read_text()),
                        "source_revisions": {source["id"]: source["sha256"]},
                        "claim_ids": ["old"],
                    }
                },
                "issues": [
                    {
                        "id": "known",
                        "title": "Known issue",
                        "status": "unresolved_source",
                        "conclusion": "Preserve uncertainty.",
                        "claim_ids": ["old"],
                    }
                ],
            },
        )
        atomic_json(
            root / "datasets/article_provenance.json",
            {
                name: [
                    {"source_id": source["id"], "sha256": source["sha256"], "page": 1}
                ]
            },
        )
        pid, _ = save_packet(root, "proposals", {"topic": topic, "evidence": [ref]})
        supplied = context(topic, [ref], root)
        result = {
            "title": "Synthetic response",
            "selected_topic": topic,
            "selection_reason": "Fixture only.",
            "claims": [
                {
                    "text": "Synthetic source detail.",
                    "novelty": "new_detail",
                    "observation_period": None,
                    "uncertainties": ["Observation period not established."],
                    "evidence": [ref],
                }
            ],
            "potential_conflicts": [],
            "limitations": ["Synthetic unit test, not model inference."],
        }
        draft = {
            "topic": topic,
            "status": "draft",
            "published": False,
            "assessment": True,
            "proposal_id": pid,
            "context": supplied,
            "result": result,
            "evidence": [ref],
        }
        did, _ = save_packet(root, "llm_drafts", draft)
        return p, ref, pid, did, supplied, result

    def approval(self, root, cid):
        p = root / "approval.json"
        atomic_json(
            p,
            {
                "change_id": cid,
                "decision": "approve",
                "review_kind": "editorial",
                "reviewed_by": "synthetic-editorial-fixture",
                "reviewed_at": "2026-10-22",
                "source_semantics_checked": True,
                "uncertainties_preserved": True,
                "test_only": True,
            },
        )
        return p

    def test_context_excludes_notes_and_separates_three_year_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p, ref, pid, did, supplied, result = self.fixture(Path(d))
            self.assertNotIn("manual annotation", supplied["existing_article"]["text"])
            self.assertIn("Previously checked", supplied["existing_article"]["text"])
            self.assertEqual(supplied["evidence"][0]["report_year"], 2025)
            self.assertEqual(supplied["evidence"][0]["publication_year"], 2026)
            self.assertIsNone(supplied["evidence"][0]["observation_period"])
            self.assertEqual(supplied["known_issues"][0]["id"], "known")
            self.assertEqual(validate(result, supplied), [])
            result["selected_topic"] = "other-topic"
            self.assertTrue(
                any("alternate target" in e for e in validate(result, supplied))
            )

    def test_unsupported_conflict_and_malformed_claim_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            *_, supplied, result = self.fixture(Path(d))
            result["potential_conflicts"] = [
                {
                    "description": "Claimed conflict",
                    "existing_excerpt": "Not actually in Wiki",
                    "already_documented": False,
                    "evidence": result["claims"][0]["evidence"],
                }
            ]
            self.assertTrue(
                any("unsupported existing" in e for e in validate(result, supplied))
            )
            result["claims"] = ["bad shape"]
            self.assertTrue(validate(result, supplied))

    def test_prepare_requires_explicit_approval_and_apply_preserves_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, ref, pid, did, supplied, result = self.fixture(root)
            before = p.read_bytes()
            protected = {
                n: (root / "datasets" / n).read_bytes()
                for n in ["scientific_review.json", "observations.json"]
            }
            c = editorial.prepare(did, [0], root)
            self.assertEqual(p.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "approval"):
                editorial.apply(c["id"], root / "missing.json", root)
            report = editorial.apply(c["id"], self.approval(root, c["id"]), root)
            self.assertFalse(report["expert_review"])
            self.assertIn("Previously checked statement.", p.read_text())
            self.assertEqual(
                p.read_text().split("<!-- HUMAN NOTES -->")[1],
                before.decode().split("<!-- HUMAN NOTES -->")[1],
            )
            ledger = json.loads((root / "datasets/editorial_changes.json").read_text())
            self.assertEqual((root / ledger[0]["before"]["path"]).read_bytes(), before)
            self.assertEqual(
                len(
                    json.loads((root / "datasets/article_provenance.json").read_text())[
                        "syntheses/test.md"
                    ]
                ),
                2,
            )
            for n, b in protected.items():
                self.assertEqual((root / "datasets" / n).read_bytes(), b)
            self.assertEqual(
                effective_status("syntheses/test.md", root), "needs_scientific_review"
            )
            self.assertEqual(
                editorial.apply(c["id"], root / "approval.json", root)["status"],
                "already_applied",
            )

    def test_stale_article_and_source_block_editorial_application(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, ref, pid, did, supplied, result = self.fixture(root)
            c = editorial.prepare(did, [0], root)
            a = self.approval(root, c["id"])
            old = p.read_text()
            p.write_text(old + "New human note.\n")
            with self.assertRaisesRegex(ValueError, "Article changed"):
                editorial.apply(c["id"], a, root)
            p.write_text(old)
            atomic_json(
                root / "config/sources.json",
                {"sources": [{"id": ref["source_id"], "sha256": "b" * 64}]},
            )
            with self.assertRaisesRegex(ValueError, "Stale"):
                editorial.apply(c["id"], a, root)
            self.assertEqual(p.read_text(), old)

    def test_test_approval_is_blocked_for_production_root(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, ref, pid, did, supplied, result = self.fixture(root)
            c = editorial.prepare(did, [0], root)
            a = self.approval(root, c["id"])
            with (
                patch.object(editorial, "ROOT", root),
                self.assertRaisesRegex(ValueError, "Test approval"),
            ):
                editorial.apply(c["id"], a, root)

    def test_partial_write_is_rolled_back_without_losing_originals(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, ref, pid, did, supplied, result = self.fixture(root)
            c = editorial.prepare(did, [0], root)
            a = self.approval(root, c["id"])
            before = {
                p: p.read_bytes(),
                root / "datasets/article_provenance.json": (
                    root / "datasets/article_provenance.json"
                ).read_bytes(),
            }
            failed = False

            def write(path, text):
                nonlocal failed
                if path.name == "article_provenance.json" and not failed:
                    failed = True
                    raise OSError("Injected write failure")
                atomic_text(path, text)

            with (
                patch.object(editorial, "atomic_text", side_effect=write),
                self.assertRaises(OSError),
            ):
                editorial.apply(c["id"], a, root)
            for f, text in before.items():
                self.assertEqual(f.read_bytes(), text)
            self.assertFalse((root / "datasets/editorial_changes.json").exists())

    def test_rejected_transport_response_is_retained_and_proxy_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p, ref, pid, did, supplied, result = self.fixture(root)
            before = p.read_bytes()
            opener = MagicMock()
            opener.open.return_value = io.BytesIO(
                json.dumps({"choices": [{"message": {"content": "not JSON"}}]}).encode()
            )
            with (
                patch.object(llm, "ROOT", root),
                patch.object(
                    llm.urllib.request, "build_opener", return_value=opener
                ) as factory,
                patch.dict(
                    os.environ,
                    {
                        "BAIKAL_LLM_URL": "http://127.0.0.1:1234/v1/chat/completions",
                        "BAIKAL_LLM_MODEL": "synthetic-transport",
                        "BAIKAL_LLM_API_KEY": "synthetic-secret",
                    },
                ),
            ):
                with self.assertRaisesRegex(ValueError, "retained run"):
                    llm.synthesize("test-topic", proposal=pid, assessment=True)
                self.assertEqual(factory.call_args.args[1].proxies, {})
            runs = list((root / "data/wiki_updates/llm_runs").glob("*.json"))
            self.assertEqual(len(runs), 1)
            record = runs[0].read_text()
            self.assertIn("not JSON", record)
            self.assertNotIn("synthetic-secret", record)
            self.assertEqual(json.loads(record)["status"], "rejected")
            self.assertEqual(p.read_bytes(), before)

    def test_multiple_fragments_from_same_page_remain_valid(self):
        evidence = [
            {"source_id": "local-test", "page": 2, "quote": "First exact window."},
            {"source_id": "local-test", "page": 2, "quote": "Second exact window."},
        ]
        c = {
            "claims": [
                {
                    "text": "Fixture.",
                    "evidence": [
                        {"source_id": "local-test", "page": 2, "quote": "First exact"}
                    ],
                }
            ]
        }
        self.assertEqual(llm.validate_claims(c, evidence), [])
        self.assertTrue(llm.validate_claims({"claims": [None]}, evidence))

    def test_runner_checks_checksum_before_starting_binary(self):
        from scripts.evaluate_local_llm import run
        from argparse import Namespace

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "synthetic.gguf"
            f.write_bytes(b"GGUFsynthetic checksum fixture")
            args = Namespace(
                model_file=f,
                model_sha256="0" * 64,
                server_binary=Path(d) / "must-not-run",
            )
            with (
                patch(
                    "scripts.evaluate_local_llm.resources",
                    return_value={
                        "memory_limit_bytes": 16 * 1024**3,
                        "memory_available_bytes": 8 * 1024**3,
                        "disk_free_bytes": 8 * 1024**3,
                    },
                ),
                patch("scripts.evaluate_local_llm.subprocess.run") as start,
            ):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    run(args, {})
                start.assert_not_called()

"""Synthetic shards and frozen-input checks; no downloads or model inference."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from baikal.core import atomic_json
from baikal.local_models import profile_7b, plan_7b, verify_7b
from baikal.llm import synthesize
from scripts import evaluate_local_llm as evaluation
from scripts.download_evaluation_model import download


class SevenBProfile(unittest.TestCase):
    def test_successful_draft_status_does_not_zero_validated_counter(self):
        outcome = {"status": "draft", "published": False, "id": "fixture"}
        record = evaluation.draft_result("case", outcome)
        self.assertEqual(record["status"], "draft_pending_semantic_review")
        self.assertFalse(record["published"])
        self.assertEqual(
            sum(r["status"] == "draft_pending_semantic_review" for r in [record]), 1
        )

    def fixture(self):
        p = copy.deepcopy(profile_7b())
        chunks = [b"GGUFfirst-test-shard", b"GGUFsecond-test-shard"]
        for f, chunk in zip(p["files"], chunks):
            f.update(bytes=len(chunk), sha256=hashlib.sha256(chunk).hexdigest())
        metadata = {
            "id": p["repo"],
            "sha": p["revision"],
            "cardData": {"license": p["license"]},
            "siblings": [
                {
                    "rfilename": f["filename"],
                    "lfs": {"size": f["bytes"], "sha256": f["sha256"]},
                }
                for f in p["files"]
            ],
        }
        res = {
            "memory_limit_bytes": 32 * 1024**3,
            "memory_available_bytes": 30 * 1024**3,
            "disk_free_bytes": 30 * 1024**3,
        }
        return p, chunks, metadata, res

    def test_pinned_license_revision_and_every_lfs_checksum_required(self):
        p, _, meta, res = self.fixture()
        with patch("baikal.local_models.profile_7b", return_value=p):
            self.assertEqual(len(plan_7b(meta, res)["files"]), 2)
            for mutate in [
                lambda m: m.update(sha="wrong"),
                lambda m: m.update(id="other/repo"),
                lambda m: m["cardData"].update(license="unknown"),
                lambda m: m["siblings"][1]["lfs"].update(sha256="0" * 64),
            ]:
                bad = copy.deepcopy(meta)
                mutate(bad)
                with self.assertRaises(ValueError):
                    plan_7b(bad, res)

    def test_ram_disk_and_aggregate_budget_enforced(self):
        p, _, meta, res = self.fixture()
        with patch("baikal.local_models.profile_7b", return_value=p):
            for key in res:
                with self.assertRaises(ValueError):
                    plan_7b(meta, {**res, key: 0})
            p["max_total_bytes"] = 1
            with self.assertRaisesRegex(ValueError, "budget"):
                plan_7b(meta, res)

    def test_second_shard_tampering_or_absence_blocks_runtime(self):
        p, chunks, _, res = self.fixture()
        with (
            tempfile.TemporaryDirectory() as d,
            patch("baikal.local_models.profile_7b", return_value=p),
        ):
            files = [Path(d) / f["filename"] for f in p["files"]]
            for path, data in zip(files, chunks):
                path.write_bytes(data)
            self.assertEqual(len(verify_7b(files[0], res)["files"]), 2)
            files[1].write_bytes(b"GGUFcorrupted")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                verify_7b(files[0], res)
            files[1].unlink()
            with self.assertRaises(OSError):
                verify_7b(files[0], res)

    def test_legacy_two_gib_limit_and_filename_safety_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            for info in [
                {"filename": "test.gguf", "bytes": 3 * 1024**3},
                {"filename": "../outside.gguf", "bytes": 1},
            ]:
                with self.assertRaises(ValueError):
                    download(info, Path(d))
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_request_timeout_has_fixed_upper_bound(self):
        with self.assertRaisesRegex(ValueError, "bounded"):
            synthesize("unused", request_timeout=601)

    def frozen_fixture(self, root):
        cases = []
        old = []
        for topic in ["one", "two"]:
            packet = {
                "system_prompt": "synthetic instruction",
                "input": {"synthetic": topic},
            }
            atomic_json(root / f"new-{topic}.json", packet)
            record = {
                "request": {
                    "messages": [
                        {"role": "system", "content": packet["system_prompt"]},
                        {
                            "role": "user",
                            "content": json.dumps(packet["input"], ensure_ascii=False),
                        },
                    ],
                    "temperature": 0,
                    "max_tokens": 1800,
                }
            }
            runpath = root / f"old-{topic}.json"
            atomic_json(runpath, record)
            old.append(
                {
                    "topic": topic,
                    "run_record_path": runpath.name,
                    "run_record_sha256": hashlib.sha256(
                        runpath.read_bytes()
                    ).hexdigest(),
                    "article_sha256": "a" * 64,
                }
            )
            cases.append(
                {
                    "topic": topic,
                    "path": f"new-{topic}.json",
                    "article_sha256": "a" * 64,
                }
            )
        bp = root / "baseline.json"
        atomic_json(bp, {"v2": {"cases": old}})
        return {"assessment_protocol": "v2", "cases": cases}, bp

    def test_changed_prompt_evidence_article_or_case_set_cannot_enter_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prepared, bp = self.frozen_fixture(root)
            with patch.object(evaluation, "ROOT", root):
                self.assertTrue(
                    evaluation.frozen_v2_inputs(prepared, bp)[
                        "model_messages_identical"
                    ]
                )
                changed = copy.deepcopy(prepared)
                changed["cases"][0]["article_sha256"] = "b" * 64
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    evaluation.frozen_v2_inputs(changed, bp)
                changed = copy.deepcopy(prepared)
                changed["cases"][0]["topic"] = "unknown"
                with self.assertRaises(ValueError):
                    evaluation.frozen_v2_inputs(changed, bp)
                atomic_json(
                    root / "new-one.json", {"system_prompt": "changed", "input": {}}
                )
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    evaluation.frozen_v2_inputs(prepared, bp)

    def test_baseline_run_hash_cannot_be_silently_refreshed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            prepared, bp = self.frozen_fixture(root)
            (root / "old-one.json").write_text("{}")
            with (
                patch.object(evaluation, "ROOT", root),
                self.assertRaisesRegex(ValueError, "integrity"),
            ):
                evaluation.frozen_v2_inputs(prepared, bp)

"""Isolated synthetic decisions, never a real model or human approval."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from baikal.core import ROOT, read_json, sha256
from baikal.claim_matching import comparison_input, BASES
from baikal.claim_candidates import save_candidates
from baikal.updates import digest


class Candidates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = read_json(ROOT / "datasets/selenga_claim_registry.json")
        self.benchmark = read_json(ROOT / "datasets/claim_comparison_benchmark.json")
        paths = [
            "datasets/selenga_claim_registry.json",
            "datasets/claim_comparison_benchmark.json",
            "datasets/novelty_evaluation.json",
            "datasets/observations.csv",
            self.registry["article"]["path"],
        ]
        for p in paths:
            (self.root / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / p, self.root / p)
        cases = [
            c for c in self.benchmark["examples"] if c["case_id"] in ["A2", "N1", "N2"]
        ]
        self.manifest = {
            "allowed_cases": ["A2", "N1", "N2"],
            "pins": {p: sha256(self.root / p) for p in paths[:3]},
            "jobs": [
                {"case_id": c["case_id"], "input": comparison_input(c, self.registry)}
                for c in cases
            ],
        }
        eid = digest(self.manifest)
        self.runs = {}
        for job in self.manifest["jobs"]:
            cid = job["case_id"]
            supplied = job["input"]
            label = "already_present" if cid == "A2" else "new_detail"
            parsed = {
                "fact_id": supplied["fact"]["fact_id"],
                "classification": label,
                "basis": BASES[label],
                "supporting_claim_ids": [supplied["claims"][0]["claim_id"]]
                if cid == "A2"
                else [],
                "preserved_conflict_ids": [
                    k["id"] for k in supplied["known_conflicts"]
                ],
            }
            self.runs[cid] = {
                "experiment_id": eid,
                "case_id": cid,
                "parsed": parsed,
                "checked": copy.deepcopy(parsed),
                "errors": [],
                "request": {
                    "messages": [
                        {
                            "role": "system",
                            "content": "Synthetic transport fixture; no inference.",
                        },
                        {
                            "role": "user",
                            "content": json.dumps(supplied, ensure_ascii=False),
                        },
                    ]
                },
            }
        self.review = {
            "experiment_id": eid,
            "article_sha256": self.registry["article"]["sha256"],
            "review_kind": "semantic_comparison_non_expert",
            "expert_review": False,
            "test_only": True,
            "cases": {
                cid: {
                    "run_id": digest(r),
                    "status": "supported",
                    "reason": "Synthetic isolated review, not expert or model evidence.",
                }
                for cid, r in self.runs.items()
            },
        }

    def save(self, review=None):
        with (
            patch("baikal.claim_candidates.validate_registry"),
            patch("baikal.claim_candidates.validate_benchmark"),
        ):
            return save_candidates(
                self.root,
                self.manifest,
                self.runs,
                self.review if review is None else review,
            )

    def test_candidates_preserve_original_facts_article_notes_and_observations(self):
        article = self.root / self.registry["article"]["path"]
        before = article.read_bytes()
        obs = (self.root / "datasets/observations.csv").read_bytes()
        out = self.save()
        self.assertEqual(out["candidates"], 2)
        packet = read_json(self.root / out["path"])
        self.assertFalse(packet["published"])
        self.assertFalse(packet["expert_review"])
        for row in packet["candidates"]:
            original = row["original_fact"]
            case = next(
                c for c in self.benchmark["examples"] if c["case_id"] == row["case_id"]
            )
            self.assertEqual(digest(original), case["fact"]["original_fact_sha256"])
        self.assertEqual(
            (self.root / packet["previous_complete_article"]["path"]).read_bytes(),
            before,
        )
        self.assertEqual(article.read_bytes(), before)
        self.assertEqual((self.root / "datasets/observations.csv").read_bytes(), obs)
        self.assertEqual(self.save(), out)

    def test_no_candidates_without_separate_semantic_review(self):
        with self.assertRaisesRegex(ValueError, "semantic review required"):
            self.save({})
        bad = copy.deepcopy(self.review)
        bad["cases"]["N1"]["run_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.save(bad)

    def test_rejected_response_does_not_become_a_candidate(self):
        review = copy.deepcopy(self.review)
        review["cases"]["N1"]["status"] = "rejected"
        self.assertEqual(self.save(review)["candidates"], 1)

    def test_synthetic_decision_cannot_be_used_in_real_repository(self):
        with self.assertRaisesRegex(ValueError, "Synthetic review forbidden"):
            save_candidates(ROOT, self.manifest, self.runs, self.review)


if __name__ == "__main__":
    unittest.main()

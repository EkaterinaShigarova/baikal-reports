"""Published evidence checks remain useful without private PDFs/model responses."""

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from baikal.core import ROOT, atomic_json, read_json
from baikal.updates import digest
from scripts.check_coverage_report import verify


class CoverageReport(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.report = read_json(ROOT / "datasets/coverage_evaluation.json")
        paths = set(self.report["pins"]) | {
            "config/sources.json",
            "datasets/selenga_claim_registry.json",
            "wiki/syntheses/selenga-sediments.md",
            "datasets/selenga_coverage_audit.json",
            "datasets/coverage_evaluation.json",
        }
        for name in paths:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def save(self, report):
        atomic_json(self.root / "datasets/coverage_evaluation.json", report)

    def test_public_report_without_raw_data_does_not_claim_local_verification(self):
        result = verify(self.root)
        self.assertEqual((result["correct"], result["cards"]), (12, 27))
        self.assertFalse(result["local_required"])
        self.assertFalse(result["semantic_completeness_established"])
        with self.assertRaises(OSError):
            verify(self.root, require_local=True)

    def test_correct_enum_for_another_fact_is_not_evidence(self):
        report = copy.deepcopy(self.report)
        report["model"]["parsed"]["fact_id"] = "OTHER_FACT"
        self.save(report)
        with self.assertRaisesRegex(ValueError, "another fact"):
            verify(self.root)

    def test_omitting_unregistered_sentences_cannot_prove_global_absence(self):
        report = copy.deepcopy(self.report)
        report["review"]["absence_reviews"][0]["certificate"]["reviewed_unit_ids"].remove("U23")
        report["review_packet"]["id"] = digest(report["review"])
        self.save(report)
        with self.assertRaisesRegex(ValueError, "absence improperly inferred"):
            verify(self.root)


if __name__ == "__main__":
    unittest.main()

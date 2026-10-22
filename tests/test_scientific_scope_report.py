"""Do not accept borrowed semantic decisions or invented editorial approvals."""
import copy
import unittest
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch
from baikal.core import ROOT,read_json
from scripts.check_scientific_scope import verify


class ScientificScopeReport(unittest.TestCase):
    def test_public_copy_without_private_pages_does_not_claim_actual_inference_check(self):
        report=read_json(ROOT/"datasets/cross_topic_evaluation.json")
        paths=set(report["pins"]) | {
            "config/sources.json", "datasets/cross_topic_evaluation.json",
            "datasets/selenga_scope_review.json", "datasets/selenga_editorial_packet.json",
            "datasets/research_topic_plans.json", "datasets/selenga_claim_registry_v2.json",
            "datasets/coverage_evaluation.json", "datasets/selenga_comparison_cases_v2.json",
            "datasets/observations.json",
            "wiki/syntheses/selenga-sediments.md", "wiki/syntheses/baikal-seal.md",
            "wiki/syntheses/water-quality-evidence.md",
        }
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in paths:
                target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/name,target)
            result=verify(root)
            self.assertEqual(result["checked_local_excerpts"],0)
            self.assertFalse(result["actual_local_run_checked"])
            with self.assertRaises(FileNotFoundError):verify(root,True)

    def test_current_report_is_recomputed_not_only_parsed(self):
        result=verify()
        self.assertEqual((result["claims"],result["new_examples"],result["correct"]),(27,12,12))
        self.assertFalse(result["expert_review"])

    def test_wrong_semantic_id_and_partial_absence_review_rejected(self):
        filename=ROOT/"datasets/cross_topic_evaluation.json"
        original=read_json(filename)
        for mutation in ["semantic_id","partial_absence","approval"]:
            report=copy.deepcopy(original)
            if mutation=="semantic_id":report["model"]["parsed"]["target_id"]="UNRELATED"
            elif mutation=="partial_absence":report["absence_review"]["reviewed_block_ids"].pop()
            else:report["candidate"]["editorial_decision"]={"approved":True}
            def read(path):return report if path==filename else read_json(path)
            with self.subTest(mutation=mutation),patch("scripts.check_scientific_scope.read_json",side_effect=read):
                with self.assertRaises(ValueError):verify()


if __name__=="__main__":unittest.main()

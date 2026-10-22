"""Draft evidence, approval boundaries and isolated cross-topic workflow checks."""
import shutil
import tempfile
import unittest
from pathlib import Path
from baikal.core import ROOT,read_json,atomic_json,sha256
from baikal.draft_review import validate
from scripts.audit_pipeline_transfer import audit


class DraftReview(unittest.TestCase):
    def copy_public(self,root):
        paths={
            'config/sources.json','datasets/editorial_draft_provenance.json',
            'datasets/editorial_scientific_review.json','datasets/observations.json',
            'datasets/selenga_editorial_packet.json','datasets/cross_topic_evaluation.json',
            'datasets/selenga_claim_registry_v2.json','datasets/cross_topic_claims.json',
            'wiki/syntheses/selenga-sediments.md','wiki/syntheses/baikal-seal.md',
            'wiki/syntheses/water-quality-evidence.md',
            'docs/drafts/ports-and-petroleum.md','docs/drafts/municipal-wastewater.md',
        }
        for name in paths:
            dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,dest)

    def test_current_drafts_remain_unpublished_with_bound_evidence(self):
        r=validate()
        self.assertEqual((r['drafts'],r['editorial_candidates'],r['curated_quantities']),(2,3,21))
        self.assertFalse(r['published']);self.assertFalse(r['expert_review'])

    def test_public_copy_does_not_claim_private_source_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.copy_public(root)
            result=validate(root)
            self.assertEqual((result['checked_local_excerpts'],result['pdf_checksums_checked']),(0,0))
            with self.assertRaises(FileNotFoundError):validate(root,require_local=True)

    def test_false_approval_foreign_number_or_partial_absence_certificate_rejected(self):
        for mutation in ['approval','number','absence']:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);self.copy_public(root)
                mpath=root/'datasets/editorial_draft_provenance.json';manifest=read_json(mpath)
                if mutation=='approval':manifest['drafts'][0]['editorial_decision']={'approved':True}
                elif mutation=='number':manifest['drafts'][0]['quantities'][0]['value']='9999'
                else:
                    path=root/manifest['review']['path'];review=read_json(path)
                    review['candidates'][0]['absence_review']['reviewed_unit_ids'].pop()
                    atomic_json(path,review);manifest['review']['sha256']=sha256(path)
                atomic_json(mpath,manifest)
                with self.assertRaises(ValueError):validate(root)

    def test_synthetic_new_document_uses_generic_pipeline_without_publication(self):
        result=audit()
        self.assertTrue(result['test_only'])
        self.assertEqual((result['synthetic_pages'],result['cached_pages_on_resume'],result['proposals']),(2,1,1))
        self.assertEqual((result['repeated_page_proposals'],result['unmatched_routes_reported']),(0,2))
        self.assertTrue(result['first_checkpoint_preserved'])
        self.assertTrue(result['notes_preserved']);self.assertTrue(result['reviewed_observations_preserved'])
        self.assertFalse(result['published']);self.assertFalse(result['llm_called'])


if __name__=='__main__':unittest.main()

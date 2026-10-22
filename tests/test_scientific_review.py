import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from baikal.core import atomic_json, read_json
from baikal.scientific_review import (
    body_digest,
    reviewed_body,
    effective_status,
    validate,
    sync_review_pages,
    registry,
)


class ScientificReview(unittest.TestCase):
    def fixture(self, root):
        p = root / "wiki/syntheses/test.md"
        p.parent.mkdir(parents=True)
        p.write_text(
            "# Test\n\nCorrected fact.\n\n<!-- HUMAN NOTES -->\nKeep my annotation.\n"
        )
        source = {"id": "local-test", "sha256": "hash", "pages": 2, "report_year": 2020}
        atomic_json(root / "config/sources.json", {"sources": [source]})
        entry = {
            "status": "ai_source_checked",
            "reviewer_type": "ai_agent",
            "body_sha256": body_digest(p.read_text()),
            "source_revisions": {"local-test": "hash"},
            "claim_ids": ["test-01"],
            "coverage": "One checked block",
            "scientific_review_required": True,
            "human_confirmation": None,
        }
        data = {
            "articles": {"syntheses/test.md": entry},
            "status_labels": {
                "ai_source_checked": "Проверен AI-агентом по первоисточнику",
                "needs_scientific_review": "Требуется научная рецензия",
                "specialist_verified": "Проверен специалистом",
            },
            "issues": [],
        }
        atomic_json(root / "datasets/scientific_review.json", data)
        claim = {
            "id": "test-01",
            "article": "syntheses/test.md",
            "article_excerpt": "Corrected fact.",
            "claim_type": "source_statement",
            "decision": "confirmed",
            "observation_period": "2019",
            "territory": "Test region",
            "reason": "Source check",
            "evidence": [
                {
                    "source_id": "local-test",
                    "sha256": "hash",
                    "report_year": 2020,
                    "page": 2,
                    "quote": "Observed fact.",
                }
            ],
        }
        atomic_json(
            root / "datasets/scientific_review_claims.json", {"claims": [claim]}
        )
        atomic_json(
            root / "data/processed/hash/pages/0002.json",
            {"status": "ok", "text": "Observed fact."},
        )
        return p, data

    def test_current_review_has_no_expert_promotion(self):
        report = validate()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [])
        self.assertGreaterEqual(report["articles_reviewed"], 22)
        self.assertGreaterEqual(report["claims_reviewed"], 107)
        self.assertTrue(
            all(
                e["status"] != "specialist_verified"
                and e.get("human_confirmation") is None
                for e in registry()["articles"].values()
            )
        )

    def test_status_blocks_and_notes_do_not_change_reviewed_body(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p, data = self.fixture(root)
            digest = body_digest(p.read_text())
            sync_review_pages(root)
            self.assertEqual(body_digest(p.read_text()), digest)
            self.assertIn("Keep my annotation.", p.read_text())
            self.assertEqual(
                effective_status("syntheses/test.md", root), "ai_source_checked"
            )
            self.assertEqual(sync_review_pages(root), 0)
            p.write_text(p.read_text() + "Additional human note.\n")
            self.assertEqual(
                effective_status("syntheses/test.md", root), "ai_source_checked"
            )

    def test_article_edit_requires_review_again(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p, data = self.fixture(root)
            p.write_text(
                p.read_text().replace("Corrected fact.", "Different assertion.")
            )
            self.assertEqual(
                effective_status("syntheses/test.md", root), "needs_scientific_review"
            )
            self.assertTrue(any("stale" in w for w in validate(root)["warnings"]))

    def test_source_revision_requires_review_again(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p, data = self.fixture(root)
            atomic_json(
                root / "config/sources.json",
                {
                    "sources": [
                        {
                            "id": "local-test",
                            "sha256": "new",
                            "pages": 2,
                            "report_year": 2020,
                        }
                    ]
                },
            )
            self.assertEqual(
                effective_status("syntheses/test.md", root), "needs_scientific_review"
            )

    def test_ai_cannot_claim_specialist_status(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p, data = self.fixture(root)
            data["articles"]["syntheses/test.md"]["status"] = "specialist_verified"
            atomic_json(root / "datasets/scientific_review.json", data)
            self.assertTrue(
                any("human confirmation" in e for e in validate(root)["errors"])
            )

    def test_exact_quote_is_checked_against_its_immutable_revision(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p, data = self.fixture(root)
            ledger = read_json(root / "datasets/scientific_review_claims.json")
            ledger["claims"][0]["evidence"][0]["quote"] = "Invented statement."
            atomic_json(root / "datasets/scientific_review_claims.json", ledger)
            self.assertTrue(
                any(
                    "quote absent" in e
                    for e in validate(root, require_local=True)["errors"]
                )
            )

    def test_rebuild_keeps_corrected_text_and_human_notes(self):
        from baikal import wiki

        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            p, data = self.fixture(root)
            with (
                patch.object(wiki, "ROOT", root),
                patch.object(wiki, "WIKI", root / "wiki"),
                patch.object(wiki, "load_sources", return_value={"sources": []}),
            ):
                wiki.build()
                first = p.read_text()
                wiki.build()
            self.assertEqual(p.read_text(), first)
            self.assertIn("Corrected fact.", reviewed_body(first))
            self.assertIn("Keep my annotation.", first)

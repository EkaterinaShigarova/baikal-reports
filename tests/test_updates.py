"""Synthetic workflow contracts; these tests do not run a language model."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from baikal import acquire
from baikal.core import atomic_json
from baikal.updates import propose, load_proposal, archive_article, substantive


class UpdateProposals(unittest.TestCase):
    def test_chemical_content_is_not_mistaken_for_table_of_contents(self):
        self.assertTrue(substantive("Содержание кислорода в воде. " * 25))
        self.assertFalse(substantive("СОДЕРЖАНИЕ\n" + "Страница .... 20\n" * 25))

    def fixture(self, root, article=True):
        topic = {
            "id": "seal",
            "title": "Seal",
            "article": "syntheses/seal.md" if article else None,
        }
        atomic_json(
            root / "config/encyclopedia.json",
            {"sections": [{"subtopics": [{"topics": [topic]}]}]},
        )
        atomic_json(root / "config/update_rules.json", {"patterns": {"seal": "нерп"}})
        source = {
            "id": "local-one",
            "sha256": "a" * 64,
            "pages": 3,
            "report_year": 2020,
        }
        atomic_json(root / "config/sources.json", {"sources": [source]})
        text = "Наблюдения за нерпой в южной части озера. " * 20
        atomic_json(
            root / "data/processed" / source["sha256"] / "pages/0001.json",
            {"status": "ok", "page": 1, "text": text},
        )
        if article:
            path = root / "wiki/syntheses/seal.md"
            path.parent.mkdir(parents=True)
            path.write_text(
                "# Checked version\n\nPreserved correction.\n\n<!-- HUMAN NOTES -->\nNonempty human annotation.\n"
            )
            atomic_json(
                root / "datasets/scientific_review.json",
                {
                    "articles": {
                        "syntheses/seal.md": {
                            "claim_ids": ["c1"],
                            "source_revisions": {"local-one": "a" * 64},
                        }
                    },
                    "issues": [{"id": "methods", "claim_ids": ["c1"]}],
                },
            )
        atomic_json(
            root / "datasets/observations.json", [{"status": "unchanged fixture"}]
        )
        return source

    def test_proposals_preserve_published_text_notes_and_observations(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.fixture(root)
            before = (root / "wiki/syntheses/seal.md").read_bytes()
            observations = (root / "datasets/observations.json").read_bytes()
            report = propose("local-one", pages=[1], root=root)
            self.assertFalse(report["published"])
            self.assertFalse(report["llm_called"])
            packet = load_proposal(root, report["proposals"][0]["id"], "seal")
            self.assertEqual(packet["known_issue_ids"], ["methods"])
            self.assertEqual(packet["status"], "pending_semantic_review")
            self.assertIsNone(packet["evidence"][0]["observation_period"])
            self.assertEqual(
                (root / packet["preceding_versions"][0]["path"]).read_bytes(), before
            )
            self.assertEqual((root / "wiki/syntheses/seal.md").read_bytes(), before)
            self.assertEqual(
                (root / "datasets/observations.json").read_bytes(), observations
            )
            events = (root / "data/wiki_updates/events.jsonl").read_bytes()
            repeat = propose("local-one", pages=[1], root=root)
            self.assertEqual(repeat["unchanged_pages"], 1)
            self.assertEqual(repeat["proposals"], [])
            self.assertEqual(
                (root / "data/wiki_updates/events.jsonl").read_bytes(), events
            )

    def test_new_topic_candidate_needs_distinct_documents_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = self.fixture(root, article=False)
            first = propose(s["id"], pages=[1], root=root)
            old = json.loads((root / first["proposals"][0]["path"]).read_text())
            self.assertFalse(old["new_article_minimum_packet_met"])
            other = {**s, "id": "local-two", "sha256": "b" * 64, "report_year": 2021}
            atomic_json(root / "config/sources.json", {"sources": [s, other]})
            atomic_json(
                root / "data/processed" / other["sha256"] / "pages/0001.json",
                {"status": "ok", "page": 1, "text": "Учёт нерпы на севере. " * 30},
            )
            second = propose(other["id"], pages=[1], root=root)
            packet = json.loads((root / second["proposals"][0]["path"]).read_text())
            self.assertEqual(packet["kind"], "new_article_candidate")
            self.assertEqual(packet["distinct_documents"], 2)
            self.assertTrue(packet["new_article_minimum_packet_met"])
            self.assertFalse(packet["published"])
            self.assertFalse((root / "wiki").exists())
            self.assertEqual(
                json.loads((root / first["proposals"][0]["path"]).read_text()), old
            )

    def test_source_revision_keeps_previous_packets_and_discards_stale_input(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = self.fixture(root)
            first = propose(s["id"], pages=[1], root=root)
            old = (root / first["proposals"][0]["path"]).read_bytes()
            new = {**s, "sha256": "b" * 64}
            atomic_json(root / "config/sources.json", {"sources": [new]})
            atomic_json(
                root / "data/processed" / new["sha256"] / "pages/0001.json",
                {
                    "status": "ok",
                    "page": 1,
                    "text": "Изменённое описание учёта нерпы. " * 30,
                },
            )
            second = propose(new["id"], pages=[1], root=root)
            packet = load_proposal(root, second["proposals"][0]["id"], "seal")
            self.assertTrue(packet["source_revision_changed"])
            self.assertEqual({r["sha256"] for r in packet["evidence"]}, {"b" * 64})
            with self.assertRaisesRegex(ValueError, "Stale"):
                load_proposal(root, first["proposals"][0]["id"], "seal")
            self.assertEqual((root / first["proposals"][0]["path"]).read_bytes(), old)

    def test_two_mirrors_of_same_pdf_are_not_two_documents(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = self.fixture(root, article=False)
            propose(s["id"], pages=[1], root=root)
            mirror = {**s, "id": "local-mirror"}
            atomic_json(root / "config/sources.json", {"sources": [s, mirror]})
            report = propose(mirror["id"], pages=[1], root=root)
            packet = load_proposal(root, report["proposals"][0]["id"], "seal")
            self.assertEqual(packet["distinct_documents"], 1)
            self.assertFalse(packet["new_article_minimum_packet_met"])

    def test_contents_and_failed_pages_are_not_silent_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = self.fixture(root)
            directory = root / "data/processed" / s["sha256"] / "pages"
            atomic_json(
                directory / "0002.json",
                {
                    "status": "ok",
                    "page": 2,
                    "text": "ОГЛАВЛЕНИЕ\n" + "нерпа .... 44\n" * 40,
                },
            )
            atomic_json(
                directory / "0003.json", {"status": "error", "page": 3, "text": ""}
            )
            r = propose(s["id"], pages=[2, 3], root=root)
            self.assertEqual(r["skipped_noncontent_pages"], 1)
            self.assertFalse(r["proposals"])
            self.assertTrue(any("failed" in x for x in r["notices"]))
            atomic_json(
                directory / "0003.json",
                {
                    "status": "ok",
                    "page": 3,
                    "text": "Повторно доступный текст о нерпе. " * 30,
                },
            )
            self.assertTrue(propose(s["id"], pages=[3], root=root)["proposals"])

    def test_limits_unrouted_text_and_national_scope(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = self.fixture(root)
            s["wiki_page_ranges"] = [[2, 3]]
            atomic_json(root / "config/sources.json", {"sources": [s]})
            directory = root / "data/processed" / s["sha256"] / "pages"
            atomic_json(
                directory / "0002.json",
                {
                    "status": "ok",
                    "page": 2,
                    "text": "Пока нет правила для нового объекта. " * 20,
                },
            )
            r = propose(s["id"], pages=[1, 2], limit=1, root=root)
            self.assertEqual(r["inspected_changed_pages"], 1)
            self.assertFalse(r["proposals"])
            r = propose(s["id"], pages=[1, 2], limit=1, root=root)
            self.assertTrue(any("manual topic discovery" in x for x in r["notices"]))
            with self.assertRaises(ValueError):
                propose(s["id"], pages=[4], root=root)
            s["wiki_page_ranges"] = [[1, 3]]
            atomic_json(root / "config/sources.json", {"sources": [s]})
            self.assertTrue(propose(s["id"], pages=[1], root=root)["proposals"])

    def test_tampered_quotes_and_archived_versions_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            s = self.fixture(root)
            r = propose(s["id"], pages=[1], root=root)
            pid = r["proposals"][0]["id"]
            atomic_json(
                root / "data/processed" / s["sha256"] / "pages/0001.json",
                {"status": "ok", "page": 1, "text": "Unrelated replacement."},
            )
            with self.assertRaisesRegex(ValueError, "quote"):
                load_proposal(root, pid, "seal")
            a = archive_article(root, "syntheses/seal.md")
            (root / a["path"]).write_text("Corruption")
            with self.assertRaisesRegex(ValueError, "integrity"):
                archive_article(root, "syntheses/seal.md")

    def test_unchanged_pdf_download_reuses_immutable_file_without_network(self):
        import hashlib

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            content = b"%PDF-test-cache-only"
            sha = hashlib.sha256(content).hexdigest()
            raw = root / "raw" / f"{sha}.pdf"
            raw.parent.mkdir()
            raw.write_bytes(content)
            with (
                patch.object(acquire, "DATA", root),
                patch.object(
                    acquire, "open_url", side_effect=AssertionError("Network forbidden")
                ),
            ):
                self.assertEqual(
                    acquire.download({"id": "local-one", "sha256": sha}), raw
                )

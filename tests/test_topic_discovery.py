import copy
import tempfile
import unittest
from pathlib import Path

from baikal.core import ROOT, read_json, atomic_json, sha256
from baikal.topic_discovery import check_bundle, context, gate, retain, scan


class TopicDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.bundle = read_json(ROOT / "datasets/topic_discovery_tasks.json")["tasks"][0]
        self.wire = context(self.bundle)
        self.reply = {"decision": "new_article", "target_id": None,
                      "title": "Портовые наблюдения и границы сравнения",
                      "reason": "Самостоятельный объект наблюдений требует отдельного обзора.",
                      "relations": [{"article_id": self.wire["articles"][0]["id"], "relation": "distinct_scope"}],
                      "sections": [{"kind": "object", "evidence_ids": ["P16"]},
                                   {"kind": "observations", "evidence_ids": ["P21", "P25"]}],
                      "limitation_ids": self.bundle["required_limitation_ids"]}

    def test_exact_public_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root / "config/sources.json", read_json(ROOT / "config/sources.json"))
            for e in self.bundle["evidence"]:
                p = root / "wiki/sources" / f"{e['source_id']}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a") as stream:
                    stream.write(f'<a id="page-{e["page"]}"></a>\n')
            self.assertEqual(check_bundle(self.bundle, root), 0)
            with self.assertRaises(ValueError):
                check_bundle(self.bundle, root, require_local=True)
            changed = copy.deepcopy(self.bundle)
            changed["evidence"][0]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                check_bundle(changed, root)

    def test_catalog_queue_not_publication_and_context_not_absence(self):
        rows = {x["id"]: x for x in self.wire["catalog"]}
        self.assertFalse(rows["ports-oil"]["published"])
        self.assertTrue(rows["baikal-seal"]["published"])
        for a in self.wire["articles"]:
            self.assertEqual(a["coverage"], "partial_not_absence_proof")
            text = (ROOT / "wiki" / a["path"]).read_text()
            self.assertTrue(all(b["quote"] in text for b in a["blocks"]))

    def test_gate_is_not_semantic_approval(self):
        checked = gate(self.reply, self.bundle, self.wire)
        self.assertTrue(checked["program_gate_passed"])
        self.assertTrue(checked["semantic_review_required"])
        self.assertIsNone(checked["editorial_decision"])
        self.assertEqual(checked["whole_wiki_novelty"], "unverified")

    def test_unknown_source_or_borrowed_number_rejected(self):
        changed = copy.deepcopy(self.reply)
        changed["sections"][0]["evidence_ids"] = ["MADE_UP"]
        with self.assertRaises(ValueError):
            gate(changed, self.bundle, self.wire)
        changed = copy.deepcopy(self.reply)
        changed["reason"] += " Рост в 10 раз."
        self.assertFalse(gate(changed, self.bundle, self.wire)["program_gate_passed"])

    def test_missing_conflict_or_duplicate_blocks_new_article(self):
        changed = copy.deepcopy(self.reply)
        changed["limitation_ids"] = ["L1"]
        self.assertFalse(gate(changed, self.bundle, self.wire)["program_gate_passed"])
        changed = copy.deepcopy(self.reply)
        changed["relations"][0]["relation"] = "duplicate"
        self.assertFalse(gate(changed, self.bundle, self.wire)["program_gate_passed"])

    def test_one_pdf_is_insufficient_for_new_article(self):
        changed = copy.deepcopy(self.reply)
        for s in changed["sections"]:
            s["evidence_ids"] = ["P16"]
        self.assertFalse(gate(changed, self.bundle, self.wire)["program_gate_passed"])
        self.assertIsNone(retain(changed, self.bundle, self.wire, "not_a_run")["draft"])

    def test_extended_target_must_match_relation(self):
        changed = copy.deepcopy(self.reply)
        changed["decision"] = "extend_existing"
        changed["target_id"] = self.wire["articles"][0]["id"]
        self.assertFalse(gate(changed, self.bundle, self.wire)["program_gate_passed"])
        changed["relations"][0]["relation"] = "overlaps"
        self.assertTrue(gate(changed, self.bundle, self.wire)["program_gate_passed"])

    def test_unknown_topic_heading_scan_without_routing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            for i in range(2):
                sha = str(i) * 64
                sources.append({"id": f"unseen-{i}", "sha256": sha})
                atomic_json(root / "data/processed" / sha / "pages/0001.json",
                            {"text": "2.3. Новая тема лесных обследований\n" + "Сведения контрольного синтетического примера. " * 10})
            atomic_json(root / "config/sources.json", {"sources": sources})
            result = scan(root)
            self.assertEqual(result["pages_read"], 2)
            self.assertEqual(result["total_leads"], 1)
            self.assertEqual(result["leads"][0]["novelty"], "unknown")
            self.assertFalse((root / "wiki").exists())

    def test_draft_keeps_exact_quotes_and_archives_notes_without_publication(self):
        # Isolated fixture, not another model response or editorial approval.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wire = copy.deepcopy(self.wire)
            atomic_json(root / "config/sources.json", read_json(ROOT / "config/sources.json"))
            for e in self.bundle["evidence"]:
                atomic_json(root / "data/processed" / e["sha256"] / "pages" / f"{e['page']:04d}.json",
                            {"text": e["quote"]})
                p = root / "wiki/sources" / f"{e['source_id']}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a") as stream:
                    stream.write(f'<a id="page-{e["page"]}"></a>\n')
            originals = {}
            for a in wire["articles"]:
                p = root / "wiki" / a["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes((ROOT / "wiki" / a["path"]).read_bytes() + b"\nFixture human note.\n")
                a["sha256"] = sha256(p)
                originals[a["path"]] = p.read_bytes()
            result = retain(self.reply, self.bundle, wire, "test_only_fixture", root)
            text = (root / result["draft"]).read_text()
            self.assertIn("неопубликованный", text)
            self.assertIsNone(result["check"]["editorial_decision"])
            for e in self.bundle["evidence"]:
                self.assertIn("\n".join("> " + line for line in e["quote"].splitlines()), text)
            for a in wire["articles"]:
                self.assertEqual((root / "wiki" / a["path"]).read_bytes(), originals[a["path"]])
                self.assertEqual((root / "data/wiki_updates/versions" / f"{a['sha256']}.md").read_bytes(), originals[a["path"]])
            repeated = retain(self.reply, self.bundle, wire, "test_only_fixture", root)
            self.assertEqual(repeated["packet_id"], result["packet_id"])
            self.assertFalse((root / "datasets/observations.csv").exists())


if __name__ == "__main__":
    unittest.main()

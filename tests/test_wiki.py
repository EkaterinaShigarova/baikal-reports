import tempfile, unittest
from pathlib import Path
from baikal.wiki import write_page, MARK
from baikal.llm import validate_claims


class WikiContracts(unittest.TestCase):
    def test_notes_survive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.md"
            p.write_text("# old\n" + MARK + "\nMy verified note\n")
            self.assertTrue(write_page(p, "# new"))
            self.assertIn("My verified note", p.read_text())
            self.assertFalse(write_page(p, "# new"))

    def test_llm_citations(self):
        evidence = [{"source_id": "x", "page": 3, "quote": "value 123"}]
        good = {
            "claims": [
                {
                    "text": "claim",
                    "evidence": [{"source_id": "x", "page": 3, "quote": "123"}],
                }
            ]
        }
        self.assertEqual(validate_claims(good, evidence), [])
        good["claims"][0]["evidence"][0]["page"] = 4
        self.assertTrue(validate_claims(good, evidence))


class WikiWithoutRaw(unittest.TestCase):
    def test_compiled_wiki_survives_clean_checkout(self):
        from unittest.mock import patch
        from baikal import wiki
        from baikal.core import atomic_json

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            w = root / "wiki"
            (w / "sources").mkdir(parents=True)
            (w / "datasets").mkdir()
            (w / "sources/drive-test.md").write_text(
                "# Existing evidence\n\n## Ограничения\n"
            )
            (root / "datasets").mkdir()
            atomic_json(
                root / "datasets/wiki_evidence.json",
                {
                    "air": [
                        {
                            "source_id": "drive-test",
                            "page": 1,
                            "year": 2018,
                            "quote": "Выбросы в атмосферу",
                        }
                    ]
                },
            )
            with (
                patch.object(wiki, "ROOT", root),
                patch.object(wiki, "WIKI", w),
                patch.object(wiki, "load_sources", return_value={"sources": []}),
            ):
                wiki.build()
            self.assertIn("sources/drive-test.md", (w / "index.md").read_text())
            self.assertIn("Выбросы", (w / "topics/air.md").read_text())

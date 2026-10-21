import tempfile, unittest
from pathlib import Path
import pymupdf
from baikal.extract import _init, _extract_one, numeric_candidates


class Extraction(unittest.TestCase):
    def test_page_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            p = base / "a.pdf"
            d = pymupdf.open()
            page = d.new_page()
            page.insert_text(
                (30, 50),
                "Environmental report. Water area 123 km2. This is a sufficiently long source text for native extraction.",
            )
            d.save(p)
            d.close()
            _init(str(p), str(base / "out"))
            r = _extract_one(0)
            self.assertEqual(r["status"], "ok")
            self.assertTrue((base / "out/pages/0001.json").exists())
            self.assertIn("Water area", (base / "out/markdown/0001.md").read_text())

    def test_candidates_keep_context(self):
        r = numeric_candidates("На Байкале концентрация 0,001 мг/л в воде.", 7)
        self.assertEqual(r[0]["page"], 7)
        self.assertIsNone(r[0]["territory"])
        self.assertEqual(r[0]["status"], "candidate")


class Resume(unittest.TestCase):
    def test_resume_preserves_page_and_completes_missing(self):
        from unittest.mock import patch
        from baikal.extract import process
        from baikal.core import sha256

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            pdf = root / "input.pdf"
            d = pymupdf.open()
            for i in range(2):
                p = d.new_page()
                p.insert_text(
                    (30, 50),
                    f"Page {i + 1}: Baikal environmental monitoring with enough valid native text to avoid OCR fallback.",
                )
            d.save(pdf)
            d.close()
            digest = sha256(pdf)
            pdf.rename(raw / f"{digest}.pdf")
            source = {"id": "local-test", "sha256": digest}
            with patch("baikal.extract.DATA", root), patch("baikal.core.DATA", root):
                a = process(source, workers=1, limit=1)
                first = root / "processed" / digest / "pages/0001.json"
                before = first.stat().st_mtime_ns
                b = process(source, workers=1)
                self.assertEqual(a["extracted_pages"], 1)
                self.assertEqual(a["unrun_pages"], 1)
                self.assertEqual(b["cached_pages"], 1)
                self.assertEqual(b["extracted_pages"], 2)
                self.assertEqual(first.stat().st_mtime_ns, before)

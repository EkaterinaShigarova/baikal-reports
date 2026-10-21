import shutil, unittest
from baikal.core import DATA
import pymupdf
from baikal.extract import ocr_page


class OCR(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("tesseract")
        and (DATA / "tools/tessdata/rus.traineddata").exists(),
        "Tesseract rus model is not installed",
    )
    def test_raster_recognition(self):
        d = pymupdf.open()
        p = d.new_page()
        p.insert_text((50, 100), "BAIKAL 2018 REPORT", fontsize=25)
        png = p.get_pixmap(dpi=150).tobytes("png")
        r = pymupdf.open()
        s = r.new_page()
        s.insert_image(s.rect, stream=png)
        text, meta = ocr_page(s)
        self.assertEqual(meta["status"], "ok")
        self.assertIn("2018", text)
        self.assertGreater(meta["mean_confidence"], 65)
        r.close()
        d.close()

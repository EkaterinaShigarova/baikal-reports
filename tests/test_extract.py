import tempfile,unittest
from pathlib import Path
import pymupdf
from baikal.extract import _init,_extract_one,numeric_candidates
class Extraction(unittest.TestCase):
    def test_page_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);p=base/'a.pdf'
            d=pymupdf.open();page=d.new_page();page.insert_text((30,50),'Environmental report. Water area 123 km2. This is a sufficiently long source text for native extraction.');d.save(p);d.close()
            _init(str(p),str(base/'out'));r=_extract_one(0)
            self.assertEqual(r['status'],'ok');self.assertTrue((base/'out/pages/0001.json').exists())
            self.assertIn('Water area',(base/'out/markdown/0001.md').read_text())
    def test_candidates_keep_context(self):
        r=numeric_candidates('На Байкале концентрация 0,001 мг/л в воде.',7)
        self.assertEqual(r[0]['page'],7);self.assertIsNone(r[0]['territory']);self.assertEqual(r[0]['status'],'candidate')

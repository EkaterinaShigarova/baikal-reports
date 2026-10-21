import unittest
import pymupdf
from baikal.tableqa import assess_tables


class TableAudit(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page()
        for x, text in [(50, "Item"), (180, "March"), (300, "September")]:
            self.page.insert_text((x, 50), text)
        for x, text in [(50, "Oxygen"), (180, "10.5"), (300, "7.8")]:
            self.page.insert_text((x, 80), text)

    def tearDown(self):
        self.doc.close()

    def test_missing_right_column_is_visible(self):
        p = {
            "page": 1,
            "method": "native",
            "tables": [
                {
                    "bbox": [40, 30, 250, 90],
                    "rows": [["Item", "March"], ["Oxygen", "10.5"]],
                }
            ],
        }
        r = assess_tables(self.page, p)["tables"][0]
        self.assertIn("unaccounted_numeric_tokens", r["flags"])
        self.assertIn("7.8", r["missing_numeric_tokens"])
        self.assertIn("text_outside_detected_columns", r["flags"])

    def test_full_table(self):
        p = {
            "page": 1,
            "method": "native",
            "tables": [
                {
                    "bbox": [40, 30, 400, 90],
                    "rows": [["Item", "March", "September"], ["Oxygen", "10.5", "7.8"]],
                }
            ],
        }
        self.assertEqual(assess_tables(self.page, p)["tables"][0]["flags"], [])

import unittest
from baikal.indicators import normalize


class Indicators(unittest.TestCase):
    def test_never_guess_scope_or_collapse_range(self):
        s = {
            "id": "x",
            "sha256": "a" * 64,
            "url": "https://example.org/a",
            "report_year": 2018,
        }
        c = normalize(
            {"raw": "0,1-0,5 мг/л", "quote": "от 0,1-0,5 мг/л", "page": 2}, s, "native"
        )
        self.assertIsNone(c["value"])
        self.assertIsNone(c["year"])
        self.assertIsNone(c["territory"])
        self.assertFalse(c["comparability_approved"])

    def test_explicit_context(self):
        s = {"id": "x", "sha256": "a" * 64, "url": "https://example.org/a"}
        c = normalize(
            {
                "raw": "2,5 мг/л",
                "quote": "В 2018 году в воде озера Байкал 2,5 мг/л",
                "page": 3,
            },
            s,
            "native",
        )
        self.assertEqual(c["value"], "2.5")
        self.assertEqual(c["year"], 2018)
        self.assertEqual(c["territory"], "озеро Байкал")
        self.assertEqual(c["review_status"], "candidate")

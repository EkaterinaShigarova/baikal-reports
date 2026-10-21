import unittest
from baikal.core import ROOT, read_json, validate_observation, comparable


class DatasetContracts(unittest.TestCase):
    def test_reviewed_data_provenance(self):
        rows = read_json(ROOT / "datasets/observations.json", [])
        self.assertGreaterEqual(len(rows), 168)
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        for row in rows:
            self.assertEqual(validate_observation(row), [])
            self.assertIn(row["raw_value"], row["quote"])

    def test_total_emissions_known_values(self):
        rows = read_json(ROOT / "datasets/observations.json", [])
        series = [r for r in rows if r["id"].startswith("emissions-total-")]
        self.assertTrue(comparable(series))
        self.assertEqual(series[-1]["value"], "240.153")
        sulfur = [r for r in rows if r["id"].startswith("non-sulfate")]
        self.assertFalse(comparable(sulfur))

    def test_official_paired_year_table_keeps_scope_units_and_rows(self):
        rows = read_json(ROOT / "datasets/observations.json", [])
        river = [r for r in rows if r["id"].startswith("rosgidromet2025-river-")]
        self.assertEqual(len(river), 88)
        groups = {r["comparability_group"] for r in river}
        self.assertEqual(len(groups), 44)
        for g in groups:
            pair = [r for r in river if r["comparability_group"] == g]
            self.assertEqual({r["year"] for r in pair}, {2024, 2025})
            self.assertTrue(comparable(pair))
        known = {r["id"]: r for r in river}
        self.assertEqual(known["rosgidromet2025-river-1-0-2024"]["value"], "16.0")
        self.assertEqual(known["rosgidromet2025-river-1-0-2025"]["value"], "61.8")
        self.assertEqual(known["rosgidromet2025-river-10-3-2025"]["unit"], "км³")

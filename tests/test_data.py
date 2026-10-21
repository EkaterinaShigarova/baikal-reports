import unittest
from baikal.core import ROOT, read_json, validate_observation, comparable


class DatasetContracts(unittest.TestCase):
    def test_reviewed_data_provenance(self):
        rows = read_json(ROOT / "datasets/observations.json", [])
        self.assertEqual(len(rows), 49)
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

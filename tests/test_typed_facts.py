"""Guard checks on published evidence; no real model calls or observation writes."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from baikal.core import ROOT, read_json
from baikal.typed_facts import (
    extract_schema,
    validate_schema,
    check_fact,
    extract_checked,
    prepare_view,
    validate_view,
)
from scripts.evaluate_typed_facts import call


class TypedFacts(unittest.TestCase):
    def case(self, topic="selenga-sediments"):
        old = next(
            c
            for c in read_json(ROOT / "datasets/source_first_evaluation.json")["cases"]
            if c["topic"] == topic
        )
        refs = [
            {k: v for k, v in r.items() if k != "fragment_id"}
            for r in old["source_fragments"]
        ]
        return {
            "topic": topic,
            "evidence": refs,
            "fragments": [
                {"id": f"S{i + 1}", "text": r["quote"], "report_year": r["report_year"]}
                for i, r in enumerate(refs)
            ],
            "article": {"path": "fixture.md", "sha256": "0" * 64},
        }

    def view(self):
        return read_json(ROOT / "datasets/typed_evidence_2015.json")

    def table_fact(self):
        return {
            "text": "В таблице максимум БП за 2015 год составляет 7,9 нг/г с.о.",
            "primary_evidence_id": "S2",
            "cell_id": "T2015_C1",
            "source_role": "table",
            "statistic": "range_max",
            "observation_year": 2015,
            "year_basis_id": "Y_ROW_2015",
            "indicator": "БП",
            "value": "7,9",
            "unit": "нг/г с.о.",
            "territory": "протока Усть-Харауз",
            "limitations": [
                "В прозе указан иной максимум; исходное расхождение не разрешено."
            ],
            "ambiguity_ids": ["ORDER_2015"],
        }

    def test_correct_cell_binds_indicator_unit_territory_time_and_exact_sources(self):
        f = check_fact(self.table_fact(), self.case(), self.view())
        self.assertEqual(f["value"], "7,9")
        self.assertEqual(f["time"]["observation_year"], 2015)
        self.assertEqual(len(f["evidence"]), 2)
        self.assertIsNone(f["time"]["publication_year"])
        self.assertEqual(f["evidence"], self.case()["evidence"])

    def test_schema_compliance_does_not_accept_neighboring_value_or_wrong_statistic(
        self,
    ):
        for edits in [
            {"value": "61,8"},
            {"territory": "весь полигон"},
            {"unit": "%"},
            {"observation_year": 2014},
            {"statistic": "mean"},
            {"indicator": "ПАУ"},
            {"text": "В таблице максимум БП за 2015 год составляет 61,8 нг/г с.о."},
        ]:
            f = {**self.table_fact(), **edits}
            validate_schema(
                f, extract_schema(self.view())["properties"]["facts"]["items"]
            )
            with self.assertRaises(ValueError):
                check_fact(f, self.case(), self.view())

    def test_prose_and_table_versions_are_distinct_and_neither_repaired(self):
        f = {
            **self.table_fact(),
            "text": "В прозе максимум бенз(а)пирена за 2015 год указан как 61,8 нг/г с.о.",
            "primary_evidence_id": "S1",
            "cell_id": None,
            "source_role": "prose",
            "year_basis_id": "Y_MAX_2015",
            "value": "61,8",
        }
        a = check_fact(f, self.case(), self.view())
        b = check_fact(self.table_fact(), self.case(), self.view())
        self.assertNotEqual(a["fact_id"], b["fact_id"])
        self.assertEqual([a["value"], b["value"]], ["61,8", "7,9"])
        with self.assertRaises(ValueError):
            check_fact({**f, "value": "7,9"}, self.case(), self.view())

    def test_conflicting_fact_requires_role_attribution_and_explicit_ambiguity(self):
        for changes in [
            {"ambiguity_ids": []},
            {"text": "Максимум БП за 2015 год составляет 7,9 нг/г с.о."},
        ]:
            with self.assertRaises(ValueError):
                check_fact({**self.table_fact(), **changes}, self.case(), self.view())
        with self.assertRaises(ValueError):
            check_fact(
                {**self.table_fact(), "limitations": []}, self.case(), self.view()
            )

    def release(self):
        return {
            "text": "В Селенгу выпущено 1029 тыс. шт. молоди осетра средней навеской 3,0 г.",
            "primary_evidence_id": "S2",
            "cell_id": None,
            "source_role": "prose",
            "statistic": "statement",
            "observation_year": None,
            "year_basis_id": None,
            "indicator": "выпуск молоди осетра",
            "value": "1029",
            "unit": "тыс. шт.",
            "territory": "р. Селенга",
            "limitations": ["Выпуск не доказывает выживаемость; год прямо не назван."],
            "ambiguity_ids": [],
        }

    def test_neighbor_egg_year_cannot_date_release(self):
        c = self.case("baikal-sturgeon")
        v = prepare_view(c, ROOT)
        f = check_fact(self.release(), c, v)
        self.assertIsNone(f["observation_year"])
        self.assertEqual(f["time"]["report_year"], 2016)
        for edits in [
            {"observation_year": 2016, "year_basis_id": "Y_EGGS_2016"},
            {"observation_year": 2016, "year_basis_id": None},
            {"unit": "г"},
            {"primary_evidence_id": "S1"},
            {"value": None},
        ]:
            with self.assertRaises(ValueError):
                check_fact({**self.release(), **edits}, c, v)

    def test_no_silent_loss_of_cells_or_header_rebinding(self):
        # Local checkpoints are not required in a clean checkout. Test a synthetic cache.
        from unittest.mock import patch

        v = self.view()
        rows = [[""] * 7 for _ in range(5)]
        for c in v["table_view"]["cells"]:
            rows[0][c["column_index"]] = c["header"]
            rows[4][c["column_index"]] = c["raw_cell"]
            rows[4][0] = c["row_header"]
        case = self.case()
        case["fragments"] = copy.deepcopy(v["source_fragments"])
        with (
            patch("baikal.source_first.check_sources"),
            patch(
                "baikal.typed_facts.read_json",
                return_value={"tables": [{"rows": rows}]},
            ),
        ):
            validate_view(case, v, ROOT)
            bad = copy.deepcopy(v)
            bad["table_view"]["cells"].pop()
            with self.assertRaisesRegex(ValueError, "column"):
                validate_view(case, bad, ROOT)
            bad = copy.deepcopy(v)
            bad["table_view"]["cells"][0]["header"] = "Другой показатель"
            with self.assertRaises(ValueError):
                validate_view(case, bad, ROOT)

    def test_partial_fact_failure_does_not_repair_or_discard_good_fact(self):
        v = self.view()
        good = self.table_fact()
        bad = {**good, "value": "61,8"}
        result = {
            "facts": [good, bad],
            "source_conflicts": [
                {
                    "conflict_id": "ORDER_2015",
                    "description": "Проза и таблица меняют порядок максимумов БП и канцерогенных аренов.",
                    "limitations": [
                        "Нельзя выбрать исправленное значение без уточнения источника."
                    ],
                }
            ],
            "limitations": ["Подготовка структуры не является научной рецензией."],
        }
        original = copy.deepcopy(result)
        out = extract_checked(result, self.case(), v)
        self.assertEqual(len(out["facts"]), 1)
        self.assertEqual(len(out["rejected"]), 1)
        self.assertEqual(result, original)
        self.assertEqual(len(out["source_conflicts"]), 1)
        result["source_conflicts"] = []
        with self.assertRaises(ValueError):
            extract_checked(result, self.case(), v)

    def test_transport_sends_schema_and_preserves_rejection_without_real_network(self):
        class Opener:
            def open(self, req, **kwargs):
                payload = json.loads(req.data)
                self_outer.assertEqual(
                    payload["response_format"]["type"], "json_schema"
                )
                self_outer.assertNotIn(
                    "wiki_context", json.loads(payload["messages"][1]["content"])
                )
                return io.BytesIO(
                    b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}'
                )

        self_outer = self
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = self.case()
            out = call(
                root,
                "fixture",
                c,
                "extract",
                self.view(),
                extract_schema(self.view()),
                "http://127.0.0.1:1",
                Opener(),
                {},
                lambda x: extract_checked(x, c, self.view()),
            )
            self.assertTrue(out["errors"])
            self.assertIsNone(out["checked"])
            self.assertTrue((root / out["path"]).is_file())

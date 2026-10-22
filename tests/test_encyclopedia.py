import copy
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from baikal.app import app
from baikal.core import ROOT, load_sources, atomic_json
from baikal.encyclopedia import (
    catalog,
    topics,
    validate_catalog,
    search_articles,
    check_evidence,
    render_catalog,
)


class Encyclopedia(unittest.TestCase):
    def setUp(self):
        self.config = catalog()
        self.sources = {s["id"]: s for s in load_sources()["sources"]}

    def test_catalog_covers_articles_and_distinguishes_research(self):
        self.assertEqual(validate_catalog(self.config, ROOT, self.sources), [])
        entries = [t for _, _, t in topics(self.config)]
        self.assertGreaterEqual(sum(bool(t.get("article")) for t in entries), 22)
        self.assertGreaterEqual(sum(t["status"] == "research" for t in entries), 2)
        for t in entries:
            if t["status"] == "research":
                self.assertIsNone(t["article"])
        published = {t["article"] for t in entries if t.get("article")}
        self.assertEqual(
            published,
            {
                str(p.relative_to(ROOT / "wiki"))
                for p in (ROOT / "wiki/syntheses").glob("*.md")
            },
        )

    def test_catalog_rejects_source_revision_and_path_escape(self):
        config = copy.deepcopy(self.config)
        topic = next(topics(config))[2]
        topic["sources"][0]["sha256"] = "incorrect-revision"
        topic["article"] = "../README.md"
        errors = validate_catalog(config, ROOT, self.sources)
        self.assertTrue(any("revision/page mismatch" in e for e in errors))
        self.assertTrue(any("invalid encyclopedia article" in e for e in errors))

    def test_body_search_and_yo_normalization(self):
        rows = search_articles("синедровый")
        self.assertEqual(rows[0]["path"], "syntheses/phytoplankton.md")
        self.assertIn("синедров", rows[0]["snippet"].lower())
        self.assertEqual(
            search_articles("осетр")[0]["path"], "syntheses/baikal-sturgeon.md"
        )
        self.assertEqual(search_articles("несуществующеесловоxyz"), [])

    def test_multiyear_evidence_and_citations_cover_all_years(self):
        report = check_evidence()
        self.assertEqual(report["errors"], [])
        self.assertGreaterEqual(report["articles"], 14)
        self.assertTrue(set(range(2015, 2026)).issubset(report["years"]))
        from baikal.core import read_json

        for refs in read_json(ROOT / "datasets/encyclopedia_evidence.json")[
            "articles"
        ].values():
            self.assertGreaterEqual(len({r["source_id"] for r in refs}), 2)

    def test_missing_article_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atomic_json(
                root / "datasets/encyclopedia_evidence.json",
                {"articles": {"syntheses/missing.md": []}},
            )
            report = check_evidence(root)
            self.assertTrue(
                any("missing or invalid article" in e for e in report["errors"])
            )

    def test_reporting_year_is_not_silently_replaced_by_observation_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atomic_json(
                root / "config/sources.json",
                {
                    "sources": [
                        {
                            "id": "drive-test",
                            "sha256": "a",
                            "pages": 4,
                            "report_year": 2022,
                        }
                    ]
                },
            )
            article = root / "wiki/syntheses/test.md"
            article.parent.mkdir(parents=True)
            article.write_text(
                "# Test\n[drive-test:p3](../sources/drive-test.md#page-3)"
            )
            atomic_json(
                root / "datasets/encyclopedia_evidence.json",
                {
                    "articles": {
                        "syntheses/test.md": [
                            {
                                "source_id": "drive-test",
                                "sha256": "a",
                                "page": 3,
                                "report_year": 2021,
                                "statement": "Observation was made in 2021",
                                "quote": "Observed in 2021.",
                            }
                        ]
                    }
                },
            )
            self.assertTrue(
                any(
                    "reporting year mismatch" in e
                    for e in check_evidence(root)["errors"]
                )
            )

    def test_api_routes_and_article_search(self):
        with TestClient(app) as client:
            c = client.get("/api/wiki/catalog")
            self.assertEqual(c.status_code, 200)
            self.assertEqual(len(c.json()["sections"]), 6)
            r = client.get("/api/wiki/search", params={"q": "синедровый"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()[0]["path"], "syntheses/phytoplankton.md")
            self.assertEqual(
                client.get(
                    "/api/wiki/search", params={"q": "", "limit": 0}
                ).status_code,
                422,
            )
            self.assertEqual(
                client.get("/api/wiki/syntheses/baikal-omul.md").status_code, 200
            )

    def test_catalog_render_has_research_gaps_and_physical_page_links(self):
        text = render_catalog()
        self.assertIn("в очереди, статья не создана", text)
        self.assertIn("#page-", text)
        self.assertIn('<a id="life"></a>', text)

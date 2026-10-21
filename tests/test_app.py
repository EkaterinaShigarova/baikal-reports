import unittest,tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from baikal.app import app
class API(unittest.TestCase):
    def setUp(self):self.client=TestClient(app)
    def test_real_series_and_comparability_guard(self):
        r=self.client.get('/api/series',params={'group':'2018-report-table-1.3.1.2.1-total'})
        self.assertEqual(r.status_code,200);self.assertEqual(len(r.json()),7)
        self.assertEqual(self.client.get('/api/series',params={'group':'non-sulfate-sulfur-control'}).status_code,422)
    def test_dashboard_and_traversal(self):
        self.assertIn('Baikal',self.client.get('/').text)
        self.assertEqual(self.client.get('/api/wiki/%2e%2e/pyproject.toml').status_code,404)
    def test_filter(self):
        rows=self.client.get('/api/observations',params={'year':2018}).json()
        self.assertEqual(len(rows),7);self.assertTrue(all(r['year']==2018 for r in rows))
    def test_search_uses_real_fts_and_parameterization(self):
        from baikal import store
        with tempfile.TemporaryDirectory() as t,patch.object(store,'DATA',Path(t)):
            with store.connect() as db:
                db.execute('INSERT INTO page_search VALUES(?,?,?)',('s',3,'Мониторинг воды Селенга'))
            r=self.client.get('/api/search',params={'q':'Селенга'})
            self.assertEqual(r.status_code,200);self.assertEqual(r.json()[0]['page'],3)
            self.assertEqual(self.client.get('/api/search',params={'q':'" OR 1=1; --'}).status_code,200)

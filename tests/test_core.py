import unittest
from baikal.core import parse_number,comparable,validate_observation
from baikal.acquire import parse_drive_listing
class Contracts(unittest.TestCase):
    def test_scalar_and_ranges(self):
        self.assertEqual(parse_number('1\u00a0234,50'),'1234.50')
        for s in ['5,8-13,3','<0,001','около 2','10/5','NaN','1e9']:self.assertIsNone(parse_number(s))
    def test_comparability(self):
        a=dict(indicator='x',unit='мг/л',territory='Байкал',period='annual',method='mean',comparability_group='x',review_status='reviewed',comparability_approved=True)
        self.assertTrue(comparable([a,dict(a,year=2020)]))
        self.assertFalse(comparable([a,dict(a,unit='мкг/л')]))
        self.assertFalse(comparable([dict(a,review_status='candidate')]))
        self.assertFalse(comparable([dict(a,comparability_approved=False)]))
    def test_invalid_record(self):self.assertIn('invalid page',validate_observation({}))
    def test_drive_missing_listing(self):
        with self.assertRaises(ValueError):parse_drive_listing('<html>Login</html>')

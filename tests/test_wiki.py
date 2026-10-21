import tempfile,unittest
from pathlib import Path
from baikal.wiki import write_page,MARK
from baikal.llm import validate_claims
class WikiContracts(unittest.TestCase):
    def test_notes_survive_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'x.md';p.write_text('# old\n'+MARK+'\nMy verified note\n')
            self.assertTrue(write_page(p,'# new'))
            self.assertIn('My verified note',p.read_text())
            self.assertFalse(write_page(p,'# new'))
    def test_llm_citations(self):
        evidence=[{'source_id':'x','page':3,'quote':'value 123'}]
        good={'claims':[{'text':'claim','evidence':[{'source_id':'x','page':3,'quote':'123'}]}]}
        self.assertEqual(validate_claims(good,evidence),[])
        good['claims'][0]['evidence'][0]['page']=4
        self.assertTrue(validate_claims(good,evidence))

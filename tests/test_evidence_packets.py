import copy
import unittest
from baikal.core import ROOT, read_json
from baikal.evidence_packets import registry, validate, make_packet, compact_context
from scripts.evaluate_evidence_packets import gate
from baikal.packet_context import with_article_claims
from baikal.packet_terms import check_terms


class EvidencePacketsTests(unittest.TestCase):
    def setUp(self):
        self.data=registry()
        self.requests={r['id']:r for r in read_json(ROOT/'datasets/evidence_packet_requests.json')['requests']}

    def test_source_year_value_and_unit_cannot_be_borrowed(self):
        validate(self.data)
        for key,value in [('observation_period',{'years':[2030]}),('value','607'),('unit','t_water')]:
            data=copy.deepcopy(self.data);fact=data['facts'][0]
            fact[key]=value
            with self.assertRaises(ValueError):validate(data)

    def test_regional_vehicles_are_not_bpt_environmental_measurements(self):
        packet=make_packet(self.requests['roads'],self.data)
        self.assertEqual(packet['facts'],[])
        self.assertEqual(packet['structural_decision']['action'],'insufficient')
        reasons={r for x in packet['excluded'] for r in x['reasons']}
        self.assertIn('different_territorial_scope',reasons)
        self.assertIn('different_indicator',reasons)

    def test_month_annual_unit_and_statistics_remain_distinct(self):
        r=copy.deepcopy(self.requests['climate']);r['statistic']='range_of_monthly_anomaly'
        self.assertEqual([f['id'] for f in make_packet(r,self.data)['facts']],['C22-JAN'])
        r=copy.deepcopy(self.requests['climate']);r['observation_period']={'years':[2022]}
        self.assertEqual([f['id'] for f in make_packet(r,self.data)['facts']],['C22-NORM'])
        r=copy.deepcopy(self.requests['climate']);r['unit']='t_water'
        self.assertFalse(make_packet(r,self.data)['facts'])

    def test_scope_and_fact_overlap_are_separate(self):
        packet=make_packet(self.requests['forest-actions'],self.data)
        same=next(a for a in packet['related_articles'] if a['id']=='forest-fires')
        self.assertEqual(same['scope_relation'],'same_subject')
        self.assertIn('FF19-ACTIONS',same['fact_overlap'])
        self.assertEqual(packet['structural_decision']['fact_novelty'],'not_established')
        winter=make_packet(self.requests['winter-tourism'],self.data)
        self.assertEqual(winter['structural_decision']['action'],'subtopic')

    def test_header_or_cyclic_vocabulary_not_accepted(self):
        data=copy.deepcopy(self.data);data['evidence'][0]['kind']='heading'
        with self.assertRaises(ValueError):validate(data)
        data=copy.deepcopy(self.data);data['objects']['climate']['parent']='air_temperature'
        with self.assertRaises(ValueError):validate(data)

    def test_model_context_does_not_include_expected_or_structural_answer(self):
        packet=make_packet(self.requests['climate'],self.data);wire=compact_context(packet)
        self.assertNotIn('expected',wire);self.assertNotIn('structural_decision',wire)
        self.assertTrue(wire['no_global_absence_proof'])
        self.assertEqual(len(wire['issue_excerpts']),2)

    def test_prepared_projection_includes_exact_existing_claim_not_only_id(self):
        packet=make_packet(self.requests['forest-actions'],self.data)
        wire=with_article_claims(packet)
        self.assertEqual(wire['context_version'],2)
        claim=wire['matched_article_claims'][0]
        self.assertEqual(claim['fact_id'],'FF19-ACTIONS')
        self.assertIn('выполненные меры',claim['exact_article_quote'])
        self.assertEqual(claim['article_path'],'syntheses/forest-fires.md')
        self.assertNotIn('expected',wire)

    def test_indicator_meaning_and_month_cannot_be_relabelled(self):
        check_terms(self.data)
        changed=copy.deepcopy(self.data);changed['facts'][0]['indicator']='received_water_mass'
        with self.assertRaises(ValueError):check_terms(changed)
        changed=copy.deepcopy(self.data);changed['facts'][2]['observation_period']['month']=2
        with self.assertRaises(ValueError):check_terms(changed)

    def test_incompatible_model_route_and_unknown_citations_are_rejected(self):
        packet=make_packet(self.requests['forest-actions'],self.data);wire=compact_context(packet)
        answer={'action':'new_article_candidate','target_id':None,'reason':'Предлагается отдельный обзор выполненных мер.',
                'fact_novelty':'not_established','sections':[{'kind':'observations','fact_ids':['FF19-ACTIONS'],'limitation_ids':[l['id'] for l in packet['limitations']]}]}
        self.assertFalse(gate(answer,packet,wire)['program_gate_passed'])
        answer['action']='extend_existing';answer['target_id']='forest-fires'
        self.assertTrue(gate(answer,packet,wire)['program_gate_passed'])
        answer['sections'][0]['fact_ids']=['INVENTED']
        with self.assertRaises(ValueError):gate(answer,packet,wire)

    def test_unknown_topic_requires_only_new_data_not_python_branch(self):
        # Pure synthetic structural fixture, not real source evidence/model output.
        data={'objects':{'unseen':{'parent':None}},'territories':{'site':{'parent':None}},'articles':[],
              'facts':[],'evidence':[]}
        for n in (1,2):
            data['evidence'].append({'id':f'E{n}','sha256':str(n)*64})
            data['facts'].append({'id':f'F{n}','object':'unseen','territory':'site','indicator':'test_only',
                                 'support':{'evidence_id':f'E{n}'},'anchors':[]})
        request={'id':'synthetic-only','title':'Synthetic structural control','object':'unseen','territory':'site',
                 'fact_ids':['F1','F2'],'indicators':['test_only'],'limitations':[],'missing_evidence':[]}
        packet=make_packet(request,data)
        self.assertEqual(packet['structural_decision']['action'],'new_article_candidate')
        self.assertFalse(packet['published'])


if __name__=='__main__':unittest.main()

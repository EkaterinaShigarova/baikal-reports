"""Verify packet/report/draft provenance without model calls or publication."""
import argparse
import hashlib
import json
from baikal.core import ROOT,read_json,sha256
from baikal.evidence_packets import registry,validate,make_packet,compact_context
from baikal.updates import digest
try:
    from scripts.evaluate_evidence_packets import gate,PINS,PROMPT
except ModuleNotFoundError:
    from evaluate_evidence_packets import gate,PINS,PROMPT
from baikal.local_models import profile_7b
from baikal.packet_context import with_article_claims
from baikal.packet_terms import check_terms


def check(root=ROOT,require_local=False):
    report=read_json(root/'datasets/evidence_packet_evaluation.json')
    if report['published'] or report['expert_review'] or report['editorial_decision'] is not None:raise ValueError('Invalid editorial status')
    data=registry(root);counts=validate(data,root,require_local)
    check_terms(data,root)
    suite=read_json(root/'datasets/evidence_packet_requests.json')
    requests={q['id']:q for q in suite['requests']}
    packets={q['id']:make_packet(q,data,root) for q in suite['requests']}
    for p,h in report['pins'].items():
        if sha256(root/p)!=h:raise ValueError(f'Pinned input changed: {p}')
    for summary in report['packets']:
        packet=packets[summary['id']]
        if digest(packet)!=summary['packet_id'] or packet['structural_decision']!=summary['decision']:
            raise ValueError('Packet or structural decision changed')
        if digest(with_article_claims(packet))!=summary['prepared_context_id']:
            raise ValueError('Prepared exact-claim projection changed')
    structural_correct=0
    for case in suite['benchmark']:
        decision=packets[case['request_id']]['structural_decision']
        structural_correct+=all(decision[k]==v for k,v in case['expected'].items())
    model_cases=[]
    for b in suite['benchmark']:
        if b['id'] in suite['model_case_ids']:
            p=packets[b['request_id']];model_cases.append({'id':b['id'],'packet':p,'wire':compact_context(p)})
    frozen={'pins':{p:sha256(root/p) for p in PINS},'cases':model_cases,'prompt':PROMPT,
            'model_profile':profile_7b(),'max_requests':3,'published':False}
    if digest(frozen)!=report['experiment_id']:raise ValueError('Model inputs cannot be reconstructed')
    local_runs=correct=passed=0
    for output,case in zip(report['cases'],model_cases):
        if output['case_id']!=case['id']:raise ValueError('Case identities changed')
        if output['check'] is not None and gate(output['parsed'],case['packet'],case['wire'])!=output['check']:
            raise ValueError('Model gate result changed')
        expected=next(b['expected'] for b in suite['benchmark'] if b['id']==case['id'])
        match=bool(output['parsed']) and all(output['parsed'][k]==v for k,v in expected.items())
        if output['content_review']['decision_correct']!=match:raise ValueError('Model score changed')
        correct+=match;passed+=bool(output['check'] and output['check']['program_gate_passed'])
        path=root/output['path']
        if path.exists():
            run=read_json(path)
            if digest(run)!=output['run_id'] or run['parsed']!=output['parsed']:raise ValueError('Retained real response changed')
            if hashlib.sha256(run['raw_response'].encode()).hexdigest()!=output['raw_response_sha256']:raise ValueError('Raw response hash changed')
            if json.loads(run['request']['messages'][1]['content'])!=case['wire']:raise ValueError('Wire context changed')
            local_runs+=1
        elif require_local:raise ValueError('Actual local response unavailable')
    if len(report['cases'])!=3:raise ValueError('Three bounded cases required')
    summary=report['summary']
    if (summary['structural_correct']!=structural_correct or summary['model_correct']!=correct
        or summary['model_gate_passed']!=passed or summary['real_model_responses']!=len(report['cases'])
        or summary['model_plans_content_accepted']!=sum(c['content_review']['plan_accepted'] for c in report['cases'])
        or summary['new_published_articles']!=0 or summary['new_reviewed_observations']!=0):raise ValueError('Summary changed')
    drafts=0
    for draft in report['drafts']:
        path=root/draft['path']
        if not path.resolve().is_relative_to((root/'docs/drafts').resolve()) or sha256(path)!=draft['sha256']:
            raise ValueError('Unpublished draft revision changed')
        if draft['published'] or draft['expert_review'] or draft['editorial_decision'] is not None:raise ValueError('Draft cannot claim approval')
        text=path.read_text()
        for span in draft['blocks']:
            if span['text'] not in text:raise ValueError('Draft evidence block changed')
            if not set(span['fact_ids']).issubset({f['id'] for f in data['facts']}):raise ValueError('Unknown draft fact')
        drafts+=1
    pdfs=0
    source_hashes={e['sha256'] for e in data['evidence']}
    cfg={s['id']:s for s in read_json(root/'config/sources.json')['sources']}
    for lead in report['follow_up_leads']:
        if cfg[lead['source_id']]['sha256']!=lead['sha256']:raise ValueError('Follow-up source changed')
        p=root/'data/processed'/lead['sha256']/'pages'/f"{lead['page']:04d}.json"
        if p.exists():
            if sha256(p)!=lead['checkpoint_sha256'] or lead['quote'] not in read_json(p)['text']:raise ValueError('Follow-up excerpt changed')
        elif require_local:raise ValueError('Follow-up checkpoint unavailable')
        source_hashes.add(lead['sha256'])
    for image in report['visual_inspections']:
        if image['pdf_sha256']!=cfg[image['source_id']]['sha256'] or image['expert_review']:raise ValueError('Visual provenance changed')
        p=root/image['image_path']
        if p.exists():
            if sha256(p)!=image['image_sha256']:raise ValueError('Viewed image changed')
        elif require_local:raise ValueError('Viewed image unavailable')
    for sha in source_hashes:
        path=root/'data/raw'/f'{sha}.pdf'
        if path.exists():
            if sha256(path)!=sha:raise ValueError('Source PDF changed')
            pdfs+=1
        elif require_local:raise ValueError('Source PDF unavailable')
    return {'errors':[],'source_cards':counts,'packets':len(packets),'structural_correct':structural_correct,
            'model_correct':correct,'model_gate_passed':passed,'local_model_runs':local_runs,
            'source_pdf_checksums':pdfs,'unpublished_source_drafts':drafts,'expert_review':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--require-local',action='store_true');a=p.parse_args()
    try:print(json.dumps(check(require_local=a.require_local),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,TypeError) as exc:p.exit(1,f'Evidence packet report check stopped: {exc}\n')

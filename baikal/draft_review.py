"""Read-only provenance checks for arbitrary unpublished article drafts."""
import re
from decimal import Decimal
from pathlib import Path
from .core import ROOT,read_json,sha256
from .updates import digest


def pointer(value, path):
    for key in path.strip('/').split('/'):
        key=key.replace('~1','/').replace('~0','~')
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


def contains_value(value, text):
    compact=re.sub(r'\s+','',text).replace('–','-').replace('−','-')
    token=re.sub(r'\s+','',str(value)).replace('–','-').replace('−','-')
    if '-' in token:
        return re.search(r'(?<![\d.,])'+re.escape(token)+r'(?![\d.,])',compact) is not None
    number=Decimal(token.replace(',','.'))
    return number in {Decimal(x.replace(',','.')) for x in re.findall(r'(?<![\w])\d+(?:[,.]\d+)?',text)}


def validate(root=ROOT, manifest_path='datasets/editorial_draft_provenance.json', require_local=False):
    root=Path(root);manifest=read_json(root/manifest_path)
    review_path=root/manifest['review']['path']
    if sha256(review_path)!=manifest['review']['sha256']:
        raise ValueError('Source review version changed')
    review=read_json(review_path)
    if (review['published'] is not False or review['expert_review'] is not False
        or review['editorial_decision'] is not None or manifest['published'] is not False
        or manifest['expert_review'] is not False or manifest['editorial_decision'] is not None):
        raise ValueError('Draft cannot claim publication, human approval or expertise')
    cfg={s['id']:s for s in read_json(root/'config/sources.json')['sources']}
    sources={s['id']:s for s in review['sources']};checked=0;pdf_checked=set()
    for source in sources.values():
        original=cfg[source['source_id']]
        if any(source.get(k)!=original.get(k) for k in ['sha256','report_year','publication_year']):
            raise ValueError('Source version or year role changed')
        page=root/'data/processed'/source['sha256']/f"pages/{source['page']:04d}.json"
        if require_local or page.exists():
            if not page.exists():raise FileNotFoundError(page)
            if source['quote'] not in read_json(page)['text']:raise ValueError('Exact source quote absent')
            checked+=1
        pdf=root/'data/raw'/f"{source['sha256']}.pdf"
        if require_local and source['sha256'] not in pdf_checked:
            if sha256(pdf)!=source['sha256']:raise ValueError('Immutable PDF checksum mismatch')
            pdf_checked.add(source['sha256'])
    for inspection in review['visual_inspections']:
        if inspection['pdf_sha256']!=cfg[inspection['source_id']]['sha256'] or inspection['expert_review'] is not False:
            raise ValueError('Visual inspection not bound to source')
        path=root/inspection['path']
        if require_local or path.exists():
            if sha256(path)!=inspection['png_sha256']:raise ValueError('Retained page image changed')
    for check in review['arithmetic_checks']:
        if check['operation']!='subtract':raise ValueError('Unsupported consistency operation')
        for operand in check['operands']+[check['printed_change']]:
            if not contains_value(operand['value'],sources[operand['evidence_id']]['quote']):
                raise ValueError('Arithmetic operand missing from its own source')
        a,b=[Decimal(x['value']) for x in check['operands']]
        if str(a-b)!=check['calculated_change'] or check['repair_source'] is not False:
            raise ValueError('Calculation cannot repair published data')
    for issue in review['issues']:
        for suggestion in issue.get('editorial_suggestions',[]):
            path=root/suggestion['article']['path']
            if (sha256(path)!=suggestion['article']['sha256'] or suggestion['article_quote'] not in path.read_text()
                or suggestion['editorial_decision'] is not None or suggestion['published'] is not False
                or suggestion['expert_review'] is not False):
                raise ValueError('Editorial suggestion lost its version/status boundary')
    obs=review['observation_snapshot']
    if sha256(root/obs['path'])!=obs['sha256']:raise ValueError('Reviewed observations changed')
    for candidate in review['candidates']:
        fact=pointer(read_json(root/candidate['origin']),candidate['fact_pointer'])
        if digest(fact)!=candidate['fact_sha256'] or candidate['id']!=fact.get('fact_id',fact.get('id')):
            raise ValueError('Reviewed candidate was changed or borrowed')
        if (sha256(root/candidate['article']['path'])!=candidate['article']['sha256']
            or candidate['expert_review'] is not False or candidate['published'] is not False
            or candidate['editorial_decision'] is not None):
            raise ValueError('Candidate version/approval changed')
        if not contains_value(fact['features']['value'],sources[candidate['source_id']]['quote']):
            raise ValueError('Candidate quantity not supported by its own source')
        cert=candidate['absence_review'];inventory=candidate['inventory']
        if sha256(root/inventory['path'])!=inventory['sha256']:raise ValueError('Absence inventory changed')
        units=pointer(read_json(root/inventory['path']),inventory['pointer'])
        if (cert['fact_sha256']!=digest(fact) or cert['article_sha256']!=candidate['article']['sha256']
            or cert[inventory['certificate_key']]!=[u['id'] for u in units]):
            raise ValueError('Whole-article novelty review lost version binding')
        archive=root/candidate['previous_complete_article']['path']
        if require_local or archive.exists():
            if sha256(archive)!=candidate['article']['sha256']:raise ValueError('Full prior article not preserved')
    blocks=quantities=0
    for draft in manifest['drafts']:
        path=(root/draft['path']).resolve()
        if not path.is_relative_to((root/manifest['draft_root']).resolve()):raise ValueError('Draft outside review area')
        text=path.read_text()
        if (sha256(path)!=draft['sha256'] or draft['status']!='draft_awaiting_editorial_review'
            or draft['expert_review'] is not False or draft['published'] is not False
            or draft['editorial_decision'] is not None):
            raise ValueError('Draft version/status changed')
        for block in draft['blocks']:
            if text[block['start']:block['end']]!=block['quote']:raise ValueError('Draft span changed')
            for eid in block['evidence_ids']:
                source=sources[eid]
                anchor=f"{source['source_id']}.md#page-{source['page']}"
                if anchor not in block['quote']:raise ValueError('Draft claim lacks its cited physical page')
            blocks+=1
        for item in draft['quantities']:
            if (item['expert_review'] is not False or item['comparability_approved'] is not False
                or item['draft_lexeme'] not in text or not contains_value(item['value'],sources[item['evidence_id']]['quote'])):
                raise ValueError('Draft quantity absent from cited evidence')
            quantities+=1
    return {'errors':[],'drafts':len(manifest['drafts']),'source_excerpts':len(sources),
            'checked_local_excerpts':checked,'pdf_checksums_checked':len(pdf_checked),
            'editorial_candidates':len(review['candidates']),'evidence_blocks':blocks,
            'curated_quantities':quantities,'published':False,'expert_review':False}

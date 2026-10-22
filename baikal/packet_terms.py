"""Controlled indicator/unit/statistic meanings for newly built packets.

Added after the bounded model run; no retrospective model rescore.
"""
import re
from .core import ROOT,read_json


def check_terms(data, root=ROOT):
    vocabulary=read_json(root/'config/packet_indicators.json')
    for fact in data['facts']:
        definition=vocabulary['indicators'].get(fact['indicator'])
        if not definition:raise ValueError('Unknown scientific indicator')
        for field,allowed in [('unit','units'),('statistic','statistics'),('message_kind','message_kinds')]:
            if fact[field] not in definition[allowed]:raise ValueError('Indicator meaning/unit/statistic/message mismatch')
        period=fact['observation_period']
        if period and period.get('month'):
            stem=vocabulary['month_stems'].get(str(period['month']))
            quotes=' '.join(a['quote'] for a in fact['anchors'] if a['role']=='period')
            if not stem or not re.search(re.escape(stem),quotes,re.I):raise ValueError('Month interpretation unanchored')
    return {'controlled_indicators':len(vocabulary['indicators']),'cards_checked':len(data['facts'])}

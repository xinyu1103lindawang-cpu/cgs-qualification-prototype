"""Deterministic, proposed CGS rules. The model never calculates final totals."""
import json
from pathlib import Path
RULES = json.loads((Path(__file__).resolve().parents[1] / 'data/rules.json').read_text())
WEIGHTS, LABELS, ANCHORS = (RULES[k] for k in ('weights', 'labels', 'anchors'))
CONDITIONS, REASONS = RULES['conditionLabels'], RULES['reasons']
FLOORS = {'B': 2, 'A': 2, 'N': 3, 'T': 2, 'E': 2}
RULE_VERSION = '1.1-proposed'

def tier(n):
    return 5 if n >= 1000000 else 4 if n >= 500000 else 3 if n >= 100000 else 2

def band(n):
    return 'High' if n >= 75 else 'Medium' if n >= 55 else 'Low'

def calculate(c):
    coverage = sum(w for k, w in WEIGHTS.items() if c['scores'].get(k) is not None)
    ql = sum(w * c['scores'][k] / 5 for k, w in WEIGHTS.items() if c['scores'].get(k) is not None)
    qu = ql + 100 - coverage
    revenue = c.get('revenue')
    rl, ru = (tier(revenue['low']) * 20, tier(revenue['high']) * 20) if revenue else (0, 100)
    pl, pu = .6 * ql + .4 * rl, .6 * qu + .4 * ru
    floors = all(c['scores'].get(k) is not None and c['scores'][k] >= v for k, v in FLOORS.items())
    conditions = all(c.get('conditions', {}).get(k) is True for k in CONDITIONS)
    readiness = 'Not ready' if c.get('blocker') else 'Ready' if conditions and coverage >= 80 and ql >= 70 and floors else 'More evidence needed'
    priority = 'Pending confirmation' if not revenue else band(pl) if band(pl) == band(pu) else band(pl) + '–' + band(pu)
    gaps = []
    for k, minimum in FLOORS.items():
        if c['scores'].get(k) is None:
            gaps.append({'key': k, 'label': LABELS[k] + ': pending confirmation', 'question': QUESTIONS[k]})
        elif c['scores'][k] < minimum:
            gaps.append({'key': k, 'label': LABELS[k] + ': below minimum', 'question': QUESTIONS[k]})
    for k, label in CONDITIONS.items():
        if c.get('conditions', {}).get(k) is not True:
            gaps.append({'key': 'condition:' + k, 'label': label + (': not met' if c.get('conditions', {}).get(k) is False else ': pending confirmation'), 'question': CONDITION_QUESTIONS[k]})
    if ql < 70 and not gaps:
        gaps.append({'key': 'Q', 'label': 'Confirmed Q is below 70', 'question': 'Which business outcomes and practical commitments can be validated next?'})
    if c['scores'].get('H') is None:
        gaps.append({'key': 'H', 'label': 'Relationship history: pending confirmation', 'question': QUESTIONS['H']})
    if not revenue:
        gaps.append({'key': 'revenue', 'label': 'CGS revenue: to estimate', 'question': 'What CGS service scope and fee basis support an initial revenue estimate?'})
    return dict(ql=round(ql, 4), qu=round(qu, 4), coverage=coverage, rl=rl, ru=ru,
                rt=rl / 20 if revenue and rl == ru else None, pl=round(pl, 4), pu=round(pu, 4),
                readiness=readiness, priority=priority, gaps=gaps, ruleVersion=RULE_VERSION)

QUESTIONS = {'B': 'What funding source is available, and what still needs budget approval?',
             'A': 'Who approves this purchase, and how can they join the discussion?',
             'N': 'What specific problem is affecting the business, and how will success be measured?',
             'T': 'What business milestone drives the timing, and when is a decision expected?',
             'H': 'Have we worked together before, and what was the outcome?',
             'E': 'What useful information or action can we agree to exchange next?'}
CONDITION_QUESTIONS = {'problem': QUESTIONS['N'], 'fit': 'Which approved CGS capability matches the client’s problem?',
                       'external': 'Are you considering external consulting support for this work?',
                       'next': 'What specific next action have both sides agreed, with an owner and date?',
                       'commercial': 'Can we confirm a viable funding source and decision-access path?',
                       'conflict': 'Which conflicting statements need clarification before we rely on them?'}

def validate_score(key, value):
    if key not in WEIGHTS or (value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value not in ([0, 1, 2, 2.5, 3, 4, 5] if key == 'H' else [0, 1, 2, 3, 4, 5]))):
        raise ValueError('Use a valid rubric score, or leave it pending.')

def delta(before, after):
    return {'q': round(after['ql'] - before['ql'], 4), 'p': round(after['pl'] - before['pl'], 4),
            'before': before, 'after': after}

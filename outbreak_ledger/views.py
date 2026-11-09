"""Explicit effective-value selection; contradictory approved claims abstain."""
from collections import defaultdict

def effective(c):
    return c['corrected_value'] if c['status']=='corrected' else c['value']

def key(c):
    return tuple(c.get(k) for k in ('issuer','disease','geo','geo_level','indicator','classification','count_type','period_end'))

def select_claims(candidates, approved_only=True):
    groups=defaultdict(list)
    for c in candidates:
        if c['status']!='rejected':groups[key(c)].append(c)
    selected=[]; conflicts=[]
    for k,items in groups.items():
        approved=[c for c in items if c['status'] in ('approved','corrected')]
        pool=approved if approved else [] if approved_only else items
        if not pool:continue
        values={effective(c) for c in pool}
        if len(values)>1:
            conflicts.append({'key':k,'ids':[c['id'] for c in pool],'values':sorted(values,key=lambda v:(v is None,v or 0))})
        else:
            # Identical assertions are equivalent for this view; keep all raw records.
            selected.append(max(pool,key=lambda c:(c.get('reviewed_at') or '',c['id'])))
    return selected,conflicts

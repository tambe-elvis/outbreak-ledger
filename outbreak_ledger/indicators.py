"""Approved-view indicators with explicit source selection and calendar coverage."""
from collections import defaultdict
from datetime import date,timedelta
from .views import effective, select_claims
APPROVED=('approved','corrected')

def coverage(store,level):
    docs=store.rows('SELECT doc_id,issuer,report_no,period_end FROM documents ORDER BY issuer,period_end')
    present=defaultdict(set)
    for c in store.rows('SELECT DISTINCT doc_id,geo FROM candidates WHERE is_total=0 AND geo_level=?',(level,)):
        present[c['doc_id']].add(c['geo'])
    issuers=defaultdict(list)
    for d in docs:issuers[d['issuer']].append(d)
    out=[]
    for issuer,ds in issuers.items():
        geos=sorted(set().union(*(present[d['doc_id']] for d in ds)))
        for d in ds:
            for g in geos:out.append({'issuer':issuer,'report_no':d['report_no'],'period_end':d['period_end'],'geo':g,'status':'reported' if g in present[d['doc_id']] else 'not_reported'})
    return out

def new_confirmed_by_period(store,issuer,level):
    cov=[r for r in coverage(store,level) if r['issuer']==issuer]
    all_rows=store.rows("SELECT * FROM candidates WHERE issuer=? AND indicator='cases' AND classification='confirmed' AND count_type='new' AND is_total=0 AND geo_level=?",(issuer,level))
    chosen,conflicts=select_claims(all_rows)
    vals={(r['geo'],r['period_end']):r for r in chosen}
    disputed={(x['key'][2],x['key'][-1]) for x in conflicts}
    out=[];seen=set()
    for r in cov:
        k=(r['geo'],r['period_end'])
        if k in seen:continue
        seen.add(k); c=vals.get(k)
        if k in disputed:v=None;basis='abstain: conflicting approved claims; resolve explicitly'
        elif c is not None:v=effective(c);basis=f"approved candidate {c['id']}"
        elif r['status']=='not_reported':v=None;basis='abstain: geography not reported in this report'
        elif any((x['geo'],x['period_end'])==k for x in all_rows):v=None;basis='abstain: no unambiguous approved value (awaiting review or rejected)'
        else:v=None;basis='abstain: no explicit new-confirmed value'
        out.append({**r,'value':v,'basis':basis})
    return out

def time_since_last_confirmed(store,issuer,level):
    series=defaultdict(list)
    docs=store.rows('SELECT period_start,period_end FROM documents WHERE issuer=?',(issuer,))
    daily=bool(docs) and all(d['period_start']==d['period_end'] for d in docs)
    for r in new_confirmed_by_period(store,issuer,level):series[r['geo']].append(r)
    out=[]
    for geo,rows in sorted(series.items()):
        rows.sort(key=lambda r:r['period_end'] or '')
        if any(not r['period_end'] for r in rows):
            out.append({'geo':geo,'result':None,'basis':'abstain: invalid reporting date'});continue
        positives=[r for r in rows if r['value'] is not None and r['value']>0]
        if not positives:
            out.append({'geo':geo,'result':None,'basis':'abstain: no positive period among approved values in this window'});continue
        last=positives[-1]; after=[r for r in rows if r['period_end']>last['period_end']]
        gaps=[r for r in after if r['value'] is None]
        if daily:
            expected=date.fromisoformat(last['period_end'])+timedelta(days=1)
            latest=date.fromisoformat(rows[-1]['period_end']);actual={r['period_end'] for r in after}
            while expected<=latest:
                if expected.isoformat() not in actual:gaps.append({'period_end':expected.isoformat(),'basis':'absent expected daily report'})
                expected+=timedelta(days=1)
        if gaps:out.append({'geo':geo,'result':None,'basis':'abstain: '+'; '.join(g['period_end']+' '+g['basis'] for g in gaps)});continue
        # Non-daily ranges must cover the interval without missing periods.
        if not daily:
            by_end={d['period_end']:d['period_start'] for d in docs}
            previous_end=date.fromisoformat(last['period_end']); bad=False
            for r in after:
                start=by_end.get(r['period_end'])
                if not start or date.fromisoformat(start)>previous_end+timedelta(days=1):bad=True
                previous_end=date.fromisoformat(r['period_end'])
            if bad:out.append({'geo':geo,'result':None,'basis':'abstain: reporting periods do not provide continuous coverage'});continue
        out.append({'geo':geo,'result':(date.fromisoformat(rows[-1]['period_end'])-date.fromisoformat(last['period_end'])).days,'basis':f"Elapsed days since positive reporting period ending {last['period_end']}; {len(after)} explicit zero periods. Not days since infection or onset."})
    return out

"""Regression tests for reviewer controls, coverage and local HTTP workflow."""
import base64,csv,io,json,threading,urllib.request,urllib.error
from pathlib import Path
import pytest
from outbreak_ledger.store import Store
from outbreak_ledger.harmonize import parse_date
from outbreak_ledger.views import select_claims
from outbreak_ledger.indicators import time_since_last_confirmed
from outbreak_ledger.extract import verify_provenance
from outbreak_ledger import web
from samples.make_samples import build_all


def simple_store(tmp_path, days=('2026-06-01','2026-06-03')):
    s=Store(str(tmp_path/'s.db'))
    for i,d in enumerate(days):
        s.add_document(dict(doc_id=str(i),issuer='TEST',report_no=i+1,period_start=d,period_end=d))
        s.add_candidate(dict(doc_id=str(i),issuer='TEST',disease='BVD',geo='alpha',geo_level='health_zone',is_total=0,period_end=d,indicator='cases',classification='confirmed',count_type='new',value=1 if i==0 else 0))
    s.commit();return s

@pytest.mark.parametrize('value',[-1,1.2,True,'3',None])
def test_correction_rejects_invalid_counts(tmp_path,value):
    s=simple_store(tmp_path)
    with pytest.raises(ValueError):s.review(1,'correct','tester','checked source',value, require_event=False)
    assert not s.rows('SELECT * FROM review_log')

@pytest.mark.parametrize('reviewer,note',[('','source checked'),('tester',''),('  ','  ')])
def test_reviewer_and_reason_required(tmp_path,reviewer,note):
    s=simple_store(tmp_path)
    with pytest.raises(ValueError):s.review(1,'approve',reviewer,note, require_event=False)


def test_missing_entire_daily_report_abstains(tmp_path):
    s=simple_store(tmp_path)
    for i in (1,2):s.review(i,'approve','tester','source checked', require_event=False)
    row=time_since_last_confirmed(s,'TEST','health_zone')[0]
    assert row['result'] is None and '2026-06-02' in row['basis']


def test_correction_rejection_history(tmp_path):
    s=simple_store(tmp_path)
    s.review(1,'correct','tester','checked against PDF',9, require_event=False)
    s.review(1,'reject','tester','wrong case definition', require_event=False)
    event=s.rows('SELECT * FROM review_log ORDER BY id DESC')[0]
    assert event['old_effective_value']==9 and event['new_effective_value'] is None
    assert s.rows('SELECT value FROM candidates WHERE id=1')[0]['value']==1


def test_approved_conflicts_require_resolution(tmp_path):
    s=simple_store(tmp_path)
    c=s.rows('SELECT * FROM candidates WHERE id=1')[0];c.pop('id');c['value']=7
    cid=s.add_candidate(c)
    s.review(1,'approve','tester','checked source', require_event=False);s.review(cid,'approve','tester','competing claim checked', require_event=False)
    chosen,conflicts=select_claims(s.rows('SELECT * FROM candidates'))
    assert not chosen and len(conflicts)==1
    s.review(1,'reject','tester','earlier claim superseded explicitly', require_event=False)
    chosen,conflicts=select_claims(s.rows('SELECT * FROM candidates'))
    assert not conflicts and chosen[0]['value']==7


def test_invalid_calendar_date():
    assert parse_date('2026-02-30') is None


def test_real_http_import_review_export_persistence_and_integrity(tmp_path):
    reports=tmp_path/'reports';build_all(str(reports))
    class H(web.Handler):
        data_dir=tmp_path/'data';preview=False
    server=web.ThreadingHTTPServer(('127.0.0.1',0),H)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root='http://127.0.0.1:'+str(server.server_port)
    def request(path,payload=None,token=web.TOKEN):
        headers={'Content-Type':'application/json','X-Ledger-Token':token}
        req=urllib.request.Request(root+path,data=None if payload is None else json.dumps(payload).encode(),headers=headers)
        with urllib.request.urlopen(req) as res:return res.headers.get_content_type(),res.read()
    try:
        with pytest.raises(urllib.error.HTTPError) as e:request('/api/demo',{},token='bad')
        assert e.value.code==403
        files=[{'name':p.name,'data':base64.b64encode(p.read_bytes()).decode()} for p in sorted(reports.glob('*.pdf'))]
        _,body=request('/api/import',{'module':'synthetic_demo','files':files})
        assert all('error' not in r for r in json.loads(body)['results'])
        _,body=request('/api/state?module=synthetic_demo');st=json.loads(body)
        assert st['stats']['documents']==4 and st['stats']['approved']==0
        cid=next(c['id'] for c in st['candidates'] if c['value'] is not None and 'unknown' not in (c['indicator'],c['classification'],c['count_type']))
        c=next(c for c in st['candidates'] if c['id']==cid)
        mime,png=request('/api/page?module=synthetic_demo&doc='+c['doc_id']+'&page='+str(c['page']))
        assert mime=='image/png' and png.startswith(b'\x89PNG')
        with pytest.raises(urllib.error.HTTPError) as e:request('/api/review',{'module':'synthetic_demo','id':cid,'action':'correct','value':23,'reviewer':'tester','note':'source checked in test'})
        assert e.value.code==400   # event assignment not yet confirmed
        ev=c['events'][0]['event_id']
        request('/api/event',{'module':'synthetic_demo','event_id':ev,'action':'confirm','include_assignments':True,'reviewer':'tester','note':'synthetic demo event checked'})
        request('/api/review',{'module':'synthetic_demo','id':cid,'action':'correct','value':23,'reviewer':'tester','note':'source checked in test'})
        mime,body=request('/api/export?module=synthetic_demo')
        text=body.decode('utf-8-sig');assert text.startswith('# policy=OL-RP-1')
        rows=list(csv.DictReader(io.StringIO(text.split('\n',1)[1])))
        assert rows[0]['event_id']==ev and rows[0]['disease']
        assert len(rows)==1 and rows[0]['effective_value']=='23'
        # Reopening independent connections reproduces saved state.
        assert web.state(H.data_dir,'synthetic_demo')['stats']['approved']==1
        _,m,s=web.init_context(H.data_dir,'synthetic_demo')
        assert all(r['provenance_ok'] for r in verify_provenance(s))
        source=Path(s.rows('SELECT path FROM documents WHERE doc_id=?',(c['doc_id'],))[0]['path']);source.write_bytes(source.read_bytes()+b'\nchanged')
        assert any(not r['provenance_ok'] for r in verify_provenance(s))
        s.conn.close()
        with pytest.raises(urllib.error.HTTPError) as e:request('/api/export?module=synthetic_demo')
        assert e.value.code==409
        with pytest.raises(urllib.error.HTTPError):request('/api/page?module=synthetic_demo&doc='+c['doc_id'])
    finally:server.shutdown();server.server_close();thread.join()


def test_rules_use_corrected_counts(tmp_path):
    from outbreak_ledger.harmonize import Module
    from outbreak_ledger.rules import run_rules
    s=simple_store(tmp_path,days=('2026-06-01','2026-06-02'))
    s.conn.execute("UPDATE candidates SET count_type='cumulative', value=10 WHERE id=1")
    s.conn.execute("UPDATE candidates SET count_type='cumulative', value=8 WHERE id=2")
    m=Module(str(web.ROOT/'config'/'synthetic_demo.yaml'))
    run_rules(s,m)
    assert 'cumulative_decrease' in s.rows('SELECT flags FROM candidates WHERE id=2')[0]['flags']
    s.review(2,'correct','tester','source confirms twelve',12, require_event=False)
    run_rules(s,m)
    assert 'cumulative_decrease' not in s.rows('SELECT flags FROM candidates WHERE id=2')[0]['flags']


def test_partial_or_edited_verification_sheet_does_not_inflate_accuracy(tmp_path):
    from outbreak_ledger.evaluate import score_review_sheet
    s=simple_store(tmp_path);sheet=tmp_path/'sheet.csv'
    cols=['candidate_id','expert_value','meaning_correct_yes_no','extracted_value']
    def write(rows):
        with sheet.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(rows)
    write([dict(candidate_id=1,expert_value='',meaning_correct_yes_no='yes',extracted_value='1')])
    with pytest.raises(ValueError):score_review_sheet(s,str(sheet))
    write([dict(candidate_id=1,expert_value='99',meaning_correct_yes_no='yes',extracted_value='99')])
    assert next(iter(score_review_sheet(s,str(sheet))['by_table'].values()))['value_accuracy']==0
    write([dict(candidate_id=999,expert_value='1',meaning_correct_yes_no='yes',extracted_value='1')])
    with pytest.raises(ValueError):score_review_sheet(s,str(sheet))

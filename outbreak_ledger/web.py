"""Browser UI for the local pilot. No remote service or AI API is used."""
import argparse,base64,csv,hashlib,io,json,os,secrets,threading,webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from .store import Store,now
from . import __version__, briefs
from .extract import ingest,sha256,detect_module
from .harmonize import Module
from .rules import run_all_rules
from .views import effective
from . import events as EV
from . import issues as IS
from .reconcile import reconcile,POLICY_ID
from .extract import diagnose

ROOT=Path(__file__).resolve().parents[1]
ASSETS=Path(__file__).with_name('web_assets')
LOCK=threading.RLock()
TOKEN=secrets.token_urlsafe(32)
MODULES={'events':'Outbreak workspace (automatic report recognition)','synthetic_demo':'Synthetic demonstration'}
FAMILIES={'events':['bvd_drc_insp','drc_insp_weekly_profile','who_wer'],'synthetic_demo':['synthetic_demo']}
MAX_FILES,MAX_FILE_MB,MAX_BATCH_MB=25,20,60

def init_context(data_dir,module):
    if module not in MODULES:raise ValueError('Unknown workspace')
    folder=Path(data_dir).resolve()/module;folder.mkdir(parents=True,exist_ok=True)
    sources=folder/'sources';sources.mkdir(exist_ok=True)
    mods={n:Module(str(ROOT/'config'/f'{n}.yaml')) for n in FAMILIES[module]}
    return folder,mods,Store(str(folder/'ledger.db'))

def config_fingerprint(mods):
    h=hashlib.sha256()
    for n in sorted(list(mods)+['qualitative_common']):h.update((ROOT/'config'/f'{n}.yaml').read_bytes())
    return h.hexdigest()[:16]

def refresh(s,mods):
    """Recompute every rule-derived layer from stored evidence and reviewer decisions."""
    rules=run_all_rules(s,mods)
    EV.propose_all(s,mods)
    recon=reconcile(s,mods)
    issues=IS.build(s)
    return rules,recon,issues

def event_summaries(s,rows,statements,recon,issues):
    evs={e['event_id']:dict(e) for e in s.rows('SELECT * FROM events')}
    for e in evs.values():
        e['geo_scope']=json.loads(e['geo_scope'] or '[]');e['aliases']=json.loads(e['aliases'] or '[]')
        e.update(observations=0,approved=0,pending=0,statements=0,challenges=0,signals=0,families=set(),issuers=set(),documents=set(),first_evidence=None,last_evidence=None,confirmed_links=0,proposed_links=0)
    def span(e,p):
        if p:
            e['first_evidence']=min(filter(None,[e['first_evidence'],p]));e['last_evidence']=max(filter(None,[e['last_evidence'],p]))
    for r in rows:
        for l in r['events']:
            e=evs.get(l['event_id'])
            if not e or r['status']=='rejected':continue
            e['observations']+=1;e['approved']+=r['status'] in ('approved','corrected');e['pending']+=r['status']=='candidate'
            e['families'].add(r['family']);e['issuers'].add(r['issuer'] or '?');e['documents'].add(r['doc_id']);span(e,r['period_end'])
            e['confirmed_links' if l['status']=='confirmed' else 'proposed_links']+=1
    for st in statements:
        if st['status']=='rejected':continue
        for l in st['events']:
            e=evs.get(l['event_id'])
            if not e:continue
            e['statements']+=1;e['challenges']+=st['statement_type']=='challenge';e['signals']+=st['statement_type'] in ('signal','published_product')
            e['families'].add(st['family']);e['documents'].add(st['doc_id']);span(e,st['period_end'])
            e['confirmed_links' if l['status']=='confirmed' else 'proposed_links']+=1
    for e in evs.values():
        e['series']=[x for x in recon['series'] if x['event_id']==e['event_id']]
        e['issues']=[i['issue_id'] for i in issues if e['event_id'] in i['events']]
        e['open_issues']=sum(1 for i in issues if e['event_id'] in i['events'] and i['derived_code'] in ('ongoing_latest','ongoing_not_updated','contradictory'))
        e['families']=sorted(e['families']);e['issuers']=sorted(e['issuers']);e['documents']=sorted(e['documents'])
    order={'outbreak_episode':0,'surveillance_stream':1,'public_health_signal':2}
    return sorted(evs.values(),key=lambda e:(e['status']=='merged',order.get(e['event_type'],3),-(e['observations']+e['statements']),e['event_id']))

def state(data_dir,module):
    folder,mods,s=init_context(data_dir,module)
    try:
        rules,recon,issues=refresh(s,mods)
        fam={n:m.family_label for n,m in mods.items()}
        links=EV.item_events(s)
        titles={e['event_id']:e['title'] for e in s.rows('SELECT event_id,title FROM events')}
        rows=s.rows('SELECT c.*,d.filename,d.report_no,d.module,d.publication_date FROM candidates c JOIN documents d USING(doc_id) ORDER BY c.id')
        for r in rows:
            r['effective_value']=effective(r);r['flags']=json.loads(r['flags']);r['family']=fam.get(r['module'],r['module'])
            r['events']=links.get(('candidate',r['item_key']),[])
            for l in r['events']:l['title']=titles.get(l['event_id'])
        docs=s.rows('SELECT doc_id,filename,issuer,report_no,period_start,period_end,publication_date,retrieved_at,metadata_flags,module,episode,family_origin FROM documents ORDER BY module,period_end,filename')
        for d in docs:d['family']=fam.get(d['module'],d['module'])
        statements=s.rows('SELECT st.*,d.filename,d.report_no FROM statements st JOIN documents d USING(doc_id) ORDER BY d.module,d.period_end,st.id')
        issue_of={m['item_key']:i['issue_id'] for i in issues for m in i['members']}
        for st in statements:
            for k in ('topics','geos','health_zones','quantities','dates'):st[k]=json.loads(st[k] or '[]')
            st['family']=fam.get(st['module'],st['module']);st['issue_id']=issue_of.get(st['item_key'])
            st['events']=links.get(('statement',st['item_key']),[])
            for l in st['events']:l['title']=titles.get(l['event_id'])
        events=event_summaries(s,rows,statements,recon,issues)
        unrec=[]
        for u in s.rows('SELECT filename,reason,at,doc_id FROM unrecognized ORDER BY at DESC'):
            try:u['diagnosis']=json.loads(u['reason'])
            except (TypeError,ValueError):u['diagnosis']={'reason':u['reason']}
            unrec.append(u)
        relations=s.rows('SELECT * FROM relations ORDER BY kind,status,rel_key')
        return {'version':__version__,'policy':POLICY_ID,'config_fingerprint':config_fingerprint(mods),'module':module,'modules':MODULES,'families':fam,
                'family_options':[{'name':n,'label':m.family_label} for n,m in mods.items()],
                'documents':docs,'candidates':rows,'statements':statements,'events_list':events,'series':recon['series'],
                'cross_issuer':recon['cross_issuer_disagreements'],'issues':issues,'relations':relations,'unrecognized':unrec,
                'public_notes':public_notes(s),
                'findings':rules['findings'][:500],'gap_notes':s.rows('SELECT * FROM gap_notes'),
                'events':s.rows('SELECT * FROM review_log ORDER BY id DESC LIMIT 1000'),
                'statement_events':s.rows('SELECT * FROM statement_review_log ORDER BY id DESC LIMIT 1000'),
                'decisions':s.rows('SELECT * FROM decisions ORDER BY id DESC LIMIT 1000'),
                'stats':{'documents':len(docs),'candidates':len(rows),'flagged':rules['flagged'],'approved':sum(r['status'] in ('approved','corrected') for r in rows),
                         'pending':sum(r['status']=='candidate' for r in rows),'statements':sum(st['status']!='rejected' for st in statements),
                         'events':sum(e['status']!='merged' for e in events),'confirmed_events':sum(e['status']=='confirmed' for e in events),
                         'issues':len(issues),'open_issues':sum(i['derived_code'] in ('ongoing_latest','ongoing_not_updated') for i in issues),
                         'unresolved_periods':sum(x['unresolved_periods'] for x in recon['series']),'unrecognized':len(unrec)},
                'rule_basis':'Rule outputs are proposals. Reviewed values, event assignments and links require a named reviewer and a reason.'}
    finally:s.conn.close()

def save_public_note(store, data):
    """Record a reviewed English description against one version of a claim."""
    key = data['item_key']
    event = data['event_id']
    target = f'{event}:{key}'
    action = data['action']
    if action not in ('save', 'withdraw'):
        raise ValueError('Choose save or withdraw for this response note.')
    payload = {'item_key': key, 'event_id': event}
    if action == 'save':
        rows = store.rows("SELECT status, reviewed_at FROM statements WHERE item_key=?", (key,))
        links = EV.item_events(store).get(('statement', key), [])
        if not rows or rows[0]['status'] not in ('confirmed', 'retagged') or not any(
            link['event_id'] == event and link['status'] == 'confirmed' for link in links
        ):
            raise ValueError('Review the statement and confirm its event before saving an English note.')
        summary = data.get('summary')
        if not isinstance(summary, str) or not 10 <= len(summary.strip()) <= 600:
            raise ValueError('Enter an English response note of 10 to 600 characters.')
        payload.update(summary=summary.strip(), statement_reviewed_at=rows[0]['reviewed_at'])
    store.decide('public_note', target, action, payload, data['reviewer'], data['note'], POLICY_ID)


def public_notes(store):
    """Return the last decision for each note. The reader checks current evidence eligibility."""
    rows = store.rows("SELECT id, target, action, payload, reviewer, note, at FROM decisions WHERE kind='public_note' ORDER BY id")
    latest = {row['target']: row for row in rows}
    return [{**json.loads(row['payload']),
             'note_review': {key: row[key] for key in ('id', 'reviewer', 'note', 'at')}}
            for row in latest.values() if row['action'] == 'save']


def check_sources(folder,doc_ids):
    for doc_id in doc_ids:
        source=folder/'sources'/f'{doc_id}.pdf'
        if not source.exists() or sha256(source)!=doc_id:return False
    return True

def csv_response(rows,cols,meta):
    buf=io.StringIO(newline='');buf.write('# '+'; '.join(f'{k}={v}' for k,v in meta.items())+'\n')
    w=csv.DictWriter(buf,fieldnames=cols,extrasaction='ignore');w.writeheader()
    for r in rows:
        r={k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v) for k,v in r.items()}
        for k in cols:
            if isinstance(r.get(k),str) and r[k].startswith(('=','+','-','@')):r[k]="'"+r[k]  # spreadsheet formula injection guard
        w.writerow(r)
    return '\ufeff'+buf.getvalue()

def import_files(data_dir,module,files):
    if not files or len(files)>MAX_FILES:raise ValueError(f'Select 1-{MAX_FILES} PDF files per batch')
    folder,mods,s=init_context(data_dir,module);results=[]
    try:
        for f in files:
            name=Path(f.get('name','')).name
            if not name.lower().endswith('.pdf'):results.append({'name':name,'error':'Only PDF files are supported in this version'});continue
            data=base64.b64decode(f['data'],validate=True)
            if len(data)>MAX_FILE_MB*1024*1024 or not data.startswith(b'%PDF-'):results.append({'name':name,'error':f'PDF must be valid and at most {MAX_FILE_MB} MB'});continue
            h=hashlib.sha256(data).hexdigest();dest=folder/'sources'/f'{h}.pdf'
            if not dest.exists():dest.write_bytes(data)
            elif sha256(dest)!=h:raise ValueError('Managed source hash mismatch')
            if s.has_document(h):results.append({'name':name,'duplicate':True});continue
            m=detect_module(str(dest),list(mods.values()))
            if m is None:
                diag=diagnose(str(dest),list(mods.values()))
                s.conn.execute("INSERT OR REPLACE INTO unrecognized (doc_id,filename,path,reason,at) VALUES (?,?,?,?,datetime('now'))",(h,name,str(dest),json.dumps(diag,ensure_ascii=False)));s.commit()
                results.append({'name':name,'error':'Not recognised: '+diag['reason']+' Kept in the unrecognised list; nothing was counted, and its absence is not a zero. A reviewer can assign a report family in Sources.'});continue
            results.append(_ingest_one(s,m,dest,h,name))
        refresh(s,mods);archive_config(folder,mods)
        return results
    finally:s.conn.close()

def _ingest_one(s,m,dest,h,name,override=None):
    s.conn.execute('SAVEPOINT file_import')
    try:
        r=ingest(str(dest),m,s,override)
        if not r.get('candidates') and not r.get('statements'):
            for t in ('candidates','statements','context','revision_notes','documents'):s.conn.execute(f'DELETE FROM {t} WHERE doc_id=?',(h,))
            s.commit();return {'name':name,'error':'Recognised as '+m.family_label+' but nothing could be read. Scanned PDFs need OCR, which is not included.'}
        s.conn.execute('UPDATE documents SET filename=? WHERE doc_id=?',(name,h));s.conn.execute('DELETE FROM unrecognized WHERE doc_id=?',(h,));s.commit()
        return {'name':name,'family':m.family_label,'candidates':r.get('candidates',0),'statements':r.get('statements',0)}
    except Exception as exc:
        s.conn.rollback();return {'name':name,'error':str(exc)}

def archive_config(folder,mods):
    archive=folder/'config_archive';archive.mkdir(exist_ok=True)
    for n in list(mods)+['qualitative_common']:
        cfg=(ROOT/'config'/f'{n}.yaml').read_bytes();(archive/(n+'-'+hashlib.sha256(cfg).hexdigest()[:16]+'.yaml')).write_bytes(cfg)

def assign_family(data_dir,module,data):
    """Reviewer proposes a report family and metadata for an unrecognised PDF. Validated by extraction."""
    folder,mods,s=init_context(data_dir,module)
    try:
        if not str(data.get('reviewer','')).strip() or not str(data.get('note','')).strip():raise ValueError('A reviewer name and reason are required')
        u=s.rows('SELECT * FROM unrecognized WHERE doc_id=?',(data.get('doc_id'),))
        if not u:raise ValueError('That file is not in the unrecognised list')
        m=mods.get(data.get('family'))
        if not m:raise ValueError('Choose a configured report family')
        meta=data.get('metadata') or {}
        from .harmonize import parse_date
        for k in ('issuer','period_end','publication_date'):
            if not str(meta.get(k,'')).strip():raise ValueError(f'Metadata {k} is required')
        for k in ('period_start','period_end','publication_date'):
            if meta.get(k) and not parse_date(meta[k]):raise ValueError(f'{k} must be a date (YYYY-MM-DD)')
        if meta.get('period_start') and meta['period_start']>meta['period_end']:raise ValueError('period_start is after period_end')
        dest=(folder/'sources'/f"{u[0]['doc_id']}.pdf").resolve()   # located by hash, so a moved workspace still works
        if not dest.is_relative_to((folder/'sources').resolve()) or sha256(dest)!=u[0]['doc_id']:raise ValueError('Source integrity check failed')
        meta.setdefault('period_start',meta['period_end'])
        r=_ingest_one(s,m,dest,u[0]['doc_id'],u[0]['filename'],meta)
        if 'error' not in r:
            s.decide('document',u[0]['doc_id'],'assign_family',{'family':m.name,'metadata':meta},data['reviewer'],data['note'],POLICY_ID);s.commit()
        refresh(s,mods);return r
    finally:s.conn.close()

class Handler(BaseHTTPRequestHandler):
    data_dir=None;preview=False
    def log_message(self,*args):pass
    def allowed(self):
        host=self.headers.get('Host','').split(':')[0].lower()
        return host in ({'localhost','127.0.0.1','::1','terminal.local'} if self.preview else {'localhost','127.0.0.1','::1'})
    def send(self,status,content,mime='application/json',extra=None):
        if isinstance(content,dict):content=json.dumps(content,ensure_ascii=False).encode()
        elif isinstance(content,str):content=content.encode()
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; connect-src 'self'; base-uri 'none'")
        for k,v in (extra or {}).items():self.send_header(k,v)
        self.end_headers();self.wfile.write(content)
    def context(self):
        q=parse_qs(urlsplit(self.path).query);return q.get('module',['events'])[0],q
    def do_GET(self):
        if not self.allowed():return self.send(403,{'error':'Loopback host required'})
        try:
            module,q=self.context();path=urlsplit(self.path).path
            if path in ('/','/index.html'):
                return self.send(200,(ASSETS/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN),'text/html;charset=utf-8')
            if path in ('/app.js','/public.js','/style.css'):
                return self.send(200,(ASSETS/path[1:]).read_bytes(),'text/javascript' if path.endswith('.js') else 'text/css')
            with LOCK:
                if path=='/api/state':return self.send(200,state(self.data_dir,module))
                if path in ('/api/briefs', '/api/brief_pdf', '/api/brief_evidence'):
                    _, _, store = init_context(self.data_dir, module)
                    try:
                        if path == '/api/briefs':
                            return self.send(200, {'briefs': briefs.list_saved(store)})
                        brief_id = q.get('id', [''])[0]
                        rows = store.rows("SELECT * FROM situation_briefs WHERE brief_id=?", (brief_id,))
                        if not rows:
                            return self.send(404, {'error': 'Saved brief not found.'})
                        record = rows[0]
                        name = f"situation-brief-{record['brief_id'][:12]}"
                        if path == '/api/brief_pdf':
                            return self.send(200, record['pdf'], 'application/pdf',
                                             {'Content-Disposition': f'inline; filename="{name}.pdf"'})
                        return self.send(200, {'evidence_sha256': record['evidence_sha256'],
                                               'brief': json.loads(record['payload'])},
                                         extra={'Content-Disposition': f'attachment; filename="{name}-evidence.json"'})
                    finally:
                        store.conn.close()

                if path in ('/api/page','/api/pdf'):
                    folder,m,s=init_context(self.data_dir,module)
                    try:d=s.rows('SELECT * FROM documents WHERE doc_id=?',(q.get('doc',[''])[0],))
                    finally:s.conn.close()
                    if not d:raise ValueError('Source not found')
                    p=(folder/'sources'/f"{d[0]['doc_id']}.pdf").resolve()   # located by hash, not by the stored absolute path
                    if not p.is_relative_to((folder/'sources').resolve()) or sha256(p)!=d[0]['doc_id']:raise ValueError('Source integrity check failed')
                    if path=='/api/pdf':return self.send(200,p.read_bytes(),'application/pdf',{'Content-Disposition':'inline; filename="source.pdf"'})
                    import pypdfium2 as pdfium
                    page=int(q.get('page',['1'])[0]);pdf=pdfium.PdfDocument(str(p))
                    try:
                        if not 1<=page<=len(pdf):raise ValueError('Invalid page')
                        pg=pdf[page-1]
                        try:
                            bitmap=pg.render(scale=1.3);im=bitmap.to_pil();buf=io.BytesIO();im.save(buf,format='PNG');content=buf.getvalue();im.close();bitmap.close()
                        finally:pg.close()
                    finally:pdf.close()
                    return self.send(200,content,'image/png')
                if path in ('/api/export','/api/export_reconciled','/api/export_statements'):
                    folder,m,s=init_context(self.data_dir,module)
                    try:
                        rules,recon,issues=refresh(s,m);links=EV.item_events(s)
                        meta={'policy':POLICY_ID,'config':config_fingerprint(m),'generated':now(),'workspace':module}
                        titles={e['event_id']:e['title'] for e in s.rows('SELECT event_id,title FROM events')}
                        if path=='/api/export':
                            rows=s.rows("SELECT c.*,d.filename,d.report_no,d.module,d.publication_date,d.doc_id AS source_sha256 FROM candidates c JOIN documents d USING(doc_id) WHERE c.status IN ('approved','corrected') ORDER BY c.id")
                            if not check_sources(folder,{r['doc_id'] for r in rows}):return self.send(409,{'error':'Source integrity check failed; restore the original PDF before export'})
                            sup=recon['supersessions']
                            out=[]
                            for r in rows:
                                conf=[l for l in links.get(('candidate',r['item_key']),[]) if l['status']=='confirmed']
                                r.update(effective_value=effective(r),event_id=conf[0]['event_id'] if conf else '',event_title=titles.get(conf[0]['event_id']) if conf else '',
                                         family=m[r['module']].family_label if r['module'] in m else r['module'],flags=r['flags'],superseded_by=sup.get(r['item_key'],''),
                                         conflict='yes' if any(f in r['flags'] for f in ('conflicting_source','cross_source_difference','revised_in_later_report','cross_table_mismatch')) else 'no')
                                out.append(r)
                            cols=['item_key','event_id','event_title','disease','family','issuer','report_no','period_start','period_end','period_label','geo','geo_level','indicator','classification','count_type','effective_value','status','superseded_by','conflict','flags','filename','source_sha256','page','table_index','row_index','col_index','raw_label','raw_value','reviewer','reviewed_at','review_note']
                            return self.send(200,csv_response(out,cols,meta),'text/csv;charset=utf-8',{'Content-Disposition':'attachment; filename="reviewed_observations.csv"'})
                        if path=='/api/export_reconciled':
                            out=[]
                            docs=set()
                            for x in recon['series']:
                                for t in x['timeline']:
                                    rv=t['reviewed'];base={'event_id':x['event_id'],'event_title':titles.get(x['event_id'],''),'family':x['family'],'issuer':x['issuer'],'geo':x['geo'],'geo_level':x['geo_level'],'indicator':x['indicator'],'classification':x['classification'],'count_type':x['count_type'],'period_kind':x['period_kind'],'period_start':t['period_start'],'period_end':t['period_end'],'period_label':t['period_label']}
                                    if rv:
                                        docs.add(rv['doc_id']);out.append({**base,'reviewed_value':rv['value'],'status':'reviewed','pending_note':t['pending_note'] or '','source_item':rv['item_key'],'source_file':rv['filename'],'page':rv['page']})
                                    elif t['reviewed_conflict']:
                                        out.append({**base,'reviewed_value':'','status':'withheld: reviewed claims conflict','pending_note':'; '.join(f"{c['filename']}={c['value']}" for c in t['reviewed_conflict'])})
                            if not check_sources(folder,docs):return self.send(409,{'error':'Source integrity check failed; restore the original PDF before export'})
                            cols=['event_id','event_title','family','issuer','geo','geo_level','indicator','classification','count_type','period_kind','period_start','period_end','period_label','reviewed_value','status','pending_note','source_item','source_file','page']
                            return self.send(200,csv_response(out,cols,meta),'text/csv;charset=utf-8',{'Content-Disposition':'attachment; filename="reconciled_series.csv"'})
                        rows=s.rows("SELECT st.*,d.filename,d.doc_id AS source_sha256 FROM statements st JOIN documents d USING(doc_id) WHERE st.status IN ('confirmed','retagged') ORDER BY st.id")
                        if not check_sources(folder,{r['doc_id'] for r in rows}):return self.send(409,{'error':'Source integrity check failed; restore the original PDF before export'})
                        issue_of={mm['item_key']:i['issue_id'] for i in issues for mm in i['members']}
                        for r in rows:
                            ls=links.get(('statement',r['item_key']),[])
                            r['event_ids']=[l['event_id'] for l in ls if l['status']=='confirmed'];r['event_ids_proposed']=[l['event_id'] for l in ls if l['status']=='proposed'];r['issue_id']=issue_of.get(r['item_key'],'')
                        cols=['item_key','event_ids','event_ids_proposed','issue_id','issuer','period_end','statement_type','topics','state','disease','geos','health_zones','quote','quantities','dates','caveat','section','pillar','filename','source_sha256','page','status','reviewer','reviewed_at','review_note']
                        return self.send(200,csv_response(rows,cols,meta),'text/csv;charset=utf-8',{'Content-Disposition':'attachment; filename="reviewed_statements.csv"'})
                    finally:s.conn.close()
                if path=='/api/history':
                    folder,m,s=init_context(self.data_dir,module)
                    try:return self.send(200,{'module':module,'policy':POLICY_ID,'events':s.rows('SELECT * FROM review_log ORDER BY id'),'statement_events':s.rows('SELECT * FROM statement_review_log ORDER BY id'),'decisions':s.rows('SELECT * FROM decisions ORDER BY id'),'note':'Ordered review and reconciliation decisions. Full historical replay of outputs is not implemented.'})
                    finally:s.conn.close()
            self.send(404,{'error':'Not found'})
        except Exception as exc:self.send(400,{'error':str(exc)})
    def do_POST(self):
        origin=self.headers.get('Origin')
        if not self.allowed() or self.headers.get('X-Ledger-Token')!=TOKEN or (origin and urlsplit(origin).netloc!=self.headers.get('Host')):return self.send(403,{'error':'Local session validation failed. Reload the application.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=int(MAX_BATCH_MB*1.4)*1024*1024:raise ValueError(f'Upload batch exceeds {MAX_BATCH_MB} MB')
            data=json.loads(self.rfile.read(length));module=data.get('module','events')
            with LOCK:
                path=urlsplit(self.path).path
                if path=='/api/import':return self.send(200,{'results':import_files(self.data_dir,module,data['files'])})
                if path=='/api/demo':
                    from samples.make_samples import build_all
                    folder=Path(self.data_dir)/'demo_fixtures';folder.mkdir(parents=True,exist_ok=True);build_all(str(folder))
                    files=[{'name':p.name,'data':base64.b64encode(p.read_bytes()).decode()} for p in sorted(folder.glob('*.pdf'))]
                    return self.send(200,{'results':import_files(self.data_dir,'synthetic_demo',files)})
                if path == '/api/briefs':
                    snapshot = briefs.capture(state(self.data_dir, module), data['event_id'], data.get('area', ''))
                    folder, _, store = init_context(self.data_dir, module)
                    try:
                        if not check_sources(folder, {doc['doc_id'] for doc in snapshot['sources']}):
                            return self.send(409, {'error': 'Restore the original source PDFs before saving the brief.'})
                        briefs.save(store, snapshot)
                        return self.send(201, {'brief_id': snapshot['brief_id']})
                    finally:
                        store.conn.close()
                if path=='/api/public_note':
                    folder,mods,s=init_context(self.data_dir,module)
                    try:
                        save_public_note(s,data)
                        s.commit()
                    finally:
                        s.conn.close()
                    return self.send(200,{'ok':True})
                if path=='/api/assign_family':return self.send(200,assign_family(self.data_dir,module,data))
                if path in ('/api/review','/api/event','/api/assignment','/api/issue_member','/api/relation','/api/issue_assess','/api/gap_note'):
                    folder,m,s=init_context(self.data_dir,module)
                    try:
                        who,why=data.get('reviewer',''),data.get('note','')
                        if path=='/api/review':
                            s.review(int(data['id']),data['action'],who,why,data.get('value'));s.commit();run_all_rules(s,m)
                        elif path=='/api/event':
                            act=data.get('action')
                            if act=='confirm':EV.confirm_event(s,data['event_id'],who,why,bool(data.get('include_assignments',True)))
                            elif act=='edit':EV.edit_event(s,data['event_id'],data.get('fields') or {},who,why)
                            elif act=='merge':EV.merge_events(s,data['event_id'],data['into'],who,why)
                            elif act=='separate':EV.separate_event(s,data['event_id'],data.get('doc_ids') or [],data.get('title') or 'Separated event',who,why)
                            else:raise ValueError('Unknown event action')
                        elif path=='/api/assignment':EV.set_assignment(s,data['item_kind'],data['item_key'],data['event_id'],data['action'],who,why)
                        elif path=='/api/issue_member':IS.set_membership(s,data['issue_id'],data['statement_key'],data['action'],who,why)
                        elif path=='/api/relation':IS.set_relation(s,data.get('rel_key'),data.get('action'),who,why,data.get('create'))
                        elif path=='/api/issue_assess':IS.assess_issue(s,data['issue_id'],data['assessment'],who,why)
                        elif path=='/api/gap_note':
                            if data.get('status') not in ('not_issued','clear'):raise ValueError('Unknown gap status')
                            s.decide('gap',f"{data['module_family']}:{data['issuer']}:{data['report_no']}",data['status'],{},who,why,POLICY_ID)
                            if data['status']=='clear':s.conn.execute('DELETE FROM gap_notes WHERE module=? AND issuer=? AND report_no=?',(data['module_family'],data['issuer'],int(data['report_no'])))
                            else:s.conn.execute('INSERT OR REPLACE INTO gap_notes VALUES (?,?,?,?,?,datetime(\'now\'),?)',(data['module_family'],data['issuer'],int(data['report_no']),'not_issued',who.strip(),why.strip()))
                            s.commit()
                    finally:s.conn.close()
                    return self.send(200,{'ok':True})
                if path=='/api/statement_review':
                    folder,m,s=init_context(self.data_dir,module)
                    try:
                        s.review_statement(int(data['id']),data['action'],data.get('reviewer',''),data.get('note',''),data.get('tags'));s.commit()
                    finally:s.conn.close()
                    return self.send(200,{'ok':True})
            self.send(404,{'error':'Not found'})
        except Exception as exc:self.send(400,{'error':str(exc)})

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=int(os.environ.get('PORT','8765')));p.add_argument('--data',default=str(ROOT/'local_data'));p.add_argument('--open',action='store_true');p.add_argument('--preview',action='store_true');a=p.parse_args()
    Handler.data_dir=a.data;Handler.preview=a.preview
    host='0.0.0.0' if a.preview else '127.0.0.1';server=ThreadingHTTPServer((host,a.port),Handler)
    print(f'Outbreak Ledger local pilot: http://127.0.0.1:{a.port}',flush=True);print('Keep this window open. Press Ctrl+C to stop. Data stays in '+str(Path(a.data).resolve()),flush=True)
    if a.open:threading.Timer(.8,lambda:webbrowser.open(f'http://127.0.0.1:{a.port}')).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()

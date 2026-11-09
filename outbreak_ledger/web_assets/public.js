'use strict';

// Reader labels are explicit. Source wording and extraction codes stay in the review workspace.
(() => {
    const labels = {
        BVD: 'Bundibugyo virus disease', drc: 'Democratic Republic of the Congo',
        demo_country: 'Example country', 'nord-kivu': 'North Kivu', 'sud-kivu': 'South Kivu',
        'haut-uele': 'Haut-Uele', 'bas-uele': 'Bas-Uele',
        outbreak_episode: 'Outbreak', surveillance_stream: 'Routine surveillance',
        public_health_signal: 'Public health signal',
        approved: 'Reviewed', corrected: 'Reviewed with correction', candidate: 'Awaiting review',
        confirmed: 'Confirmed', retagged: 'Reviewed with corrections', rejected: 'Excluded after review',
        proposed: 'Awaiting confirmation', evidenced: 'Revision stated in the report',
        unresolved: 'Figures disagree', unknown: 'Not identified', unassigned: 'Event to be confirmed',
        supersedes: 'Revises an earlier figure', updates: 'Updates an earlier report',
        addresses: 'Action addressing the problem', reports_resolution: 'Resolution reported',
        contradicts: 'Conflicting accounts', recurs: 'Problem reported again',
        challenge: 'Operational challenge', action: 'Response action', recommendation: 'Recommendation',
        assessment: 'Source assessment', signal: 'Public health signal', published_product: 'Published guidance',
        observation: 'Reported update', ongoing: 'Reported ongoing', resolved: 'Reported resolved',
        persisting: 'Reported ongoing', denied: 'Reported absent', new: 'Newly reported',
        laboratory: 'Laboratory services', lab_capacity: 'Laboratory capacity',
        supply_shortage: 'Supply shortage', ipc_wash: 'Infection prevention, water and sanitation',
        risk_communication: 'Risk communication and community engagement',
        data_reporting: 'Reporting and data quality', poe_screening: 'Screening at points of entry',
        acute_flaccid_paralysis: 'Acute flaccid paralysis', rdt_positive: 'Positive rapid diagnostic test',
        idsr_notified: 'Reported through routine surveillance', unspecified: 'Case classification not stated',
        unspecified_label: 'Case classification not stated', covid19: 'COVID-19',
        diarrhoea_dehydration_u5: 'Diarrhoea with dehydration in children under five',
        national: 'National', province: 'Province', health_zone: 'Health zone', aggregate: 'Combined area',
        case_management: 'Case management', community_resistance: 'Community concerns and acceptance',
        transport_logistics: 'Transport and logistics', safe_burials: 'Safe burials',
        silent_zones: 'Areas without notifications', points_of_entry: 'Points of entry',
        health_worker_infection: 'Infections among health workers',
        public_note: 'English response note', assign_family: 'Report type assigned'
    };
    const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
    const human = value => {
        if (!value) return 'Not specified';
        if (own(labels, value)) return labels[value];
        const name = String(value).replace(/_/g, ' ');
        return name[0].toUpperCase() + name.slice(1);
    };
    window.readerLabel = human;
    const organisations = {
        'INSP-DRC': 'National Institute of Public Health, DRC',
        'INSP-RDC': 'National Institute of Public Health, DRC',
        'DRC-INSP': 'National Institute of Public Health, DRC',
        WHO: 'World Health Organization', WHO_WER: 'World Health Organization',
        'MSP-DEMO': 'Illustrative health authority', 'AGENCY-DEMO': 'Illustrative reporting agency'
    };
    const families = {
        bvd_drc_insp: 'DRC Ebola situation reports',
        drc_insp_weekly_profile: 'DRC weekly surveillance reports',
        who_wer: 'WHO Weekly Epidemiological Record',
        synthetic_demo: 'Illustrative situation reports'
    };
    const classifications = {
        confirmed: 'confirmed', probable: 'probable', suspected: 'suspected',
        idsr_notified: 'notified', rdt_positive: 'with a positive rapid test',
        unspecified: '', unspecified_label: ''
    };
    const measures = {cases: 'cases', deaths: 'deaths'};
    const counts = {new: 'New', cumulative: 'Cumulative'};
    const numeric = new Intl.NumberFormat('en-GB');
    const calendar = new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
    let selectedEvent = '';
    let area = '';
    const date = input => {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(input || '')) return 'Date not recorded';
        const value = new Date(input + 'T00:00:00Z');
        return Number.isNaN(value.valueOf()) ? 'Date not recorded' : calendar.format(value);
    };
    const period = claim => claim.period_start && claim.period_start !== claim.period_end
        ? date(claim.period_start) + ' to ' + date(claim.period_end) : date(claim.period_end);
    const valuePeriod = (series, claim) => series.period_kind === 'snapshot'
        ? 'As of ' + date(claim.period_end) : period(claim);
    const source = claim => {
        if (!claim || !claim.doc_id) return '';
        const page = Number(claim.page) || 0;
        const base = '/api/' + (page ? 'page' : 'pdf');
        const path = base + '?module=' + encodeURIComponent(module) + '&doc=' + encodeURIComponent(claim.doc_id)
            + (page ? '&page=' + page : '');
        const name = Number.isInteger(claim.report_no) ? 'Report ' + claim.report_no : 'Source report';
        return '<a class="source-link" href="' + e(path) + '" target="_blank" rel="noopener">'
            + e(name + (page ? ', page ' + page : ' (original PDF)')) + '</a>';
    };
    const family = name => families[name] || 'Other reporting series';
    const issuer = value => own(organisations,value) ? organisations[value] :
        value && /^[a-zA-Z][a-zA-Z0-9 .&-]{1,70}$/.test(value) ? human(value) : 'Issuing organisation not specified';
    const areaName = value => /^(?:total|national total)$/i.test(value || '') ? 'All areas in this report' : human(value);
    const measure = series => [counts[series.count_type], classifications[series.classification], measures[series.indicator]]
        .filter(Boolean).join(' ');
    const isSupported = series => own(measures,series.indicator) && own(counts,series.count_type) && own(classifications,series.classification);
    const usable = claim => claim && claim.event_link === 'confirmed' && ['approved','corrected'].includes(claim.status)
        && typeof claim.value === 'number' && Number.isFinite(claim.value);
    const empty = content => '<div class="reader-empty">' + content + '</div>';
    const details = (title,content) => '<details class="reader-details"><summary>' + e(title) + '</summary>' + content + '</details>';

    function eventTitle(event) {
        return event.title && !/[_{}<>]/.test(event.title) ? event.title
            : human(event.disease) + ' · ' + human(event.event_type);
    }

    function currentSeries(event) {
        return event.series.filter(isSupported).filter(series => !area || series.geo === area)
            .map(series => {
                const eligible = series.timeline.filter(point => usable(point.reviewed));
                return {series, eligible, latest:eligible.at(-1), current:series.timeline.at(-1)};
            });
    }

    function eligibleNotes(event) {
        const byKey = new Map(data.statements.map(statement => [statement.item_key,statement]));
        return (data.public_notes || []).filter(note => note.event_id === event.event_id)
            .map(note => ({...note, statement:byKey.get(note.item_key)}))
            .filter(note => {
                const statement = note.statement;
                return statement && ['confirmed','retagged'].includes(statement.status)
                    && statement.reviewed_at === note.statement_reviewed_at
                    && statement.events.some(link => link.event_id === event.event_id && link.status === 'confirmed');
            }).sort((a,b) => (b.statement.period_end || '').localeCompare(a.statement.period_end || '') ||
                a.item_key.localeCompare(b.item_key));
    }

    function seriesCard(row) {
        const {series, latest, current} = row;
        const claim = latest.reviewed;
        const classificationMissing = ['unspecified','unspecified_label'].includes(series.classification);
        const newer = latest !== current ? 'A later reporting period has no reviewed figure.' : '';
        const conflict = current.state === 'unresolved' || current.reviewed_conflict.length
            ? 'Figures for the latest period disagree.' : '';
        const pending = latest.pending_note ? 'A later claim for this period awaits review.' : '';
        const notice = [newer,conflict,pending].filter(Boolean).join(' ');
        return '<article class="reader-card"><p class="reader-kicker">' + e(areaName(series.geo))
            + '</p><h3>' + e(measure(series)) + '</h3><div class="reader-number">' + numeric.format(claim.value)
            + '</div><p>' + e(valuePeriod(series,claim)) + '</p><p class="reader-muted">' + e(issuer(series.issuer))
            + ' · ' + e(family(series.module)) + '</p>'
            + (classificationMissing ? '<p class="reader-muted">The source does not state a case classification.</p>' : '')
            + (notice ? '<p class="reader-notice">' + e(notice) + '</p>' : '') + source(claim) + '</article>';
    }

    function renderOverview(event, rows, notes) {
        const reviewed = rows.filter(row => row.latest);
        const docs = new Set(data.candidates.filter(c => c.events.some(l => l.event_id === event.event_id && l.status === 'confirmed') && c.status !== 'rejected').map(c=>c.doc_id));
        for (const item of notes) docs.add(item.statement.doc_id);
        const pending = rows.some(row => !row.latest || row.latest !== row.current || row.current.state === 'unresolved' || row.latest.pending_note);
        const disagreements = data.cross_issuer.filter(item => item.event_id === event.event_id && (!area || item.geo === area));
        const areas = [...new Set(event.series.filter(isSupported).map(row => row.geo))].sort((a,b)=>areaName(a).localeCompare(areaName(b)));
        $('#reader-overview').innerHTML = '<div class="reader-event"><div><p class="reader-kicker">' + e(human(event.event_type)) + '</p><h2>'
            + e(eventTitle(event)) + '</h2><p>' + e((event.geo_scope || []).map(areaName).join(', ') || 'Area not specified')
            + '</p></div><div class="brief-action"><span class="reader-reviewed">Event confirmed</span>'
            + '<button class="secondary" id="save-situation-brief">Save one-page brief</button></div></div>'
            + '<div class="reader-metrics"><div><b>' + numeric.format(docs.size) + '</b> linked reports</div><div><b>'
            + numeric.format(reviewed.length) + '</b> reporting series with reviewed figures</div><div><b>'
            + numeric.format(notes.length) + '</b> English response notes</div></div>'
            + '<div class="reader-section-title"><div><h3>Latest reviewed figures</h3><p>Each figure retains its area, reporting period and source.</p></div>'
            + (areas.length ? '<label>Area <select id="reader-area"><option value="">All areas</option>'
            + areas.map(value=>'<option value="' + e(value) + '"' + (area===value?' selected':'') + '>' + e(areaName(value)) + '</option>').join('')
            + '</select></label>' : '') + '</div>'
            + (pending ? '<p class="reader-notice">Some reporting series have figures awaiting review or conflicting values. Open the review workspace to examine them.</p>' : '')
            + (disagreements.length ? '<p class="reader-notice">Reporting organisations give different figures for at least one comparable period. Figures are displayed by source.</p>' : '')
            + (reviewed.length ? '<div class="reader-grid">' + reviewed.map(seriesCard).join('') + '</div>'
                : empty('Reviewed figures will appear here after their source values are approved.'))
            + '<div class="reader-section-title"><div><h3>Latest response notes</h3><p>English summaries checked against the original report.</p></div>'
            + '<button class="secondary" id="see-response">See response updates</button></div>'
            + (notes.length ? '<div class="reader-grid">' + notes.slice(0,3).map(noteCard).join('') + '</div>'
                : empty('No reviewed English response notes are available for this event.'));
        if ($('#reader-area')) $('#reader-area').onchange = ev => { area = ev.target.value; window.renderReader(); };
        $('#see-response').onclick = () => setTab('response');
        const save = $('#save-situation-brief');
        save.onclick = async () => {
            save.disabled = true;
            msg('');
            try {
                await api('/api/briefs', {module, event_id:event.event_id, area});
                setTab('briefs');
                msg('Situation brief saved.');
            } catch (error) {
                msg(error.message,true);
            } finally {
                save.disabled = false;
            }
        };
    }

    function renderChanges(rows) {
        const sections = rows.filter(row=>row.eligible.length).map(({series,eligible,current})=>{
            const last = eligible.at(-1);
            const before = eligible.at(-2);
            let explanation = 'One reviewed period is available.';
            if (before && series.count_type === 'cumulative') {
                const diff = last.reviewed.value - before.reviewed.value;
                explanation = 'Net change in the reported cumulative figure: ' + (diff > 0 ? '+' : '') + numeric.format(diff)
                    + ' from ' + date(before.period_end) + ' to ' + date(last.period_end) + '. This difference may include revisions.';
            } else if (before) {
                explanation = 'The latest two reviewed periods report ' + numeric.format(before.reviewed.value) + ' and '
                    + numeric.format(last.reviewed.value) + '. Each figure applies to its own period.';
            }
            const coverage = series.gaps.length ? '<p class="reader-notice">There are gaps between available reporting periods. Coverage between these dates is incomplete or unknown.</p>' : '';
            const tableRows = eligible.slice().reverse().map(point=>'<tr><td>' + e(valuePeriod(series,point.reviewed)) + '</td><td>'
                + numeric.format(point.reviewed.value) + '</td><td>' + source(point.reviewed) + '</td></tr>').join('');
            const revisions = eligible.flatMap(point => (point.superseded || []).filter(previous =>
                usable(previous) && previous.value !== point.reviewed.value &&
                data.relations.some(link => link.kind === 'supersedes' &&
                    ['confirmed','evidenced'].includes(link.status) &&
                    link.from_key === point.reviewed.item_key && link.to_key === previous.item_key)).map(previous =>
                '<p>' + e(date(point.period_end)) + ' · revised from ' + numeric.format(previous.value) + ' to '
                + numeric.format(point.reviewed.value) + '. Earlier: ' + source(previous) + ' · Revised: ' + source(point.reviewed) + '</p>')).join('');
            return '<article class="reader-change"><h3>' + e(measure(series)) + ' · ' + e(areaName(series.geo)) + '</h3><p class="reader-muted">'
                + e(issuer(series.issuer)) + ' · ' + e(family(series.module)) + '</p><p>' + e(explanation) + '</p>'
                + coverage + (last!==current ? '<p class="reader-notice">A later period awaits a reviewed figure.</p>' : '')
                + (revisions ? details('Earlier figures and revisions',revisions) : '')
                + details('View reviewed figures by period','<div class="table-scroll"><table><thead><tr><th>Reporting period</th><th>Reviewed figure</th><th>Source</th></tr></thead><tbody>'
                    + tableRows + '</tbody></table></div>') + '</article>';
        });
        $('#reader-changes').innerHTML = sections.join('') || empty('Review figures from successive reports to build a dated record of changes.');
    }

    function noteCard(note, extra='') {
        const statement = note.statement;
        return '<article class="reader-card"><p class="reader-kicker">' + e(human(statement.statement_type)) + ' · ' + e(date(statement.period_end))
            + '</p><h3>' + e(statement.pillar ? human(statement.pillar) : 'Response update') + '</h3><p>' + e(note.summary)
            + '</p>' + (extra ? '<div class="reader-link-note">' + extra + '</div>' : '') + source(statement) + '</article>';
    }

    function renderResponse(notes) {
        const byKey = new Map(notes.map(note => [note.item_key,note]));
        $('#reader-response').innerHTML = notes.length ? '<div class="reader-grid">' + notes.map(note=>{
            const related = data.relations.filter(link=>link.status==='confirmed' && link.from_key===note.item_key
                && byKey.has(link.to_key) && link.kind!=='supersedes').map(link=>{
                    const prior = byKey.get(link.to_key);
                    return details(human(link.kind) + ' · ' + date(prior.statement.period_end),
                        '<p>' + e(prior.summary) + '</p>' + source(prior.statement));
                }).join('');
            return noteCard(note,related);
        }).join('') + '</div>' : empty('A reviewer can add an English note after checking a statement and confirming its event. Original excerpts remain available in the review workspace.');
    }

    function renderSources(event, notes) {
        const ids = new Set(data.candidates.filter(c=>c.status!=='rejected' && c.events.some(link=>link.event_id===event.event_id && link.status==='confirmed')).map(c=>c.doc_id));
        data.statements.filter(s=>s.status!=='rejected' && s.events.some(link=>link.event_id===event.event_id && link.status==='confirmed')).forEach(s=>ids.add(s.doc_id));
        for (const note of notes) ids.add(note.statement.doc_id);
        const docs = data.documents.filter(doc=>ids.has(doc.doc_id)).slice().sort((a,b)=>(b.period_end||'').localeCompare(a.period_end||''));
        $('#reader-sources').innerHTML = docs.length ? '<div class="reader-grid">' + docs.map(doc=>'<article class="reader-card"><p class="reader-kicker">'
            + e(family(doc.module)) + '</p><h3>' + e(Number.isInteger(doc.report_no) ? 'Report ' + doc.report_no : 'Source report')
            + '</h3><p>Reporting period · ' + e(period(doc)) + '</p><p class="reader-muted">' + e(issuer(doc.issuer))
            + (doc.publication_date ? ' · Published ' + e(date(doc.publication_date)) : '') + '</p>' + source(doc) + '</article>').join('')
            + '</div>' : empty('Source reports will appear once their connection to this event is confirmed.');
    }

    window.loadBriefs = async () => {
        const list = $('#saved-briefs');
        const workspace = module;
        list.textContent = 'Loading saved briefs…';
        try {
            const result = await api('/api/briefs?module=' + encodeURIComponent(workspace));
            if (workspace !== module) return;
            const mode = workspace === 'synthetic_demo' ? 'Practice workspace' : 'Outbreak workspace';
            list.innerHTML = '<p class="reader-muted">' + e(mode) + '</p>' +
                (result.briefs.length ? '<div class="reader-grid">' + result.briefs.map(brief => {
                    const query = '?module=' + encodeURIComponent(workspace) + '&id=' + encodeURIComponent(brief.brief_id);
                    return '<article class="reader-card"><p class="reader-kicker">Saved '
                        + e(date(brief.saved_at.slice(0,10))) + ' at ' + e(brief.saved_at.slice(11,16)) + ' UTC</p><h3>'
                        + e(brief.title) + '</h3><p>' + e(brief.scope)
                        + '</p><div class="brief-links"><a class="source-link" target="_blank" rel="noopener" href="/api/brief_pdf'
                        + e(query) + '">Open one-page PDF</a><a class="source-link" href="/api/brief_evidence'
                        + e(query) + '">Download evidence record</a></div></article>';
                }).join('') + '</div>' : empty('Save a one-page brief from the overview of a confirmed event.'));
        } catch (error) {
            if (workspace === module) list.textContent = error.message;
        }
    };

    window.renderReader = () => {
        if (!data) return;
        const events = data.events_list.filter(event=>event.status==='confirmed');
        if (!events.some(event=>event.event_id===selectedEvent)) { selectedEvent = events[0]?.event_id || ''; area=''; }
        const event = events.find(event=>event.event_id===selectedEvent);
        $('#reader-mode').textContent = module==='synthetic_demo' ? 'Practice data · illustrative only' : 'Local pilot';
        const select = $('#reader-event');
        select.innerHTML = events.length ? events.map(item=>'<option value="' + e(item.event_id) + '">'
            + e(eventTitle(item) + ' · ' + human(item.event_type)) + '</option>').join('')
            : '<option>No confirmed event available</option>';
        select.disabled = !events.length;
        select.value = selectedEvent || select.options[0]?.value || '';
        select.onchange = () => { selectedEvent=select.value; area=''; window.renderReader(); };
        if (!event) {
            for (const id of ['overview','changes','response','sources']) {
                $('#reader-'+id).innerHTML = empty('<h2>Reviewed evidence will appear here</h2><p>Add a report, confirm its event and review its findings in the workspace.</p><button class="primary" data-open-reports>Add or manage reports</button>');
            }
            document.querySelectorAll('[data-open-reports]').forEach(button=>button.onclick=()=>setTab('sources'));
            return;
        }
        const notes = eligibleNotes(event);
        renderOverview(event,currentSeries(event),notes);
        renderChanges(currentSeries(event));
        renderResponse(notes);
        renderSources(event,notes);
    };
    window.renderReader();
})();

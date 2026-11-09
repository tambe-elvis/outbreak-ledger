"""Self-contained HTML audit report: every number shown with its source."""
import html
import json

from .indicators import coverage, new_confirmed_by_period, time_since_last_confirmed

CSS = """
:root{--bg:#fff;--fg:#1d1d1b;--muted:#5f5e5a;--line:#d3d1c7;--card:#f6f5f0;--flag:#a32d2d;--ok:#3b6d11;--warn:#854f0b;color-scheme:light}
@media (prefers-color-scheme: dark){:root{--bg:#1d1d1b;--fg:#ecebe4;--muted:#b4b2a9;--line:#444441;--card:#2c2c2a;--flag:#f09595;--ok:#97c459;--warn:#ef9f27;color-scheme:dark}}
body{font-family:system-ui,Arial,sans-serif;background:var(--bg);color:var(--fg);max-width:1100px;margin:0 auto;padding:24px;line-height:1.5}
h1{font-size:24px;margin-bottom:4px}h2{font-size:18px;margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:4px}
p.muted,.muted{color:var(--muted);font-size:14px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}th{background:var(--card)}
.flag{color:var(--flag);font-weight:600}.ok{color:var(--ok)}.warn{color:var(--warn)}
.cards{display:flex;gap:12px;flex-wrap:wrap}.card{background:var(--card);border-radius:10px;padding:10px 14px;min-width:150px}
.card b{display:block;font-size:22px}code{font-size:12px}
"""


def esc(x):
    return html.escape("" if x is None else str(x))


def table(headers, rows):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>'


def build_report(store, out_path, evaluation=None, rule_summary=None, level="health_zone", title=""):
    docs = store.rows("SELECT * FROM documents ORDER BY issuer, period_end")
    dname = {d["doc_id"]: f"{d['issuer']} #{d['report_no']}" for d in docs}
    cands = store.rows("SELECT * FROM candidates ORDER BY id")
    flagged = [c for c in cands if json.loads(c["flags"])]
    status_count = {}
    for c in cands:
        status_count[c["status"]] = status_count.get(c["status"], 0) + 1

    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
             f"<title>Outbreak Ledger audit report</title><style>{CSS}</style></head><body>"]
    parts.append("<h1>Outbreak Ledger audit report</h1>")
    parts.append(f"<p class='muted'>Prototype v0.2. {esc(title)} AI reads, rules compute, humans verify. "
                 "Indicators use human-approved values only.</p>")
    cards = [("Documents", len(docs)), ("Candidate values", len(cands)), ("Flagged for review", len(flagged)),
             ("Approved", status_count.get("approved", 0) + status_count.get("corrected", 0)),
             ("Awaiting review", status_count.get("candidate", 0))]
    if evaluation:
        cards += [("Recall", evaluation["recall"]), ("Precision", evaluation["precision"]),
                  ("Provenance verified", f"{evaluation['provenance_verified']}/{evaluation['provenance_checked']}")]
    parts.append("<div class='cards'>" + "".join(f"<div class='card'><b>{esc(v)}</b>{esc(k)}</div>" for k, v in cards) + "</div>")

    if rule_summary and rule_summary.get("findings"):
        parts.append("<h2>Key findings from rule checks</h2>")
        parts.append("<p class='muted'>Breaks in cumulative series, inconsistencies between new and cumulative counts, "
                     "and disagreements between tables. Each is a question for a reviewer, not a conclusion.</p>")
        seen, rows = set(), []
        for f in rule_summary["findings"]:
            k = (f["type"], f["candidate"])
            if k in seen:
                continue
            seen.add(k)
            c = next((x for x in cands if x["id"] == f["candidate"]), None)
            rows.append([esc(f["period_end"]), esc(f["geo"]), esc(c["geo_level"] if c else ""),
                         esc(f"{c['indicator']} {c['classification']} {c['count_type']}" if c else ""),
                         esc(f["type"]), esc(c["flag_notes"] if c else f["note"]), esc(f["candidate"])])
        parts.append(table(["Period", "Geography", "Level", "Meaning", "Finding", "Explanation", "Candidate"], rows))

    parts.append("<h2>Source documents</h2>")
    parts.append(table(["Issuer", "Report", "Period", "Published", "File", "SHA-256 (first 16)", "Metadata flags"],
                       [[esc(d["issuer"]), esc(d["report_no"]), f"{esc(d['period_start'])} to {esc(d['period_end'])}",
                         esc(d["publication_date"]), esc(d["filename"]), f"<code>{esc(d['doc_id'][:16])}</code>",
                         esc(d["metadata_flags"] or "none")] for d in docs]))

    parts.append("<h2>Values flagged for human review</h2>")
    parts.append("<p class='muted'>Flags come from rule checks. They never change a value; a reviewer decides.</p>")
    def loc(c):
        if c["table_index"] == -1:
            return f"p{c['page']} line {c['row_index']} token {c['col_index']}"
        return f"p{c['page']} t{c['table_index']} r{c['row_index']} c{c['col_index']}"
    parts.append(table(["ID", "Source", "Location", "Raw label", "Raw value", "Source line", "Interpreted as", "Flags",
                        "Why", "Status"],
                       [[esc(c["id"]), esc(dname[c["doc_id"]]), esc(loc(c)), esc(c["raw_label"]), esc(c["raw_value"]),
                         f"<code>{esc(c.get('source_text') or '')}</code>",
                         esc(f"{c['geo']} ({c['geo_level']}) | {c['indicator']} | {c['classification']} | {c['count_type']}"),
                         f"<span class='flag'>{esc(', '.join(json.loads(c['flags'])))}</span>",
                         esc(c.get("flag_notes") or ""), esc(c["status"])]
                        for c in flagged]))

    if rule_summary and rule_summary.get("conflicts"):
        parts.append("<h2>Conflicting sources (retained, not resolved)</h2>")
        rows = []
        for cf in rule_summary["conflicts"]:
            disease, geo, level, ind, cls, ct, period = cf["key"]
            claims = "; ".join(f"{iss}: {val} (candidate {cid})" for iss, val, cid in cf["claims"])
            rows.append([esc(geo), esc(f"{ind} {cls} {ct}"), esc(period), esc(claims)])
        parts.append(table(["Geography", "Meaning", "Period end", "Competing claims"], rows))

    notes = store.rows("SELECT * FROM revision_notes")
    parts.append("<h2>Revisions stated in report text</h2>")
    parts.append(table(["Source", "Page", "Geography", "Kind", "From", "To", "Source wording (normalized)"],
                       [[esc(dname[n["doc_id"]]), esc(n["page"]), esc(n["geo"]), esc(n.get("kind")), esc(n["old_value"]),
                         esc(n["new_value"]), esc(n["quote"])] for n in notes]) if notes else "<p class='muted'>None.</p>")

    ctx = store.rows("SELECT * FROM context")
    parts.append("<h2>Contextual evidence for human interpretation</h2>")
    parts.append("<p class='muted'>Keyword-detected statements with a heuristic state label. "
                 "They do not feed indicators or priority scores.</p>")
    parts.append(table(["Source", "Page", "Topic", "State (heuristic)", "Provinces named", "Source wording", "Date note"],
                       [[esc(dname[c["doc_id"]]), esc(c["page"]), esc(c["topic"]), esc(c["state"]), esc(c.get("geo")),
                         esc(c["quote"]), esc(c["event_date_note"])] for c in ctx]))

    parts.append("<h2>Reporting coverage: not reported is not zero</h2>")
    cov = coverage(store, level)
    parts.append(table(["Issuer", "Report", "Period end", "Geography", "Status"],
                       [[esc(r["issuer"]), esc(r["report_no"]), esc(r["period_end"]), esc(r["geo"]),
                         f"<span class='{'ok' if r['status'] == 'reported' else 'warn'}'>{esc(r['status'])}</span>"]
                        for r in cov]))

    for issuer in sorted({d["issuer"] for d in docs}):
        parts.append(f"<h2>Indicators: {esc(issuer)}</h2>")
        parts.append(f"<p class='muted'>New confirmed cases per reporting period at {esc(level)} level "
                     "(no incidence: no population denominators configured).</p>")
        parts.append(table(["Period end", "Geography", "New confirmed", "Basis"],
                           [[esc(r["period_end"]), esc(r["geo"]), esc(r["value"]), esc(r["basis"])]
                            for r in new_confirmed_by_period(store, issuer, level)]))
        parts.append(table(["Geography", "Days since last positive period", "Basis"],
                           [[esc(r["geo"]), esc(r["result"]), esc(r["basis"])]
                            for r in time_since_last_confirmed(store, issuer, level)]))

    if evaluation:
        parts.append("<h2>Evaluation against reference dataset</h2>")
        parts.append(f"<pre>{esc(json.dumps(evaluation, indent=2, default=str))}</pre>")

    log = store.rows("SELECT * FROM review_log ORDER BY id")
    parts.append("<h2>Review log</h2>")
    parts.append(table(["Candidate", "Action", "From", "To", "Value", "Reviewer", "Note", "At"],
                       [[esc(r["candidate_id"]), esc(r["action"]), esc(r["old_status"]), esc(r["new_status"]),
                         esc(r["value"]), esc(r["reviewer"]), esc(r["note"]), esc(r["at"])] for r in log]))
    parts.append("</body></html>")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("".join(parts))
    return out_path

"""Command line interface: python -m outbreak_ledger <command>."""
import argparse
import csv
import glob
import json
import os
import sys

from .evaluate import evaluate, score_review_sheet, write_review_sheet
from .extract import ingest
from .harmonize import Module
from .report import build_report
from .rules import run_rules
from .store import Store
from .views import select_claims

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cmd_ingest(a):
    m, s = Module(a.config), Store(a.db)
    for p in a.files:
        print(json.dumps(ingest(p, m, s)))


def cmd_check(a):
    r = run_rules(Store(a.db), Module(a.config))
    print(f"Checked {r['checked']} candidates; {r['flagged']} flagged; {len(r['conflicts'])} conflicts retained.")


def cmd_review(a):
    s = Store(a.db)
    rows = s.rows("SELECT id, geo, indicator, classification, count_type, value, raw_label, page, flags, status "
                  "FROM candidates WHERE status='candidate' AND flags!='[]' ORDER BY id")
    for r in rows:
        print(f"[{r['id']}] p{r['page']} {r['geo']} '{r['raw_label']}' = {r['value']} "
              f"({r['indicator']}/{r['classification']}/{r['count_type']}) flags={r['flags']}")
    print(f"{len(rows)} flagged candidates awaiting a decision.")


def cmd_approve_clean(a):
    s = Store(a.db)
    rows = s.rows("SELECT id FROM candidates WHERE status='candidate' AND flags='[]'")
    for r in rows:
        s.review(r["id"], "approve", a.reviewer, "batch approval of rule-passed values")
    s.commit()
    print(f"Approved {len(rows)} rule-passed candidates as {a.reviewer}. Flagged values were not touched.")


def cmd_decide(a):
    s = Store(a.db)
    s.review(a.id, a.action, a.reviewer, a.note or "", a.value)
    s.commit()
    print(f"Candidate {a.id}: {a.action} by {a.reviewer}.")


def cmd_export(a):
    s = Store(a.db)
    rows = s.rows("SELECT c.*, d.filename, d.report_no FROM candidates c JOIN documents d USING(doc_id) "
                  "WHERE c.status IN ('approved','corrected') ORDER BY c.issuer, c.period_end, c.geo")
    rows, conflicts = select_claims(rows)
    if conflicts:
        raise ValueError("Conflicting approved claims must be resolved before export")
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["issuer", "report_no", "period_end", "geo", "indicator", "classification", "count_type",
                    "value", "status", "source_file", "page", "table", "row", "col", "raw_label", "raw_value", "reviewer"])
        for r in rows:
            v = r["corrected_value"] if r["status"] == "corrected" else r["value"]
            w.writerow([r["issuer"], r["report_no"], r["period_end"], r["geo"], r["indicator"], r["classification"],
                        r["count_type"], v, r["status"], r["filename"], r["page"], r["table_index"], r["row_index"],
                        r["col_index"], r["raw_label"], r["raw_value"], r["reviewer"]])
    print(f"Exported {len(rows)} approved observations to {a.out}")


def cmd_evaluate(a):
    print(json.dumps(evaluate(Store(a.db), a.reference), indent=2, default=str))


def cmd_report(a):
    s, m = Store(a.db), Module(a.config)
    rs = run_rules(s, m)
    ev = evaluate(s, a.reference) if a.reference else None
    print(f"Report written to {build_report(s, a.out, ev, rs, m.coverage_level, m.name)}")


def cmd_demo(a):
    """End-to-end run on the synthetic sample corpus."""
    from samples.make_samples import REPORTS, build_all
    from .events import confirm_event, propose_all

    reports_dir = os.path.join(a.outdir, "practice_reports")
    ref = build_all(reports_dir)
    os.makedirs(a.outdir, exist_ok=True)
    db = os.path.join(a.outdir, "demo.db")
    if os.path.exists(db):
        os.remove(db)
    m, s = Module(os.path.join(HERE, "config", "synthetic_demo.yaml")), Store(db)
    print("1. Ingest")
    for p in sorted(os.path.join(reports_dir, report["file"]) for report in REPORTS):
        r = ingest(p, m, s)
        print(f"   {r['file']}: {r['candidates']} candidate values, {r['issuer']} #{r['report_no']}")
    print("2. Rule checks")
    rs = run_rules(s, m)
    print(f"   {rs['flagged']} of {rs['checked']} flagged; {len(rs['conflicts'])} cross-source conflicts retained")
    print("3. Simulated review of fictional reports")
    propose_all(s, {"synthetic_demo": m})
    for event in s.rows("SELECT event_id FROM events WHERE status='proposed' ORDER BY event_id"):
        confirm_event(s, event["event_id"], "demo-reviewer", "Scripted confirmation of fictional event.")
    n = len(s.rows("SELECT id FROM candidates WHERE status='candidate' AND flags='[]'"))
    for r in s.rows("SELECT id FROM candidates WHERE status='candidate' AND flags='[]'"):
        s.review(r["id"], "approve", "demo-reviewer", "rule-passed")
    for r in s.rows("SELECT id, flags FROM candidates WHERE status='candidate'"):
        f = json.loads(r["flags"])
        if "explained_by_revision_note" in f:
            s.review(r["id"], "approve", "demo-reviewer",
                     "decrease matches revision stated in report text; competing pre-revision claim retained")
    s.commit()
    print(f"   approved {n} rule-passed values; approved explained revisions; left other flags pending")
    print("4. Evaluate, export and report")
    ev = evaluate(s, ref)
    print(f"   recall {ev['recall']}, precision {ev['precision']}, provenance {ev['provenance_verified']}/{ev['provenance_checked']}")
    a2 = argparse.Namespace(db=db, out=os.path.join(a.outdir, "approved_observations.csv"))
    cmd_export(a2)
    print("   " + build_report(s, os.path.join(a.outdir, "audit_report.html"), ev, rs, m.coverage_level,
                             "Synthetic demo corpus."))


AUTO_FAMILIES = ["bvd_drc_insp", "drc_insp_weekly_profile", "who_wer"]


def cmd_run(a):
    """Full pipeline on a folder of reports of any supported family, routed automatically.

    Writes: ledger.db, review_sheet.csv, approved_observations.csv,
    statements.csv, issue_threads.csv, findings.csv, unrecognised.csv and
    audit_report.html. No values are approved unless --approve-clean-as is given.
    """
    from .extract import detect_module, diagnose
    from .rules import run_all_rules
    from . import events as EV
    from . import issues as IS
    from .reconcile import reconcile
    os.makedirs(a.outdir, exist_ok=True)
    db = os.path.join(a.outdir, "ledger.db")
    s = Store(db)
    mods = {n: Module(os.path.join(HERE, "config", f"{n}.yaml")) for n in AUTO_FAMILIES}
    files = sorted(glob.glob(os.path.join(a.reports, "*.pdf")))
    if not files:
        raise ValueError("No PDF files found: check the reports folder")
    print(f"1. Recognise and ingest {len(files)} PDFs")
    unrec = []
    for p in files:
        m = detect_module(p, list(mods.values()))
        if m is None:
            diag = diagnose(p, list(mods.values()))
            unrec.append({"filename": os.path.basename(p), "reason": diag["reason"]})
            print(f"   {os.path.basename(p)}: not recognised ({diag['reason']}); nothing counted (not a zero)")
            continue
        r = ingest(p, m, s)
        print(f"   {r['file']}: " + (r.get("skipped") or f"{m.family_label} | {r['candidates']} values, "
                                      f"{r['statements']} statements | period {r['period_end']}"))
    rs = run_all_rules(s, mods)
    EV.propose_all(s, mods)
    recon = reconcile(s, mods)
    issues = IS.build(s)
    print(f"2. Rule checks: {rs['flagged']} values flagged; {len(rs['findings'])} findings")
    evs = s.rows("SELECT * FROM events WHERE status != 'merged'")
    print(f"   Events proposed: {len(evs)} ({', '.join(sorted({e['event_type'] for e in evs}))}); "
          f"series: {len(recon['series'])}, unresolved periods: {sum(x['unresolved_periods'] for x in recon['series'])}")
    if a.approve_clean_as:
        n = 0
        for r in s.rows("SELECT id FROM candidates WHERE status='candidate' AND flags='[]'"):
            try:
                s.review(r["id"], "approve", a.approve_clean_as, "batch approval of rule-passed values")
                n += 1
            except ValueError:
                pass
        s.commit()
        print(f"3. Approved {n} rule-passed values as reviewer '{a.approve_clean_as}'")
    n = write_review_sheet(s, os.path.join(a.outdir, "review_sheet.csv"))
    print(f"4. Verification sheet with {n} stratified rows")
    cmd_export(argparse.Namespace(db=db, out=os.path.join(a.outdir, "approved_observations.csv")))
    threads = [{**i, "thread_id": i["issue_id"], "status": i["derived_status"], "geos": i["places"],
                "latest_state": i["latest_source_state"], "latest_quote": i["members"][-1]["quote"]} for i in issues]
    def write(name, rows, cols):
        with open(os.path.join(a.outdir, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in r.items()})
    st = s.rows("SELECT st.*, d.filename FROM statements st JOIN documents d USING(doc_id) ORDER BY st.id")
    write("statements.csv", st, ["id", "filename", "page", "issuer", "period_end", "section", "pillar", "statement_type",
                                 "topics", "state", "disease", "geos", "health_zones", "quote", "quantities", "dates",
                                 "caveat", "thread_id", "status"])
    write("issues.csv", threads, ["thread_id", "events", "issuer", "module", "reports", "status", "provisional", "first_seen", "last_seen",
                                  "topics", "geos", "latest_state", "latest_quote"])
    write("findings.csv", rs["findings"], ["type", "candidate", "geo", "period_end", "note"])
    write("unrecognised.csv", unrec, ["filename", "reason"])
    write("events.csv", evs, ["event_id", "event_type", "disease", "geo_scope", "title", "status", "basis", "event_key"])
    srows = []
    for x in recon["series"]:
        for t in x["timeline"]:
            srows.append({"event_id": x["event_id"], "series": x["label"], "period_start": t["period_start"], "period_end": t["period_end"],
                          "state": t["state"], "latest_reported_value": (t["reported"] or {}).get("value"),
                          "reviewed_value": (t["reviewed"] or {}).get("value"),
                          "competing": "; ".join(f"{c['filename']}={c['value']}" for c in t["competing"])})
    write("series_timeline.csv", srows, ["event_id", "series", "period_start", "period_end", "state", "latest_reported_value", "reviewed_value", "competing"])
    print(f"5. {len(st)} statements, {len(issues)} issues "
          f"({sum(i['derived_code'] == 'ongoing_latest' for i in issues)} reported ongoing in the latest report)")
    print("6. " + build_report(s, os.path.join(a.outdir, "audit_report.html"), None, rs, "province",
                                "Automatic multi-family run."))


def cmd_score_sheet(a):
    print(json.dumps(score_review_sheet(Store(a.db), a.sheet), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="outbreak_ledger", description="Outbreak Ledger prototype")
    p.add_argument("--db", default="ledger.db")
    p.add_argument("--config", default=os.path.join(HERE, "config", "synthetic_demo.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("ingest"); x.add_argument("files", nargs="+"); x.set_defaults(f=cmd_ingest)
    sub.add_parser("check").set_defaults(f=cmd_check)
    sub.add_parser("review").set_defaults(f=cmd_review)
    x = sub.add_parser("approve-clean"); x.add_argument("--reviewer", required=True); x.set_defaults(f=cmd_approve_clean)
    x = sub.add_parser("decide"); x.add_argument("id", type=int); x.add_argument("action", choices=["approve", "reject", "correct"])
    x.add_argument("--value", type=int); x.add_argument("--reviewer", required=True); x.add_argument("--note"); x.set_defaults(f=cmd_decide)
    x = sub.add_parser("export"); x.add_argument("--out", default="approved_observations.csv"); x.set_defaults(f=cmd_export)
    x = sub.add_parser("evaluate"); x.add_argument("reference"); x.set_defaults(f=cmd_evaluate)
    x = sub.add_parser("report"); x.add_argument("--out", default="audit_report.html"); x.add_argument("--reference"); x.set_defaults(f=cmd_report)
    x = sub.add_parser("demo"); x.add_argument("--outdir", default="demo_output"); x.set_defaults(f=cmd_demo)
    x = sub.add_parser("run"); x.add_argument("reports"); x.add_argument("--outdir", default="run_output")
    x.add_argument("--approve-clean-as", default=None, help="reviewer name to batch-approve rule-passed values")
    x.set_defaults(f=cmd_run)
    x = sub.add_parser("score-sheet"); x.add_argument("sheet"); x.set_defaults(f=cmd_score_sheet)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    sys.exit(main())

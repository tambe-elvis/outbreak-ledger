"""v0.5 acceptance tests: persistent event intelligence across successive reports.

The reports are synthetic (a test-only family, tests/fixtures/acceptance_family.yaml).
Numbered tests map to the ten acceptance behaviours in the v0.5 brief."""
import json
import os

import pytest

from outbreak_ledger import events as EV
from outbreak_ledger import issues as IS
from outbreak_ledger.extract import Extractor, ingest, narrative_claims
from outbreak_ledger.harmonize import Module
from outbreak_ledger.reconcile import POLICY_ID, reconcile
from outbreak_ledger.rules import run_all_rules
from outbreak_ledger.store import Store
from tests.fixtures.make_acceptance import build, report

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAM = os.path.join(ROOT, "tests", "fixtures", "acceptance_family.yaml")

LAB_01 = "Laboratory reagent stock-out reported in the provincial laboratory of the cholera response."
LAB_05 = "Reagents dispatched to the provincial laboratory by the logistics team for the cholera response."
LAB_10 = "Testing resumed at the provincial laboratory, but the laboratory backlog remains."

R01 = report(1, "2026-03-01", "2026-03-02", [["Alpha", 10, 100], ["Total", 10, 100]], [LAB_01])
R05 = report(5, "2026-03-05", "2026-03-06", [["Alpha", 15, 160], ["Total", 15, 160]], [LAB_05])
R10 = report(10, "2026-03-10", "2026-03-11", [["Alpha", 20, 250], ["Total", 20, 250]], [LAB_10])
R11 = report(11, "2026-03-11", "2026-03-12", [["Alpha", 5, 255], ["Total", 5, 255]],
             ["Contact tracing teams visited all households in the cholera response area."])


def module():
    return Module(FAM)


def workspace(tmp_path, specs, name="w"):
    """Ingest specs in the given order into a fresh store and run every derived layer."""
    m = module()
    s = Store(str(tmp_path / f"{name}.db"))
    for sp in specs:
        ingest(pdf(tmp_path, sp), m, s)
    return s, {m.name: m}


def pdf(tmp_path, sp):
    """Build each report once, so the same bytes (and source hash) are reused across workspaces."""
    name = f"{sp['issuer']}_{sp['no']:02d}_{sp['pub']}_ep{sp['episode']}_{abs(hash(json.dumps(sp, sort_keys=True))) % 10**8}.pdf"
    path = tmp_path / "pdf" / name
    return str(path) if path.exists() else build(sp, str(tmp_path / "pdf"), name)


def derive(s, mods):
    run_all_rules(s, mods)
    EV.propose_all(s, mods)
    rec = reconcile(s, mods)
    iss = IS.build(s)
    return rec, iss


def confirm_and_approve(s, mods, who="tester"):
    for e in s.rows("SELECT event_id FROM events WHERE status='proposed' ORDER BY event_id"):
        EV.confirm_event(s, e["event_id"], who, "checked report heading and scope")
    for c in s.rows("SELECT id FROM candidates WHERE status='candidate' AND indicator!='unknown' ORDER BY item_key"):
        s.review(c["id"], "approve", who, "checked against the source page")
    s.commit()


def series(rec, geo="testland", ctype="cumulative", cls="confirmed", issuer=None):
    out = [x for x in rec["series"] if x["geo"] == geo and x["count_type"] == ctype and x["classification"] == cls
           and (issuer is None or x["issuer"] == issuer)]
    return out


def fingerprint(rec, iss):
    """Content-only summary used to compare workspaces built in different upload orders."""
    ser = sorted((tuple(x["key"]), tuple((t["period_end"], (t["reported"] or {}).get("value"), (t["reviewed"] or {}).get("value"), t["state"])
                                         for t in x["timeline"])) for x in rec["series"])
    issues = sorted((i["issue_id"], tuple(m["item_key"] for m in i["members"]), i["derived_code"]) for i in iss)
    return ser, issues


# 1 ----------------------------------------------------------------------------------------
def test_01_upload_order_does_not_change_results(tmp_path):
    a, mods = workspace(tmp_path, [R01, R05, R10], "a")
    b, _ = workspace(tmp_path, [R10, R01, R05], "b")
    for s in (a, b):
        derive(s, mods)
        confirm_and_approve(s, mods)
    fa, fb = fingerprint(*derive(a, mods)), fingerprint(*derive(b, mods))
    assert fa == fb
    ids = lambda s: sorted(e["event_id"] for e in s.rows("SELECT event_id FROM events"))
    assert ids(a) == ids(b) and len(ids(a)) == 1


# 2 ----------------------------------------------------------------------------------------
def test_02_cumulative_snapshots_are_never_added(tmp_path):
    s, mods = workspace(tmp_path, [R01, R10])
    rec, _ = derive(s, mods)
    (cum,) = series(rec)
    assert cum["latest_reported"]["claim"]["value"] == 250
    assert all((t["reported"] or {}).get("value") != 350 for t in cum["timeline"])
    ch = cum["net_changes"][0]
    assert ch["change"] == 150 and ch["across_gap"] and "net reported change" in ch["label"]
    gap = cum["gaps"][0]
    assert "report no 2 to 9 not in the uploaded archive" in gap["description"]
    (new,) = series(rec, ctype="new")
    assert [(t["period_end"], t["reported"]["value"]) for t in new["timeline"]] == [("2026-03-01", 10), ("2026-03-10", 20)]
    # Without an established numbering sequence the same gap is 'coverage unknown'
    m = module()
    m.cfg.pop("report_sequence")
    rec2 = reconcile(s, {m.name: m})
    assert series(rec2)[0]["gaps"][0]["description"] == "intervening coverage unknown"
    # A reviewer can record that the missing numbers were never issued
    s.conn.execute("INSERT INTO gap_notes VALUES ('acceptance_test','TEST-MOH',5,'not_issued','tester','2026-03-20','checked with issuer')")
    rec3 = reconcile(s, mods)
    assert "reviewer recorded no 5 as not issued" in series(rec3)[0]["gaps"][0]["description"]


# 3 ----------------------------------------------------------------------------------------
def test_03_same_issuer_revision_is_distinct_from_unexplained_difference(tmp_path):
    revised = report(1, "2026-03-01", "2026-03-04", [["Alpha", 10, 90], ["Total", 10, 90]],
                     ["This edition revises the cumulative confirmed cases in Alpha from 100 to 90."])
    unexplained = report(1, "2026-03-01", "2026-03-05", [["Beta", 4, 40], ["Total", 10, 95]])
    suspected = report(2, "2026-03-02", "2026-03-03", [["Alpha", 300], ["Total", 300]],
                       columns=["Province", "Cumulative suspected cases"])
    s, mods = workspace(tmp_path, [R01, revised, unexplained, suspected])
    rec, _ = derive(s, mods)
    alpha = series(rec, geo="alpha")[0]
    t = alpha["timeline"][0]
    assert t["reported"]["value"] == 90 and [x["value"] for x in t["superseded"]] == [100]   # evidenced revision
    rel = s.rows("SELECT * FROM relations WHERE kind='supersedes' AND status='evidenced'")
    assert len(rel) == 1 and "states a revision" in rel[0]["basis"]
    nat = series(rec)[0]["timeline"][0]
    assert nat["state"] == "unresolved" and sorted(c["value"] for c in nat["competing"]) == [90, 95, 100]
    assert s.rows("SELECT COUNT(*) n FROM relations WHERE kind='supersedes' AND status='proposed'")[0]["n"] >= 2
    sus = series(rec, geo="alpha", cls="suspected")
    assert sus and sus[0]["latest_reported"]["claim"]["value"] == 300                     # separate series
    # A reviewer confirming one proposed supersession resolves the national period
    r = s.rows("SELECT rel_key FROM relations WHERE kind='supersedes' AND status='proposed' ORDER BY rel_key")
    for x in r:
        IS.set_relation(s, x["rel_key"], "confirm", "tester", "issuer confirmed the later edition replaces earlier ones")
    rec2, _ = derive(s, mods)
    assert series(rec2)[0]["timeline"][0]["reported"]["value"] == 95


# 4 ----------------------------------------------------------------------------------------
def test_04_conflicting_issuers_are_kept_without_consensus(tmp_path):
    other = report(1, "2026-03-01", "2026-03-02", [["Alpha", 12, 110], ["Total", 12, 110]], issuer="TEST-WHO")
    s, mods = workspace(tmp_path, [R01, other])
    rec, _ = derive(s, mods)
    nat = series(rec)
    assert sorted(x["issuer"] for x in nat) == ["TEST-MOH", "TEST-WHO"]
    d = [x for x in rec["cross_issuer_disagreements"] if x["geo"] == "testland" and x["count_type"] == "cumulative"]
    assert d and sorted(c["value"] for c in d[0]["claims"]) == [100, 110]
    assert all(x["latest_reported"]["claim"]["value"] in (100, 110) for x in nat)            # no averaged or merged value


# 5 ----------------------------------------------------------------------------------------
def test_05_separate_episodes_stay_separate_through_review_and_export(tmp_path):
    ep4 = report(1, "2026-03-01", "2026-03-02", [["Alpha", 3, 30], ["Total", 3, 30]], episode=4)
    s, mods = workspace(tmp_path, [R01, ep4])
    rec, _ = derive(s, mods)
    evs = s.rows("SELECT * FROM events ORDER BY title")
    assert [e["title"] for e in evs] == ["Cholera outbreak no 3, Testland", "Cholera outbreak no 4, Testland"]
    by_event = {}
    for x in series(rec):
        by_event[x["event_id"]] = x["latest_reported"]["claim"]["value"]
    assert sorted(by_event.values()) == [30, 100] and len(by_event) == 2
    cid = s.rows("SELECT id FROM candidates WHERE indicator!='unknown' LIMIT 1")[0]["id"]
    with pytest.raises(ValueError, match="event assignment"):
        s.review(cid, "approve", "tester", "checked")                                      # event not yet confirmed
    confirm_and_approve(s, mods)
    links = EV.item_events(s)
    exported = {links[("candidate", c["item_key"])][0]["event_id"] for c in s.rows("SELECT item_key FROM candidates WHERE status='approved'")}
    assert exported == {e["event_id"] for e in evs}


# 6 and 7 ----------------------------------------------------------------------------------
def test_06_07_action_is_not_resolution_and_backlog_stays_open(tmp_path):
    s, mods = workspace(tmp_path, [R01, R05])
    _, iss = derive(s, mods)
    (stock,) = [i for i in iss if "stock-out" in i["title"]]
    assert stock["derived_code"] == "ongoing_not_updated" and "an action does not establish resolution" in stock["derived_status"]
    assert any(r["kind"] == "addresses" for r in stock["relations"]) and stock["provisional"]
    for sp in (R10, R11):
        ingest(pdf(tmp_path, sp), mods["acceptance_test"], s)
    _, iss = derive(s, mods)
    stock = [i for i in iss if "stock-out" in i["title"]][0]
    backlog = [i for i in iss if "backlog" in i["title"]][0]
    assert stock["issue_id"] != backlog["issue_id"]
    assert stock["derived_code"] == "resolution_reported"
    assert backlog["derived_code"] == "ongoing_not_updated"                                  # report 11 is silent
    assert backlog["derived_status"].startswith("Last reported ongoing on 2026-03-10; no later update")


# 8 ----------------------------------------------------------------------------------------
def test_08_rejecting_and_retagging_update_summaries_but_keep_history(tmp_path):
    s, mods = workspace(tmp_path, [R01, R05, R10])
    _, iss = derive(s, mods)
    backlog = [i for i in iss if "backlog" in i["title"]][0]
    sid = backlog["members"][0]["statement_id"]
    s.review_statement(sid, "reject", "tester", "not a backlog in the source")
    s.commit()
    _, iss = derive(s, mods)
    assert not [i for i in iss if "backlog" in i["title"]]
    assert s.rows("SELECT status FROM statements WHERE id=?", (sid,))[0]["status"] == "rejected"
    assert len(s.rows("SELECT * FROM statement_review_log WHERE statement_id=?", (sid,))) == 1
    with pytest.raises(ValueError):
        s.review_statement(sid, "retag", "tester", "bad type", {"topics": "lab_capacity"})   # must be a list
    rec, _ = derive(s, mods)
    confirm_and_approve(s, mods)
    cid = s.rows("SELECT id FROM candidates WHERE geo='testland' AND count_type='cumulative' AND value=250")[0]["id"]
    s.review(cid, "reject", "tester", "wrong table read")
    s.commit()
    rec, _ = derive(s, mods)
    nat = series(rec)[0]
    assert nat["latest_reviewed"]["claim"]["value"] == 160 and nat["latest_reported"]["claim"]["value"] == 160
    assert s.rows("SELECT value, status FROM candidates WHERE id=?", (cid,))[0] == {"value": 250, "status": "rejected"}


# 9 ----------------------------------------------------------------------------------------
def test_09_older_report_uploaded_late_fills_history_only(tmp_path):
    s, mods = workspace(tmp_path, [R10])
    rec, _ = derive(s, mods)
    assert series(rec)[0]["latest_reported"]["claim"]["value"] == 250
    ingest(pdf(tmp_path, R01), mods["acceptance_test"], s)
    rec, _ = derive(s, mods)
    cum = series(rec)[0]
    assert cum["latest_reported"]["period_end"] == "2026-03-10" and cum["latest_reported"]["claim"]["value"] == 250
    assert [t["period_end"] for t in cum["timeline"]] == ["2026-03-01", "2026-03-10"]


# 10 ---------------------------------------------------------------------------------------
def test_10_restart_preserves_identities_reviews_and_relations(tmp_path):
    s, mods = workspace(tmp_path, [R01, R05, R10])
    derive(s, mods)
    confirm_and_approve(s, mods)
    _, iss = derive(s, mods)
    stock = [i for i in iss if "stock-out" in i["title"]][0]
    rel = [r for r in stock["relations"] if r["kind"] == "reports_resolution"][0]
    IS.set_relation(s, rel["rel_key"], "confirm", "tester", "source states testing resumed")
    IS.set_membership(s, stock["issue_id"], stock["members"][0]["item_key"], "confirm", "tester", "same constraint")
    before = fingerprint(*derive(s, mods)), sorted(e["event_id"] for e in s.rows("SELECT * FROM events WHERE status='confirmed'"))
    path = s.rows("PRAGMA database_list")[0]["file"]
    s.conn.close()
    s2 = Store(path)
    after = fingerprint(*derive(s2, mods)), sorted(e["event_id"] for e in s2.rows("SELECT * FROM events WHERE status='confirmed'"))
    assert before == after
    assert s2.rows("SELECT status FROM relations WHERE rel_key=?", (rel["rel_key"],))[0]["status"] == "confirmed"
    stock2 = [i for i in IS.build(s2) if i["issue_id"] == stock["issue_id"]][0]
    assert stock2["members"][0]["membership"] == "confirmed"


# Regression tests for the reviewed defects -----------------------------------------------
def test_wer_narrative_attribution_needs_evidence(tmp_path):
    m = Module(os.path.join(ROOT, "config", "who_wer.yaml"))
    s = Store(str(tmp_path / "n.db"))
    ex = Extractor(m, s, "doc", {"issuer": "WHO", "period_end": "2026-10-11", "epi_year": 2026}, [])
    page = ("Mpox in Kenya\nAs of 11 October 2026, Kenya had reported a total of 123 confirmed cases and 4 confirmed deaths from mpox.\n")
    narrative_claims(ex, m, [(3, page)])
    rows = s.rows("SELECT * FROM candidates")
    assert rows and all(r["disease"] == "mpox" and r["geo"] == "kenya" for r in rows)
    page2 = "Situation update\nAs of 11 October 2026, a total of 50 confirmed cases and 2 confirmed deaths were reported.\n"
    s2 = Store(str(tmp_path / "n2.db"))
    ex2 = Extractor(m, s2, "doc", {"issuer": "WHO", "period_end": "2026-10-11", "epi_year": 2026}, [])
    narrative_claims(ex2, m, [(3, page2)])
    assert all(r["disease"] == "unknown" and r["indicator"] == "unknown" for r in s2.rows("SELECT * FROM candidates"))


def test_issues_do_not_join_statements_of_different_events(tmp_path):
    s = Store(str(tmp_path / "x.db"))
    for i, (doc, ev) in enumerate((("d1", "EV-A"), ("d2", "EV-B"))):
        s.add_document({"doc_id": doc, "filename": doc, "issuer": "X", "module": "m", "period_end": f"2026-01-0{i + 1}", "publication_date": f"2026-01-0{i + 1}"})
        s.conn.execute("INSERT INTO events (event_id, event_key, event_type, status) VALUES (?,?,?,?)", (ev, ev, "surveillance_stream", "confirmed"))
        k = s.add_statement({"doc_id": doc, "module": "m", "issuer": "X", "period_end": f"2026-01-0{i + 1}", "page": 1, "statement_type": "challenge",
                             "topics": ["lab_capacity"], "geos": [], "health_zones": [], "state": "ongoing",
                             "quote": "Laboratory reagent shortage reported in the provincial laboratory"})
        key = s.rows("SELECT item_key FROM statements WHERE id=?", (k,))[0]["item_key"]
        s.conn.execute("INSERT INTO assignments VALUES ('statement',?,?,'reviewer','confirmed','t','t','t','t')", (key, ev))
    s.commit()
    assert len(IS.build(s)) == 2


def test_event_summaries_exclude_rejected_statements(tmp_path):
    from outbreak_ledger import web
    s, mods = workspace(tmp_path, [R01])
    derive(s, mods)
    sid = s.rows("SELECT id FROM statements WHERE quote LIKE 'Laboratory%'")[0]["id"]
    s.review_statement(sid, "reject", "tester", "not relevant")
    s.commit()
    rec, iss = derive(s, mods)
    links = EV.item_events(s)
    st = s.rows("SELECT st.*, 'f' AS family FROM statements st")
    for x in st:
        x["events"] = links.get(("statement", x["item_key"]), [])
    rows = []
    ev = web.event_summaries(s, rows, st, rec, iss)[0]
    assert ev["challenges"] == 0


def test_reviewer_can_assign_a_family_to_an_unrecognised_report(tmp_path):
    import base64, glob
    from outbreak_ledger import web
    from samples.make_samples import build_all
    build_all(str(tmp_path / "reports"))
    p = sorted(glob.glob(str(tmp_path / "reports" / "*.pdf")))[0]
    res = web.import_files(str(tmp_path / "data"), "events", [{"name": "x.pdf", "data": base64.b64encode(open(p, "rb").read()).decode()}])
    assert "nothing was counted" in res[0]["error"]
    st = web.state(str(tmp_path / "data"), "events")
    u = st["unrecognized"][0]
    assert u["diagnosis"]["families"] and u["diagnosis"]["reason"]
    base = {"doc_id": u["doc_id"], "family": "bvd_drc_insp", "reviewer": "tester", "note": "issuer confirmed the layout"}
    with pytest.raises(ValueError, match="period_end"):
        web.assign_family(str(tmp_path / "data"), "events", {**base, "metadata": {"issuer": "INSP-RDC", "publication_date": "2026-06-08"}})
    r = web.assign_family(str(tmp_path / "data"), "events", {**base, "metadata": {"issuer": "INSP-RDC", "report_no": 3,
                                                                                "period_end": "2026-06-07", "publication_date": "2026-06-08"}})
    assert "error" not in r
    st = web.state(str(tmp_path / "data"), "events")
    d = st["documents"][0]
    assert d["family_origin"] == "reviewer" and "metadata_supplied_by_reviewer" in d["metadata_flags"] and not st["unrecognized"]
    assert any(x["action"] == "assign_family" for x in st["decisions"])

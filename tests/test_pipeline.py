"""Behavioural tests for the Outbreak Ledger prototype on the synthetic corpus."""
import glob
import json
import os

import pytest

from outbreak_ledger.evaluate import evaluate
from outbreak_ledger.extract import ingest, verify_provenance
from outbreak_ledger.harmonize import Module, normalize
from outbreak_ledger.indicators import new_confirmed_by_period, time_since_last_confirmed
from outbreak_ledger.rules import run_rules
from outbreak_ledger.store import Store
from samples.make_samples import build_all

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def ledger(tmp_path):
    ref = build_all(str(tmp_path / "reports"))
    m = Module(os.path.join(ROOT, "config", "synthetic_demo.yaml"))
    s = Store(str(tmp_path / "t.db"))
    for p in sorted(glob.glob(str(tmp_path / "reports" / "*.pdf"))):
        ingest(p, m, s)
    rules = run_rules(s, m)
    return s, m, ref, rules, tmp_path


def flags_of(s, **where):
    cond = " AND ".join(f"{k}=?" for k in where)
    rows = s.rows(f"SELECT flags FROM candidates WHERE {cond}", tuple(where.values()))
    return [json.loads(r["flags"]) for r in rows]


def test_normalize_handles_accents_and_punctuation():
    assert normalize("Confirmés (cumul)") == "confirmes cumul"
    assert normalize("Zone de santé") == "zone de sante"


def test_same_meaning_across_languages_and_layouts(ledger):
    s, *_ = ledger
    rows = s.rows("SELECT DISTINCT raw_label FROM candidates WHERE indicator='cases' "
                  "AND classification='confirmed' AND count_type='cumulative'")
    assert {r["raw_label"] for r in rows} == {"Cas confirmés cumulés", "Confirmés (cumul)", "Cumulative confirmed cases"}


def test_duplicate_document_is_not_ingested_twice(ledger):
    s, m, _, _, tmp = ledger
    first = sorted(glob.glob(str(tmp / "reports" / "*.pdf")))[0]
    assert "skipped" in ingest(first, m, s)


def test_ambiguous_and_unmapped_columns_abstain(ledger):
    s, *_ = ledger
    assert all("ambiguous_definition" in f for f in flags_of(s, raw_label="Probables"))
    assert all("unmapped_label" in f for f in flags_of(s, raw_label="Suspected cases under investigation"))


def test_incompatible_death_definitions_are_not_combined(ledger):
    s, *_ = ledger
    classes = {r["classification"] for r in s.rows("SELECT classification FROM candidates WHERE indicator='deaths'")}
    assert {"unspecified", "confirmed"} <= classes


def test_revision_is_detected_and_explained(ledger):
    s, *_ = ledger
    f = flags_of(s, issuer="MSP-DEMO", geo="beta", period_end="2026-06-14", raw_label="Confirmés (cumul)")[0]
    assert "cumulative_decrease" in f and "explained_by_revision_note" in f


def test_conflicting_sources_are_retained(ledger):
    s, _, _, rules, _ = ledger
    beta = [c for c in rules["conflicts"] if c["key"][1] == "beta" and c["key"][3] == "cases"]
    assert len(beta) == 1
    assert {(iss, val) for iss, val, _ in beta[0]["claims"]} == {("MSP-DEMO", 3), ("AGENCY-DEMO", 4)}


def test_subtotal_mismatch_flagged(ledger):
    s, *_ = ledger
    f = flags_of(s, issuer="MSP-DEMO", geo="TOTAL", period_end="2026-06-21", raw_label="Confirmés (cumul)")[0]
    assert "subtotal_mismatch" in f


def test_indicators_use_only_approved_values(ledger):
    s, *_ = ledger
    rows = new_confirmed_by_period(s, "MSP-DEMO", "health_zone")
    assert all(r["value"] is None for r in rows)
    assert all("awaiting review" in r["basis"] or "not reported" in r["basis"] for r in rows)


def test_missing_report_is_not_zero(ledger):
    s, *_ = ledger
    for r in s.rows("SELECT id FROM candidates WHERE value IS NOT NULL AND indicator!='unknown' AND classification!='unknown' AND count_type!='unknown'"):
        s.review(r["id"], "approve", "tester", "Source checked in test fixture", require_event=False)
    gamma = [r for r in time_since_last_confirmed(s, "MSP-DEMO", "health_zone") if r["geo"] == "gamma"][0]
    assert gamma["result"] is None and "not reported" in gamma["basis"]
    beta = [r for r in time_since_last_confirmed(s, "MSP-DEMO", "health_zone") if r["geo"] == "beta"][0]
    assert beta["result"] == 14


def test_corrections_are_logged_not_overwritten(ledger):
    s, *_ = ledger
    cid = s.rows("SELECT id, value FROM candidates LIMIT 1")[0]
    s.review(cid["id"], "correct", "tester", "typo in source", value=99, require_event=False)
    row = s.rows("SELECT value, corrected_value, status FROM candidates WHERE id=?", (cid["id"],))[0]
    assert row["value"] == cid["value"] and row["corrected_value"] == 99 and row["status"] == "corrected"
    assert len(s.rows("SELECT * FROM review_log WHERE candidate_id=?", (cid["id"],))) == 1


def test_provenance_and_evaluation(ledger):
    s, _, ref, _, _ = ledger
    assert all(p["provenance_ok"] for p in verify_provenance(s))
    ev = evaluate(s, ref)
    assert ev["recall"] == 1.0 and ev["precision"] == 1.0

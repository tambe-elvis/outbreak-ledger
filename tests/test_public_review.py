"""Regression coverage for the reader-facing evidence boundaries."""
import base64
from pathlib import Path

import pytest

from outbreak_ledger import events, web
from outbreak_ledger.reconcile import _resolve_period


def test_reported_revision_does_not_replace_reviewed_value_until_approved():
    original = dict(item_key="C-a", doc_id="a", effective_value=100, status="approved",
                    publication_date="2026-03-02", report_no=1)
    revision = dict(item_key="C-b", doc_id="b", effective_value=90, status="candidate",
                    publication_date="2026-03-04", report_no=1)
    period = _resolve_period([original, revision], {"C-a"}, set())
    assert period["reported"]["effective_value"] == 90
    assert period["reviewed"]["effective_value"] == 100
    assert period["pending_note"]
    revision["status"] = "approved"
    period = _resolve_period([original, revision], {"C-a"}, {"C-a"})
    assert period["reviewed"]["effective_value"] == 90


def test_weekly_practice_snapshots_and_public_response_review(tmp_path):
    reports = Path(__file__).resolve().parents[1] / "samples" / "reports"
    files = [dict(name=path.name, data=base64.b64encode(path.read_bytes()).decode())
             for path in sorted(reports.glob("*.pdf"))]
    web.import_files(tmp_path, "synthetic_demo", files)
    _, mods, store = web.init_context(tmp_path, "synthetic_demo")
    _, reconciliation, _ = web.refresh(store, mods)
    series = [row for row in reconciliation["series"] if row["issuer"] == "MSP-DEMO"
              and row["geo"] == "TOTAL" and row["indicator"] == "cases"
              and row["count_type"] == "cumulative"]
    assert len(series) == 1
    assert [point["reported"]["value"] for point in series[0]["timeline"]] == [16, 17, 18]

    event = store.rows("SELECT event_id FROM events")[0]["event_id"]
    statement = next(row for row in store.rows("SELECT * FROM statements") if "40 samples" in row["quote"])
    assert not statement["quote"].startswith("External situation report")
    note = dict(item_key=statement["item_key"], event_id=event, action="save",
                summary="The report identified a backlog of 40 laboratory samples.",
                reviewer="Practice reviewer", note="Compared with the original source.")
    with pytest.raises(ValueError, match="confirm its event"):
        web.save_public_note(store, note)
    events.confirm_event(store, event, "Practice reviewer", "Practice event checked.")
    store.review_statement(statement["id"], "confirm", "Practice reviewer", "Source checked.")
    web.save_public_note(store, note)
    assert web.public_notes(store)[0]["summary"] == note["summary"]
    web.save_public_note(store, {**note, "action": "withdraw"})
    assert not web.public_notes(store)
    assert len(store.rows("SELECT id FROM decisions WHERE kind='public_note'")) == 2
    assert store.rows("SELECT quote FROM statements WHERE id=?", (statement["id"],))[0]["quote"] == statement["quote"]
    store.conn.close()

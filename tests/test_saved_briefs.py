import base64
import hashlib
import io
import json
from pathlib import Path

import pdfplumber
import pytest

from outbreak_ledger import briefs, events, web


def test_saved_brief_keeps_reviewed_snapshot_after_later_change(tmp_path):
    reports = Path(__file__).resolve().parents[1] / "samples" / "reports"
    files = [
        {"name": path.name, "data": base64.b64encode(path.read_bytes()).decode()}
        for path in sorted(reports.glob("*.pdf"))
    ]
    web.import_files(tmp_path, "synthetic_demo", files)
    _, modules, store = web.init_context(tmp_path, "synthetic_demo")
    web.refresh(store, modules)
    event_id = store.rows("SELECT event_id FROM events")[0]["event_id"]
    with pytest.raises(ValueError, match="confirmed event"):
        briefs.capture(web.state(tmp_path, "synthetic_demo"), event_id)
    events.confirm_event(store, event_id, "Test reviewer", "Confirmed practice report context.")
    with pytest.raises(ValueError, match="Review at least one"):
        briefs.capture(web.state(tmp_path, "synthetic_demo"), event_id)

    row = store.rows(
        "SELECT c.id FROM candidates c JOIN documents d USING(doc_id) WHERE c.geo='TOTAL' "
        "AND c.indicator='cases' AND c.count_type='cumulative' AND d.report_no=12"
    )[0]
    store.review(row["id"], "approve", "Test reviewer", "Compared with source.")
    statement = next(row for row in store.rows("SELECT * FROM statements") if "40 samples" in row["quote"])
    store.review_statement(statement["id"], "confirm", "Test reviewer", "Source checked.")
    web.save_public_note(store, dict(item_key=statement["item_key"], event_id=event_id, action="save",
                                   summary="The report identified a backlog of 40 laboratory samples.",
                                   reviewer="Test reviewer", note="Checked against source."))
    store.commit()
    snapshot = briefs.capture(web.state(tmp_path, "synthetic_demo"), event_id)
    assert snapshot["figures"][0]["claim"]["value"] == 18
    assert snapshot["notes"][0]["note_review"]["reviewer"] == "Test reviewer"
    assert len(snapshot["sources"]) == 2
    briefs.save(store, snapshot)
    saved = store.rows("SELECT payload, pdf, evidence_sha256 FROM situation_briefs")[0]
    assert saved["evidence_sha256"] == hashlib.sha256(saved["payload"].encode("utf-8")).hexdigest()
    with pdfplumber.open(io.BytesIO(saved["pdf"])) as pdf:
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text()
        assert "Cumulative confirmed cases: 18" in text
        assert "40 laboratory samples" in text
        assert "fictional data" in text

    store.review(row["id"], "correct", "Test reviewer", "Illustrative correction.", 19)
    store.commit()
    store.conn.close()
    assert briefs.capture(web.state(tmp_path, "synthetic_demo"), event_id)["figures"][0]["claim"]["value"] == 19
    _, _, reopened = web.init_context(tmp_path, "synthetic_demo")
    assert reopened.rows("SELECT payload, pdf, evidence_sha256 FROM situation_briefs")[0] == saved
    assert json.loads(saved["payload"])["figures"][0]["claim"]["value"] == 18
    assert briefs.list_saved(reopened)[0]["brief_id"] == snapshot["brief_id"]
    reopened.conn.close()

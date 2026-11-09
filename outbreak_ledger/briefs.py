"""Capture reviewed evidence and render a fixed one-page situation brief."""

import hashlib
import io
import json
from datetime import date
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

from .store import now

FIGURE_LIMIT = 4
NOTE_LIMIT = 2

CLASSIFICATIONS = {
    "confirmed": "confirmed", "suspected": "suspected", "probable": "probable",
    "idsr_notified": "notified", "rdt_positive": "with a positive rapid test",
    "unspecified": "", "unspecified_label": "",
}
ORGANISATIONS = {
    "INSP-RDC": "National Institute of Public Health, DRC",
    "WHO": "World Health Organization", "WHO_WER": "World Health Organization",
    "MSP-DEMO": "Illustrative health authority", "AGENCY-DEMO": "Illustrative reporting agency",
}
PLACES = {
    "drc": "Democratic Republic of the Congo", "demo_country": "Example country",
    "nord-kivu": "North Kivu", "sud-kivu": "South Kivu",
    "total": "All areas in the report", "national total": "All areas in the report",
}


def place(value):
    return PLACES.get(value.lower(), value.replace("_", " ").title()) if value else "Area not specified"


def date_text(value):
    try:
        parsed = date.fromisoformat(value)
        return f"{parsed.day} {parsed:%b %Y}"
    except (TypeError, ValueError):
        return "Date unavailable"


def period_text(start, end, kind):
    if kind == "snapshot":
        return "As of " + date_text(end)
    if start and start != end:
        return date_text(start) + " to " + date_text(end)
    return date_text(end)


def approved(claim):
    return claim and claim["event_link"] == "confirmed" and claim["status"] in ("approved", "corrected")


def figure_label(series):
    label = "New" if series["count_type"] == "new" else "Cumulative"
    return " ".join(filter(None, [label, CLASSIFICATIONS[series["classification"]], series["indicator"]]))


def capture(state, event_id, area=""):
    event = next((row for row in state["events_list"] if row["event_id"] == event_id and row["status"] == "confirmed"), None)
    if event is None:
        raise ValueError("Select a confirmed event before saving a brief.")
    if not isinstance(area, str) or (area and not any(row["geo"] == area for row in event["series"])):
        raise ValueError("Select an area listed for this event.")

    # Keep separate issuer, definition, geography and period series.
    options = []
    pending = False
    gaps = False
    for series in event["series"]:
        if area and series["geo"] != area:
            continue
        if (series["indicator"] not in ("cases", "deaths") or series["count_type"] not in ("new", "cumulative")
                or series["classification"] not in CLASSIFICATIONS):
            continue
        eligible = [point for point in series["timeline"] if approved(point["reviewed"])]
        latest = series["timeline"][-1]
        pending |= not eligible or eligible[-1] is not latest or bool(latest["pending_note"]) or latest["state"] == "unresolved"
        gaps |= bool(series["gaps"])
        if not eligible:
            continue
        options.append((series, eligible))
    # Stable ordering, with national or combined-area figures first.
    options.sort(key=lambda pair: (
        pair[0]["geo_level"] not in ("national", "aggregate"),
        pair[0]["indicator"] != "cases", pair[0]["count_type"] != "cumulative",
        pair[0]["classification"] != "confirmed",
        -(date.fromisoformat(pair[1][-1]["period_end"]).toordinal() if pair[1][-1]["period_end"] else 0),
        tuple(value or "" for value in pair[0]["key"]),
    ))

    candidates = {row["item_key"]: row for row in state["candidates"]}
    figures = []
    for series, eligible in options[:FIGURE_LIMIT]:
        point = eligible[-1]
        claim = point["reviewed"]
        original = candidates[claim["item_key"]]
        entry = {
            "label": figure_label(series), "area": place(series["geo"]),
            "issuer": ORGANISATIONS.get(series["issuer"], series["issuer"] or "Organisation not specified"),
            "series": {key: series[key] for key in ("key", "module", "geo", "geo_level", "indicator",
                                                    "classification", "count_type", "period_kind")},
            "claim": claim, "extracted_value": original["value"],
            "review": {key: original[key] for key in ("reviewer", "reviewed_at", "review_note")},
            "period": period_text(point["period_start"], point["period_end"], series["period_kind"]),
            "newer_period_pending": point is not series["timeline"][-1],
            "competing_claims": point["state"] == "unresolved",
            "revision_pending": bool(point["pending_note"]),
        }
        if series["count_type"] == "cumulative" and len(eligible) >= 2:
            prior = eligible[-2]["reviewed"]
            entry["prior_claim"] = prior
            entry["prior_review"] = {key: candidates[prior["item_key"]][key]
                                     for key in ("reviewer", "reviewed_at", "review_note")}
            entry["net_change"] = claim["value"] - prior["value"]
        figures.append(entry)

    statements = {row["item_key"]: row for row in state["statements"]}
    note_options = []
    for note in state["public_notes"]:
        statement = statements.get(note["item_key"])
        if (note["event_id"] != event_id or not statement or statement["status"] not in ("confirmed", "retagged")
                or statement["reviewed_at"] != note["statement_reviewed_at"]
                or not any(link["event_id"] == event_id and link["status"] == "confirmed" for link in statement["events"])):
            continue
        places = set(statement["geos"] + statement["health_zones"])
        if area and area not in places:
            continue
        note_options.append({
            **note,
            "source": {key: statement[key] for key in ("item_key", "doc_id", "filename", "page", "period_end", "report_no", "quote")},
            "statement_review": {key: statement[key] for key in ("reviewer", "reviewed_at", "review_note")},
        })
    note_options.sort(key=lambda note: (note["source"]["period_end"] or "", note["item_key"]), reverse=True)
    notes = note_options[:NOTE_LIMIT]
    if not figures and not notes:
        raise ValueError("Review at least one figure or save an English response note for this scope first.")

    notices = []
    if pending:
        notices.append("Some figures await review or have conflicting values.")
    if gaps:
        notices.append("Some reporting periods are missing from the available series.")
    if any(row["event_id"] == event_id and (not area or row["geo"] == area) for row in state["cross_issuer"]):
        notices.append("Reporting organisations give different figures for at least one comparable period.")
    if len(options) > FIGURE_LIMIT or len(note_options) > NOTE_LIMIT:
        notices.append(f"This brief shows {len(figures)} of {len(options)} reviewed series and "
                       f"{len(notes)} of {len(note_options)} English response notes for its scope.")

    evidence = [row["claim"] for row in figures] + [row["prior_claim"] for row in figures if "prior_claim" in row]
    evidence += [row["source"] for row in notes]
    ids = {row["doc_id"] for row in evidence}
    return {
        "schema_version": 1, "brief_id": uuid4().hex, "saved_at": now(),
        "event_id": event_id, "title": event["title"],
        "event_review": {key: event.get(key) for key in ("reviewer", "reviewed_at", "review_note")},
        "scope": place(area) if area else ", ".join(place(value) for value in event["geo_scope"]) or "All event areas",
        "workspace": state["module"], "app_version": state["version"],
        "policy": state["policy"], "configuration_fingerprint": state["config_fingerprint"],
        "figures": figures, "notes": notes, "notices": notices,
        "sources": [row for row in state["documents"] if row["doc_id"] in ids],
    }


def render_pdf(brief):
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.platypus import KeepInFrame, Paragraph, Spacer

    font_dir = Path(reportlab.__file__).parent / "fonts"
    for name, file in (("BriefText", "Vera.ttf"), ("BriefBold", "VeraBd.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_dir / file)))

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Situation brief - " + brief["title"])
    pdf.setAuthor("Outbreak Ledger | IRNR Group")
    page_width, page_height = A4
    width = page_width - 80
    styles = {
        "title": ParagraphStyle("title", fontName="BriefBold", fontSize=21, leading=25, textColor=colors.HexColor("#153e42"), spaceAfter=8),
        "section": ParagraphStyle("section", fontName="BriefBold", fontSize=11, leading=14, textColor=colors.HexColor("#155e64"), spaceBefore=12, spaceAfter=5),
        "body": ParagraphStyle("body", fontName="BriefText", fontSize=10.5, leading=14, spaceAfter=5),
        "source": ParagraphStyle("source", fontName="BriefText", fontSize=8.5, leading=11, textColor=colors.HexColor("#445b60"), spaceAfter=6),
    }
    blocks = []

    def add(text, style="body"):
        blocks.append(Paragraph(escape(text), styles[style]))

    def reference(claim):
        number = f"Report {claim['report_no']}" if claim["report_no"] is not None else "Source report"
        return f"{number}, page {claim['page']} ({claim['filename']})"

    add("Situation brief", "title")
    add(brief["title"], "section")
    add(f"{brief['scope']} | Saved {date_text(brief['saved_at'][:10])}")
    if brief["workspace"] == "synthetic_demo":
        add("Practice example with fictional data", "source")
    if brief["figures"]:
        add("Reviewed figures", "section")
        for row in brief["figures"]:
            claim = row["claim"]
            add(f"{row['label']}: {claim['value']:,} | {row['area']} | {row['period']}")
            add(f"{row['issuer']} | {reference(claim)}", "source")
            if "prior_claim" in row:
                prior = row["prior_claim"]
                add(f"Net reported change {row['net_change']:+,} since {date_text(prior['period_end'])}. "
                    f"Earlier figure {prior['value']:,}, {reference(prior)}.", "source")
            if row["newer_period_pending"] or row["competing_claims"] or row["revision_pending"]:
                add("A later or competing figure requires review.", "source")
    if brief["notes"]:
        add("Response updates", "section")
        for note in brief["notes"]:
            add(note["summary"])
            add(f"{date_text(note['source']['period_end'])} | {reference(note['source'])}", "source")
    if brief["notices"]:
        add("Reading the figures", "section")
        for notice in brief["notices"]:
            add(notice, "source")
    if any("net_change" in row for row in brief["figures"]):
        add("Net changes compare cumulative reported figures and may include revisions.", "source")
    blocks.append(Spacer(1, 5))

    pdf.setFillColor(colors.HexColor("#153e42"))
    pdf.rect(0, page_height - 13, page_width, 13, fill=1, stroke=0)
    frame = KeepInFrame(width, page_height - 100, blocks, mode="shrink")
    _, height = frame.wrapOn(pdf, width, page_height - 100)
    frame.drawOn(pdf, 40, page_height - 40 - height)
    pdf.setStrokeColor(colors.HexColor("#c8dadb"))
    pdf.line(40, 34, page_width - 40, 34)
    pdf.setFillColor(colors.HexColor("#445b60"))
    pdf.setFont("BriefText", 8)
    pdf.drawString(40, 21, f"Outbreak Ledger | IRNR Group | Brief {brief['brief_id'][:12]}")
    pdf.drawRightString(page_width - 40, 21, "1 / 1")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def save(store, brief):
    encoded = json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    pdf = render_pdf(brief)
    store.conn.execute(
        "INSERT INTO situation_briefs (brief_id, saved_at, title, scope, payload, evidence_sha256, pdf) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (brief["brief_id"], brief["saved_at"], brief["title"], brief["scope"], encoded,
         hashlib.sha256(encoded.encode("utf-8")).hexdigest(), pdf),
    )
    store.commit()


def list_saved(store):
    return store.rows("SELECT brief_id, saved_at, title, scope FROM situation_briefs ORDER BY saved_at DESC, brief_id")

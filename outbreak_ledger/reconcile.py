"""Temporal reconciliation of numerical claims across reports.

Principle: evidence accumulates; figures keep their definitions and periods;
changes need explicit, inspectable rules. Nothing here edits a claim.

A series is a set of comparable claims: same event, issuer, report family,
place, geographical level, indicator, classification, count type and period
kind (one day, one epidemiological week, cumulative snapshot, year to date).
Daily and weekly counts, suspected and confirmed cases, or provinces and the
nation never compete for one value.

Within a series, the epidemiological timeline follows each claim's reporting
period. Publication date and report number order the versions of claims about
the same period. Upload and review times are recorded for audit only, so the
result is the same whatever order reports are uploaded in.

Policy OL-RP-1:
- Cumulative snapshots are never added. The difference between two comparable
  snapshots is a net reported change, which may include revisions or
  reclassification; it is not a count of new cases.
- New counts belong only to their stated period. Missing periods are not
  interpolated or filled with zeros.
- A gap is described as reports absent from the archive only when the family's
  report numbers are known to be consecutive; otherwise coverage is unknown.
  A reviewer may record that a report number was never issued.
- Same issuer, same period, different value: superseded only when the later
  report states the revision (evidenced) or a reviewer confirms supersession.
  Otherwise the period is unresolved and both claims stay visible.
- Different issuers: claims are shown side by side; no consensus is computed.
- Latest reported claim and latest reviewed value are reported separately; a
  newer claim never acquires approval by arriving later.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date

from .events import item_events
from .store import now

POLICY_ID = "OL-RP-1"
APPROVED = ("approved", "corrected")


def _d(x):
    try:
        return date.fromisoformat(x)
    except (TypeError, ValueError):
        return None


def period_kind(c, module=None):
    s, e = _d(c.get("period_start")), _d(c.get("period_end"))
    if c["count_type"] == "cumulative":
        if module is not None and module.cfg.get("cumulative_period") == "snapshot":
            return "snapshot"
        if s is None or s == e:
            return "snapshot"
        if s.month == 1 and s.day <= 7 and s.year in (e.year, e.year - 1) and (e - s).days > 7:
            return "year_to_date"
        return f"cumulative_from_{c['period_start']}"
    if s is None or e is None:
        return "undated_period"
    n = (e - s).days + 1
    return {1: "day", 7: "epi_week"}.get(n, f"{n}_days")


def effective(c):
    return c["corrected_value"] if c["status"] == "corrected" else c["value"]


def _version_order(c):
    return (c.get("publication_date") or "", c.get("report_no") or 0, c["doc_id"])


def load_claims(store, modules=None):
    """Non-rejected claims with complete meaning, joined to document and event."""
    ev = item_events(store)
    rows = store.rows("SELECT c.*, d.module, d.filename, d.publication_date, d.report_no, d.retrieved_at "
                      "FROM candidates c JOIN documents d USING(doc_id) WHERE c.status != 'rejected'")
    out = []
    for c in rows:
        if c["value"] is None and c["status"] != "corrected":
            continue
        if "unknown" in (c["indicator"], c["classification"], c["count_type"]):
            continue
        links = ev.get(("candidate", c["item_key"]), [])
        conf = [l for l in links if l["status"] == "confirmed"]
        link = conf[0] if conf else (links[0] if len(links) == 1 else None)
        c["event_id"] = link["event_id"] if link else None
        c["event_link"] = link["status"] if link else "unassigned"
        c["effective_value"] = effective(c)
        c["period_kind"] = period_kind(c, (modules or {}).get(c["module"]))
        out.append(c)
    return out


def series_key(c):
    return (c["event_id"] or "UNASSIGNED", c["issuer"], c["module"], c["geo"], c["geo_level"], c["indicator"],
            c["classification"], c["count_type"], c["period_kind"])


def rel_key(kind, a, b):
    return "R-" + hashlib.sha1(f"{kind}|{a}|{b}".encode()).hexdigest()[:16]


def propose_supersessions(store, claims=None):
    """Same issuer, same series and period, different value in a later report: record a
    supersession, evidenced when the later report states the revision, otherwise proposed."""
    claims = claims if claims is not None else load_claims(store)
    notes = store.rows("SELECT * FROM revision_notes WHERE kind='stated_revision'")
    decided = {r["rel_key"] for r in store.rows("SELECT rel_key FROM relations WHERE status IN ('confirmed','rejected')")}
    store.conn.execute("DELETE FROM relations WHERE kind='supersedes' AND origin IN ('rule','rule_explicit') AND status IN ('proposed','evidenced')")
    by = defaultdict(list)
    for c in claims:
        by[series_key(c) + (c["period_start"], c["period_end"])].append(c)
    for items in by.values():
        docs = sorted({c["doc_id"]: c for c in items}.values(), key=_version_order)
        for i, later in enumerate(docs):
            for earlier in docs[:i]:
                if later["doc_id"] == earlier["doc_id"] or later["effective_value"] == earlier["effective_value"]:
                    continue
                k = rel_key("supersedes", later["item_key"], earlier["item_key"])
                if k in decided:
                    continue
                explicit = any(n["doc_id"] == later["doc_id"] and n["geo"] == later["geo"] and n["old_value"] == earlier["effective_value"]
                               and n["new_value"] == later["effective_value"] for n in notes)
                store.conn.execute(
                    "INSERT OR REPLACE INTO relations (rel_key, kind, from_kind, from_key, to_kind, to_key, origin, status, basis, at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (k, "supersedes", "candidate", later["item_key"], "candidate", earlier["item_key"],
                     "rule_explicit" if explicit else "rule", "evidenced" if explicit else "proposed",
                     (f"{later['filename']} states a revision from {earlier['effective_value']} to {later['effective_value']}" if explicit else
                      f"{later['filename']} gives {later['effective_value']} for the period that {earlier['filename']} reported as "
                      f"{earlier['effective_value']}; no revision statement found"), now()))
    store.commit()


def _resolve_period(claims, superseded, reviewed_superseded):
    """Return the state of one series period."""
    uniq = {}
    for c in sorted(claims, key=_version_order):
        uniq.setdefault((c["doc_id"], c["effective_value"]), c)
    allc = list(uniq.values())
    active = [c for c in allc if c["item_key"] not in superseded]
    vals = sorted({c["effective_value"] for c in active})
    out = {"claims": allc, "superseded": [c for c in allc if c["item_key"] in superseded]}
    if len(vals) == 1:
        out["reported"] = max((c for c in active), key=_version_order)
        out["state"] = "agreed" if len({c["doc_id"] for c in active}) > 1 else "single"
    else:
        out["reported"] = None
        out["state"] = "unresolved"
        out["competing"] = active
    approved = [c for c in allc if c["status"] in APPROVED and c["item_key"] not in reviewed_superseded]
    rv = sorted({c["effective_value"] for c in approved})
    if len(rv) == 1:
        out["reviewed"] = max(approved, key=_version_order)
        later = [c for c in active if c["status"] not in APPROVED and _version_order(c) > _version_order(out["reviewed"])]
        if any(c["effective_value"] != out["reviewed"]["effective_value"] for c in later):
            out["pending_note"] = "a later claim with a different value awaits review"
        elif later:
            out["pending_note"] = "a later claim with the same value awaits review"
    elif len(rv) > 1:
        out["reviewed"] = None
        out["reviewed_conflict"] = approved
    else:
        out["reviewed"] = None
    return out


def _brief(c):
    if not c:
        return None
    return {"item_key": c["item_key"], "id": c["id"], "value": c["effective_value"], "status": c["status"],
            "period_start": c["period_start"], "period_end": c["period_end"], "period_label": c.get("period_label"),
            "filename": c["filename"], "report_no": c["report_no"], "publication_date": c["publication_date"],
            "doc_id": c["doc_id"], "page": c["page"], "event_link": c["event_link"]}


def reconcile(store, modules):
    """Compute every series' timeline, latest reported claim and latest reviewed value."""
    claims = load_claims(store, modules)
    propose_supersessions(store, claims)
    periods_by_claim = {
        c["item_key"]: series_key(c) + (c["period_start"], c["period_end"]) for c in claims
    }
    sup = {}
    reviewed_sup = set()
    approved_keys = {c["item_key"] for c in claims if c["status"] in APPROVED and c["event_link"] == "confirmed"}
    for relation in store.rows("SELECT from_key, to_key FROM relations WHERE kind='supersedes' AND status IN ('evidenced','confirmed')"):
        earlier = relation["to_key"]
        later = relation["from_key"]
        if earlier != later and earlier in periods_by_claim and periods_by_claim[earlier] == periods_by_claim.get(later):
            sup[earlier] = later
            if later in approved_keys:
                reviewed_sup.add(earlier)
    gap_notes = {(g["module"], g["issuer"], g["report_no"]): g for g in store.rows("SELECT * FROM gap_notes")}
    docs = store.rows("SELECT doc_id, module, issuer, report_no, period_end FROM documents")
    by = defaultdict(list)
    for c in claims:
        by[series_key(c)].append(c)
    series = []
    for key, items in by.items():
        event_id, issuer, module, geo, level, indicator, cls, ctype, pkind = key
        mod = modules.get(module)
        periods = defaultdict(list)
        for c in items:
            periods[(c["period_start"], c["period_end"])].append(c)
        timeline = []
        for (ps, pe), cl in sorted(periods.items(), key=lambda x: (x[0][1] or "", x[0][0] or "")):
            r = _resolve_period(cl, sup, reviewed_sup)
            timeline.append({"period_start": ps, "period_end": pe, "period_label": cl[0].get("period_label"), "state": r["state"],
                             "reported": _brief(r["reported"]), "reviewed": _brief(r.get("reviewed")),
                             "pending_note": r.get("pending_note"),
                             "competing": [_brief(c) for c in r.get("competing", [])],
                             "reviewed_conflict": [_brief(c) for c in r.get("reviewed_conflict", [])],
                             "superseded": [_brief(c) for c in r["superseded"]],
                             "n_claims": len(r["claims"])})
        last = timeline[-1]
        latest_reported = {"period_end": last["period_end"], "state": last["state"], "claim": last["reported"],
                           "competing": last["competing"]}
        rev = [t for t in timeline if t["reviewed"]]
        latest_reviewed = None
        if rev:
            t = rev[-1]
            latest_reviewed = {"period_end": t["period_end"], "claim": t["reviewed"], "pending_note": t["pending_note"],
                               "newer_unreviewed_through": last["period_end"] if last["period_end"] != t["period_end"] else None}
        # gaps and net changes
        step = 1 if (mod and mod.period_mode == "daily") else 7 if (mod and mod.period_mode == "weekly") else None
        gaps, changes = [], []
        for a, b in zip(timeline, timeline[1:]):
            da, db = _d(a["period_end"]), _d(b["period_end"])
            if da and db and step and (db - da).days > step:
                missing_desc = "intervening coverage unknown"
                if mod and mod.cfg.get("report_sequence") == "consecutive":
                    na = (a["reported"] or (a["competing"] or [{}])[0] or {}).get("report_no")
                    nb = (b["reported"] or (b["competing"] or [{}])[0] or {}).get("report_no")
                    if isinstance(na, int) and isinstance(nb, int) and nb > na + 1:
                        nums = list(range(na + 1, nb))
                        not_issued = [n for n in nums if gap_notes.get((module, issuer, n), {}).get("status") == "not_issued"]
                        absent = [n for n in nums if n not in not_issued]
                        parts = []
                        if absent:
                            parts.append(f"report no {absent[0]}" + (f" to {absent[-1]}" if len(absent) > 1 else "") + " not in the uploaded archive")
                        if not_issued:
                            parts.append(f"reviewer recorded no {', '.join(map(str, not_issued))} as not issued")
                        missing_desc = "; ".join(parts) or missing_desc
                gaps.append({"after": a["period_end"], "before": b["period_end"], "days": (db - da).days, "description": missing_desc})
            if ctype == "cumulative" and a["reported"] and b["reported"]:
                ch = b["reported"]["value"] - a["reported"]["value"]
                changes.append({"from": a["period_end"], "to": b["period_end"], "change": ch,
                                "across_gap": bool(da and db and step and (db - da).days > step),
                                "label": "net reported change (may include revisions or reclassification; not a count of new cases)"})
        series.append({"key": list(key), "event_id": event_id, "issuer": issuer, "module": module,
                       "family": mod.family_label if mod else module, "geo": geo, "geo_level": level, "indicator": indicator,
                       "classification": cls, "count_type": ctype, "period_kind": pkind,
                       "label": f"{indicator} {cls.replace('_', ' ')} {ctype} ({pkind.replace('_', ' ')}), {geo} [{issuer}]",
                       "timeline": timeline, "latest_reported": latest_reported, "latest_reviewed": latest_reviewed,
                       "gaps": gaps, "net_changes": changes,
                       "unresolved_periods": sum(t["state"] == "unresolved" for t in timeline),
                       "event_links": sorted({c["event_link"] for c in items})})
    # cross-issuer comparison: same series apart from issuer and family
    cross = defaultdict(list)
    for s in series:
        k = (s["event_id"], s["geo"], s["geo_level"], s["indicator"], s["classification"], s["count_type"], s["period_kind"])
        cross[k].append(s)
    disagreements = []
    for k, ss in cross.items():
        if len({s["issuer"] for s in ss}) < 2:
            continue
        per = defaultdict(list)
        for s in ss:
            for t in s["timeline"]:
                if t["reported"]:
                    per[(t["period_start"], t["period_end"])].append((s["issuer"], t["reported"]))
        for (ps, pe), cl in per.items():
            if len({i for i, _ in cl}) > 1 and len({c["value"] for _, c in cl}) > 1:
                disagreements.append({"event_id": k[0], "geo": k[1], "indicator": k[3], "classification": k[4], "count_type": k[5],
                                      "period_start": ps, "period_end": pe, "claims": [{"issuer": i, **c} for i, c in cl]})
    series.sort(key=lambda s: (s["event_id"], s["geo_level"] != "national", s["geo"], s["indicator"], s["count_type"], s["classification"]))
    return {"policy": POLICY_ID, "series": series, "cross_issuer_disagreements": disagreements, "supersessions": sup}

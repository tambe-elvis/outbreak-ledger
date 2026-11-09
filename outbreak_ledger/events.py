"""Stable event identities and their assignment to evidence.

Three event types are recognised:

- outbreak_episode: a defined outbreak with an epidemiological scope
  (for example the 17th Ebola epidemic in the DRC);
- surveillance_stream: continuing observations of a disease or syndrome
  (for example weekly IDSR malaria counts in the DRC);
- public_health_signal: a reported hazard requiring assessment, which may
  have no identified disease.

Event identifiers are derived from an event key (type, disease, place,
episode or system), so they do not depend on upload order. Rules only
propose events and assignments; a reviewer confirms, edits, merges or
separates them. Proposals are recomputed from all evidence on every refresh,
but reviewer decisions (confirmed or rejected assignments, confirmed or
merged events) are never overwritten.

Report family, issuing organisation and event are separate concepts: one
report may describe several events and one event may appear in several
report families.
"""
import hashlib
import json

from .store import now

EVENT_TYPES = ("outbreak_episode", "surveillance_stream", "public_health_signal")


def event_id_for(key):
    return "EV-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8].upper()


def _title(rule, **kw):
    t = rule.get("title", "{disease} event")
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v if v is not None else "?"))
    return t


def _ensure_event(store, key, etype, disease, geo, title, basis, proposed):
    eid = event_id_for(key)
    row = store.conn.execute("SELECT event_id FROM events WHERE event_id=?", (eid,)).fetchone()
    if row is None:
        store.conn.execute(
            "INSERT INTO events (event_id, event_key, event_type, disease, geo_scope, title, aliases, status, basis, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (eid, key, etype, disease, json.dumps(geo), title, json.dumps([key]), "proposed", basis, now()))
    proposed.add(eid)
    return eid


def _candidates_for_lookup(store, disease, country, live):
    """Outbreak episodes or streams for a disease in a country, among events supported by
    current evidence (`live`) or confirmed by a reviewer. Merged events are excluded."""
    if not disease or not country:
        return []
    out = []
    for e in store.rows("SELECT * FROM events WHERE status != 'merged' AND disease=? AND event_type IN ('outbreak_episode','surveillance_stream') "
                        "ORDER BY event_id", (disease,)):
        if (e["event_id"] in live or e["status"] == "confirmed") and country in json.loads(e["geo_scope"] or "[]"):
            out.append(e)
    return out


def _propose(store, item_kind, item_key, eid, basis, plan):
    plan.append((item_kind, item_key, eid, basis))


def propose_all(store, modules):
    """Recompute rule proposals for events and assignments from all evidence.

    Deterministic: depends only on stored evidence and reviewer decisions,
    not on the order in which reports were uploaded.
    """
    proposed_events, plan = set(), []
    docs = {d["doc_id"]: d for d in store.rows("SELECT * FROM documents")}
    decided = {}
    for a in store.rows("SELECT * FROM assignments WHERE status IN ('confirmed','rejected')"):
        decided.setdefault((a["item_kind"], a["item_key"]), {})[a["event_id"]] = a["status"]

    # Pass 1: events defined directly by report structure (document-level outbreaks, rows, signals)
    doc_event = {}
    for doc_id, d in sorted(docs.items()):
        m = modules.get(d["module"])
        if not m:
            continue
        for rule in m.cfg.get("event_rules", []):
            if rule.get("match") == "document":
                ep = d.get("episode") or "unnumbered"
                key = f"outbreak_episode|{rule['disease']}|{rule['country']}|{ep}"
                eid = _ensure_event(store, key, "outbreak_episode", rule["disease"], [rule["country"]],
                                    _title(rule, episode=ep, disease=rule["disease"]),
                                    f"Report family {m.family_label}; episode '{ep}' stated in report heading", proposed_events)
                doc_event[doc_id] = (eid, rule)
    cands = store.rows("SELECT * FROM candidates ORDER BY item_key")
    lookups = []
    for c in cands:
        d = docs.get(c["doc_id"])
        m = modules.get(d["module"]) if d else None
        if not m or c["status"] == "rejected" or not c["disease"] or c["disease"] == "unknown" or c["indicator"] == "unknown":
            continue  # unmapped rows or unattributed claims never define or join an event
        if c["doc_id"] in doc_event:
            eid, rule = doc_event[c["doc_id"]]
            if c["disease"] == rule["disease"]:
                _propose(store, "candidate", c["item_key"], eid, "observation in a report dedicated to this outbreak", plan)
            continue
        for rule in m.cfg.get("event_rules", []):
            if rule.get("match") == "row" and c["table_group"] in rule.get("table_groups", [c["table_group"]]):
                key = f"surveillance_stream|{c['disease']}|{rule['country']}|{rule['system']}"
                eid = _ensure_event(store, key, "surveillance_stream", c["disease"], [rule["country"]],
                                    _title(rule, disease=c["disease"].replace("_", " ")),
                                    f"Row label in {m.family_label}", proposed_events)
                _propose(store, "candidate", c["item_key"], eid, f"row '{(c['raw_label'] or '').split('|')[0].strip()}' of the multi-disease table", plan)
                break
            if rule.get("match") == "evidence_lookup" and c["table_group"] == "narrative":
                lookups.append(c)
                break
    # Lookups run after all structural events exist, so the result does not depend on order
    for c in lookups:
        country = c["geo"] if c["geo_level"] == "national" else None
        hits = _candidates_for_lookup(store, c["disease"], country, proposed_events)
        if len(hits) == 1:
            _propose(store, "candidate", c["item_key"], hits[0]["event_id"],
                     f"disease '{c['disease']}' and country '{country}' stated in the source text; only one matching event", plan)

    # Pass 2: statements
    for st in store.rows("SELECT * FROM statements ORDER BY item_key"):
        d = docs.get(st["doc_id"])
        m = modules.get(d["module"]) if d else None
        if not m or st["status"] == "rejected":
            continue
        diseases = [x for x in (st["disease"] or "").split(",") if x]
        geos = json.loads(st["geos"] or "[]")
        if st["statement_type"] == "signal":
            if st["state"] == "none_publicly_identified":
                continue
            hazard = diseases[0] if diseases else "undetermined"
            region = geos[0] if geos else "unspecified_region"
            key = f"public_health_signal|{hazard}|{region}|{d['period_end']}"
            eid = _ensure_event(store, key, "public_health_signal", None if hazard == "undetermined" else hazard, [region],
                                f"Signal: {hazard.replace('_', ' ')} ({region.replace('_', ' ')}), week ending {d['period_end']}",
                                "Listed in the WHO table of selected new signals", proposed_events)
            _propose(store, "statement", st["item_key"], eid, "row of the signals table", plan)
            continue
        if st["doc_id"] in doc_event:
            eid, rule = doc_event[st["doc_id"]]
            if not diseases or diseases == [rule["disease"]]:
                _propose(store, "statement", st["item_key"], eid,
                         "statement in a report dedicated to this outbreak" + ("" if diseases else "; no other disease named"), plan)
                continue
        if len(diseases) != 1:
            continue
        default_country = m.cfg.get("default_country")
        countries = [g for g in geos if g in ("drc", "uganda", "south_sudan", "kenya", "rwanda", "burundi", "central_african_republic",
                                                "tanzania", "zambia", "angola", "russian_federation")]
        if not countries and default_country and (m.cfg.get("provinces_imply_default_country", True)):
            countries = [default_country]
        if len(countries) != 1:
            continue
        # Same-document stream first (weekly profile), then a unique existing event
        key = None
        for rule in m.cfg.get("event_rules", []):
            if rule.get("match") == "row":
                key = f"surveillance_stream|{diseases[0]}|{rule['country']}|{rule['system']}"
        hits = []
        if key and store.conn.execute("SELECT 1 FROM events WHERE event_id=?", (event_id_for(key),)).fetchone() \
                and event_id_for(key) in proposed_events:
            hits = [{"event_id": event_id_for(key)}]
        if not hits:
            hits = _candidates_for_lookup(store, diseases[0], countries[0], proposed_events)
        if len(hits) == 1:
            _propose(store, "statement", st["item_key"], hits[0]["event_id"],
                     f"names '{diseases[0]}' in '{countries[0]}'; only one matching event", plan)

    # Apply: replace previous rule proposals, never touch reviewer decisions
    store.conn.execute("DELETE FROM assignments WHERE status='proposed' AND origin='rule'")
    for item_kind, item_key, eid, basis in plan:
        dec = decided.get((item_kind, item_key), {})
        if eid in dec:
            continue  # confirmed already, or rejected by a reviewer
        if item_kind == "candidate" and "confirmed" in dec.values():
            continue  # an observation belongs to one event; a reviewer already chose it
        store.conn.execute("INSERT OR IGNORE INTO assignments (item_kind, item_key, event_id, origin, status, basis, at) "
                           "VALUES (?,?,?,?,?,?,?)", (item_kind, item_key, eid, "rule", "proposed", basis, now()))
    # Remove unreviewed rule-made events that no longer have any evidence
    store.conn.execute("DELETE FROM events WHERE status='proposed' AND reviewer IS NULL AND event_id NOT IN "
                       "(SELECT DISTINCT event_id FROM assignments WHERE status != 'rejected')")
    store.commit()


# ---------------- reviewer decisions ----------------

def _event(store, eid):
    r = store.conn.execute("SELECT * FROM events WHERE event_id=?", (eid,)).fetchone()
    if r is None:
        raise KeyError(f"No event {eid}")
    return dict(r)


def confirm_event(store, eid, reviewer, note, include_assignments=True):
    e = _event(store, eid)
    if e["status"] == "merged":
        raise ValueError("A merged event cannot be confirmed; confirm the event it was merged into")
    store.decide("event", eid, "confirm", {"include_assignments": include_assignments}, reviewer, note)
    store.conn.execute("UPDATE events SET status='confirmed', reviewer=?, reviewed_at=?, review_note=? WHERE event_id=?",
                       (reviewer.strip(), now(), note.strip(), eid))
    n = 0
    if include_assignments:
        for a in store.rows("SELECT * FROM assignments WHERE event_id=? AND status='proposed'", (eid,)):
            if a["item_kind"] == "candidate" and store.conn.execute(
                    "SELECT 1 FROM assignments WHERE item_kind='candidate' AND item_key=? AND status='confirmed'", (a["item_key"],)).fetchone():
                continue
            store.conn.execute("UPDATE assignments SET status='confirmed', reviewer=?, at=?, note=? WHERE item_kind=? AND item_key=? AND event_id=?",
                               (reviewer.strip(), now(), "confirmed with event", a["item_kind"], a["item_key"], eid))
            n += 1
    store.commit()
    return n


def edit_event(store, eid, fields, reviewer, note):
    allowed = {"title", "event_type", "disease", "geo_scope", "start_date", "end_date"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"Cannot edit: {', '.join(sorted(bad))}")
    if "event_type" in fields and fields["event_type"] not in EVENT_TYPES:
        raise ValueError("Unknown event type")
    if "geo_scope" in fields and (not isinstance(fields["geo_scope"], list) or not all(isinstance(x, str) for x in fields["geo_scope"])):
        raise ValueError("geo_scope must be a list of places")
    old = _event(store, eid)
    store.decide("event", eid, "edit", {"old": {k: old[k] for k in fields}, "new": fields}, reviewer, note)
    for k, v in fields.items():
        store.conn.execute(f"UPDATE events SET {k}=? WHERE event_id=?", (json.dumps(v) if k == "geo_scope" else v, eid))
    store.conn.execute("UPDATE events SET reviewer=?, reviewed_at=? WHERE event_id=?", (reviewer.strip(), now(), eid))
    store.commit()


def merge_events(store, source, target, reviewer, note):
    """Merge `source` into `target`. The source keeps its identifier and history; lookups follow the merge."""
    if source == target:
        raise ValueError("Choose two different events")
    s, t = _event(store, source), _event(store, target)
    if store.resolve_event(target) == source:
        raise ValueError("That merge would create a cycle")
    store.decide("event", source, "merge", {"into": target}, reviewer, note)
    aliases = sorted(set(json.loads(t["aliases"] or "[]")) | set(json.loads(s["aliases"] or "[]")))
    store.conn.execute("UPDATE events SET status='merged', merged_into=?, reviewer=?, reviewed_at=?, review_note=? WHERE event_id=?",
                       (target, reviewer.strip(), now(), note.strip(), source))
    store.conn.execute("UPDATE events SET aliases=? WHERE event_id=?", (json.dumps(aliases), target))
    store.commit()


def separate_event(store, eid, doc_ids, title, reviewer, note):
    """Move all evidence from the given documents out of `eid` into a new reviewer-defined event."""
    old = _event(store, eid)
    if not doc_ids:
        raise ValueError("Choose at least one document to separate")
    key = f"reviewer|{eid}|{'|'.join(sorted(doc_ids))}"
    new = event_id_for(key)
    store.conn.execute("INSERT OR IGNORE INTO events (event_id, event_key, event_type, disease, geo_scope, title, aliases, status, basis, created_at, reviewer, reviewed_at, review_note) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (new, key, old["event_type"], old["disease"], old["geo_scope"], title, json.dumps([key]), "confirmed",
                        f"separated from {eid}", now(), reviewer.strip(), now(), note.strip()))
    moved = 0
    for kind, table in (("candidate", "candidates"), ("statement", "statements")):
        keys = [r["item_key"] for r in store.rows(f"SELECT item_key FROM {table} WHERE doc_id IN ({','.join('?' * len(doc_ids))})", tuple(doc_ids))]
        for k in keys:
            if store.conn.execute("SELECT 1 FROM assignments WHERE item_kind=? AND item_key=? AND event_id=? AND status!='rejected'",
                                  (kind, k, eid)).fetchone():
                store.conn.execute("UPDATE assignments SET status='rejected', origin='reviewer', reviewer=?, at=?, note=? "
                                   "WHERE item_kind=? AND item_key=? AND event_id=?", (reviewer.strip(), now(), f"moved to {new}", kind, k, eid))
                store.conn.execute("INSERT OR REPLACE INTO assignments (item_kind, item_key, event_id, origin, status, basis, reviewer, at, note) "
                                   "VALUES (?,?,?,?,?,?,?,?,?)", (kind, k, new, "reviewer", "confirmed", f"separated from {eid}", reviewer.strip(), now(), note.strip()))
                moved += 1
    store.decide("event", eid, "separate", {"new_event": new, "documents": sorted(doc_ids), "moved": moved}, reviewer, note)
    store.commit()
    return new, moved


def set_assignment(store, item_kind, item_key, eid, action, reviewer, note):
    """Reviewer adds, confirms or rejects an item's link to an event.

    A statement may be linked to several events (a shared constraint). An
    observation belongs to one event: confirming one rejects its other links.
    """
    if item_kind not in ("candidate", "statement"):
        raise ValueError("Unknown item kind")
    if action not in ("confirm", "reject"):
        raise ValueError("Unknown assignment action")
    _event(store, eid)
    table = "candidates" if item_kind == "candidate" else "statements"
    if not store.conn.execute(f"SELECT 1 FROM {table} WHERE item_key=?", (item_key,)).fetchone():
        raise KeyError("No such item")
    store.decide("assignment", f"{item_kind}:{item_key}", action, {"event_id": eid}, reviewer, note)
    status = "confirmed" if action == "confirm" else "rejected"
    store.conn.execute("INSERT INTO assignments (item_kind, item_key, event_id, origin, status, basis, reviewer, at, note) VALUES (?,?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(item_kind, item_key, event_id) DO UPDATE SET status=excluded.status, reviewer=excluded.reviewer, at=excluded.at, note=excluded.note",
                       (item_kind, item_key, eid, "reviewer", status, "reviewer decision", reviewer.strip(), now(), note.strip()))
    if item_kind == "candidate" and action == "confirm":
        store.conn.execute("UPDATE assignments SET status='rejected', reviewer=?, at=?, note=? WHERE item_kind='candidate' AND item_key=? "
                           "AND event_id != ? AND status != 'rejected'", (reviewer.strip(), now(), f"observation assigned to {eid}", item_key, eid))
    store.commit()


def item_events(store):
    """Map (item_kind, item_key) -> list of {event_id, status, origin, basis}, following merges; rejected links excluded."""
    out = {}
    for a in store.rows("SELECT * FROM assignments WHERE status != 'rejected'"):
        out.setdefault((a["item_kind"], a["item_key"]), []).append(
            {"event_id": store.resolve_event(a["event_id"]), "status": a["status"], "origin": a["origin"], "basis": a["basis"]})
    return out

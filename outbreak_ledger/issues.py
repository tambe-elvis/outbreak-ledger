"""Issues and relationships between dated qualitative claims.

The unit is a dated source claim (a statement) linked to events, places and
a response pillar. Challenge statements describing the same problem across
reports form an issue with a stable identifier. Other statements relate to an
issue through typed relationships:

- updates: a later report restates the issue;
- addresses: an action responding to the issue (does not establish resolution);
- reports_resolution: a source states the problem was resolved;
- contradicts: same reporting period, incompatible states;
- recurs: the issue is reported again after a reported resolution.

Rules propose memberships and relationships; reviewers confirm or reject
them. Proposals are recomputed deterministically from all evidence, sorted
by reporting period, publication and stable keys, so upload order does not
matter. Confirmed and rejected decisions are never overwritten.

Three things are kept apart: the state asserted by the source (on each
statement), the reviewer's assessment (on the issue), and the derived status
computed here, which is always labelled provisional while it rests on
unconfirmed links. Absence from a later report never implies resolution.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date

from .events import item_events
from .qualitative import signature, vocab
from .store import now

REL_KINDS = ("updates", "addresses", "reports_resolution", "contradicts", "recurs")
ONGOING = ("ongoing", "asserted", "worsening", "new")
ASSESSMENTS = ("ongoing", "resolved", "partly_resolved", "not_an_issue", "insufficient_evidence")


def _d(x):
    try:
        return date.fromisoformat(x)
    except (TypeError, ValueError):
        return None


def issue_id_for(statement_key):
    return "IS-" + hashlib.sha1(statement_key.encode()).hexdigest()[:10].upper()


def rel_key(kind, a, b):
    return "R-" + hashlib.sha1(f"{kind}|{a}|{b}".encode()).hexdigest()[:16]


def _load(store):
    ev = item_events(store)
    rows = store.rows("SELECT s.*, d.period_end AS doc_period, d.publication_date, d.report_no, d.filename "
                      "FROM statements s JOIN documents d USING(doc_id) WHERE s.status != 'rejected'")
    for r in rows:
        for k in ("topics", "geos", "health_zones"):
            r[k] = json.loads(r[k] or "[]")
        r["events"] = sorted({l["event_id"] for l in ev.get(("statement", r["item_key"]), [])})
        r["event_links"] = ev.get(("statement", r["item_key"]), [])
        r["places"] = set(r["geos"]) | set(r["health_zones"])
    rows.sort(key=lambda r: (r["doc_period"] or "", r["publication_date"] or "", r["page"] or 0, r["item_key"]))
    return rows


def _scope_ok(a_events, a_places, a_topics, b_events, b_places, b_topics):
    if set(a_events) != set() or set(b_events) != set():
        if not set(a_events) & set(b_events):
            return False
    if not set(a_topics) & set(b_topics):
        return False
    if (a_places or b_places) and not set(a_places) & set(b_places):
        return False
    return True


def build(store, threshold=0.3):
    """Recompute issue memberships and relationship proposals. Returns issues with derived status."""
    v = vocab()
    st = _load(store)
    by_key = {s["item_key"]: s for s in st}
    members_db = store.rows("SELECT * FROM issue_members")
    confirmed = {m["statement_key"]: m["issue_id"] for m in members_db if m["status"] == "confirmed"}
    rejected_pairs = {(m["issue_id"], m["statement_key"]) for m in members_db if m["status"] == "rejected"}
    issues = {}

    def new_issue(iid):
        return issues.setdefault(iid, {"issue_id": iid, "members": [], "topics": set(), "places": set(), "events": set(), "sig": set()})

    def add(iid, s, origin):
        it = new_issue(iid)
        it["members"].append((s, origin))
        it["topics"] |= set(s["topics"])
        it["places"] |= s["places"]
        it["events"] |= set(s["events"])
        it["sig"] = signature(s["quote"], v)
        it["last_period"] = s["doc_period"] or ""

    challenges = [s for s in st if s["statement_type"] == "challenge" and s["topics"]]
    for s in challenges:
        if s["item_key"] in confirmed:
            add(confirmed[s["item_key"]], s, "confirmed")
            continue
        sig = signature(s["quote"], v)
        best, best_j = None, threshold
        for iid, it in sorted(issues.items()):
            if (iid, s["item_key"]) in rejected_pairs or it.get("last_period", "") >= (s["doc_period"] or ""):
                continue
            last = it["members"][-1][0]
            if last["module"] != s["module"] or last["issuer"] != s["issuer"]:
                continue
            if not _scope_ok(it["events"], it["places"], it["topics"], s["events"], s["places"], s["topics"]):
                continue
            j = len(sig & it["sig"]) / max(1, len(sig | it["sig"]))
            if j >= best_j:
                best, best_j = iid, j
        add(best or issue_id_for(s["item_key"]), s, "proposed")

    store.conn.execute("DELETE FROM issue_members WHERE status='proposed'")
    for iid, it in issues.items():
        store.conn.execute("INSERT OR IGNORE INTO issues (issue_id, title) VALUES (?,?)", (iid, it["members"][0][0]["quote"][:140]))
        for s, origin in it["members"]:
            if origin == "proposed":
                store.conn.execute("INSERT OR IGNORE INTO issue_members (issue_id, statement_key, origin, status, at) VALUES (?,?,?,?,?)",
                                   (iid, s["item_key"], "rule", "proposed", now()))

    # ---- relationship proposals ----
    decided = {r["rel_key"] for r in store.rows("SELECT rel_key FROM relations WHERE from_kind='statement' AND status IN ('confirmed','rejected')")}
    store.conn.execute("DELETE FROM relations WHERE from_kind='statement' AND origin='rule' AND status='proposed'")

    def propose(kind, a, b, basis):
        k = rel_key(kind, a["item_key"], b["item_key"])
        if k in decided:
            return
        store.conn.execute("INSERT OR IGNORE INTO relations (rel_key, kind, from_kind, from_key, to_kind, to_key, origin, status, basis, at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (k, kind, "statement", a["item_key"], "statement", b["item_key"], "rule", "proposed", basis, now()))

    place_words = set()
    for key in ("provinces", "countries", "regions"):
        for terms_ in v[key].values():
            for t in terms_:
                place_words |= set(t.split()) | {t.replace(" ", "-")}
    place_words |= {"riposte", "activites", "niveau", "depuis", "personnes", "total", "nombre", "taux", "suivi", "rapport", "rapports"}
    member_of = {}
    for iid, it in issues.items():
        ms = [s for s, _ in it["members"]]
        for s in ms:
            member_of[s["item_key"]] = iid
        for earlier, later in zip(ms, ms[1:]):
            propose("updates", later, earlier, "same issue reported again in a later report")

    others = [s for s in st if s["statement_type"] not in ("signal", "published_product") and s["item_key"] not in member_of]
    for iid, it in issues.items():
        ms = [s for s, _ in it["members"]]
        first = ms[0]["doc_period"] or ""
        for s in others:
            p = s["doc_period"] or ""
            if not _scope_ok(it["events"], it["places"], it["topics"], s["events"], s["places"], s["topics"]):
                continue
            prior = [m for m in ms if (m["doc_period"] or "") <= p]
            if not prior:
                continue
            target = prior[-1]
            # Beyond a shared topic and place, require shared wording (at least two content words)
            shared = (signature(s["quote"], v) & set().union(*(signature(m["quote"], v) for m in ms))) - place_words - s["places"] - it["places"]
            if len(shared) < 2:
                continue
            if s["state"] == "resolved" and p > first:
                propose("reports_resolution", s, target, "same event, place and topic; source states resolution")
            elif s["statement_type"] == "action" and p >= first:
                propose("addresses", s, target, "action on the same event, place and topic; does not establish resolution")
            elif s["state"] in ("resolved", "denied") and p == first:
                for m in ms:
                    if (m["doc_period"] or "") == p and m["state"] in ONGOING:
                        propose("contradicts", s, m, "same reporting period, incompatible states")
    store.commit()

    # recurrence after a reported resolution (uses current relations)
    rels = store.rows("SELECT * FROM relations WHERE from_kind='statement' AND status != 'rejected'")
    res_by_issue = defaultdict(list)
    for r in rels:
        if r["kind"] == "reports_resolution" and r["to_key"] in member_of and r["from_key"] in by_key:
            res_by_issue[member_of[r["to_key"]]].append(by_key[r["from_key"]])
    for iid, it in issues.items():
        for res in res_by_issue.get(iid, []):
            for s, _ in it["members"]:
                if (s["doc_period"] or "") > (res["doc_period"] or ""):
                    propose("recurs", s, res, "issue reported again after a reported resolution")
                    break
    store.commit()
    return derive(store, issues, by_key)


def derive(store, issues, by_key):
    rels = store.rows("SELECT * FROM relations WHERE from_kind='statement' AND status != 'rejected'")
    assess = {r["issue_id"]: r for r in store.rows("SELECT * FROM issues")}
    latest_period = {}
    for d in store.rows("SELECT module, issuer, MAX(period_end) AS p FROM documents GROUP BY module, issuer"):
        latest_period[(d["module"], d["issuer"])] = d["p"]
    member_of = {s["item_key"]: iid for iid, it in issues.items() for s, _ in it["members"]}
    linked = defaultdict(list)
    for r in rels:
        iid = member_of.get(r["to_key"])
        if iid and r["from_key"] in by_key and r["kind"] != "updates":
            linked[iid].append((r, by_key[r["from_key"]]))
    out = []
    for iid, it in issues.items():
        ms = [s for s, _ in it["members"]]
        provisional = any(o != "confirmed" for _, o in it["members"]) or any(r["status"] == "proposed" for r, _ in linked[iid])
        ongoing = [s for s in ms if s["state"] in ONGOING]
        last_ongoing = max((s["doc_period"] for s in ongoing if s["doc_period"]), default=None)
        resolutions = [s for r, s in linked[iid] if r["kind"] == "reports_resolution"] + [s for s in ms if s["state"] == "resolved"]
        last_res = max((s["doc_period"] for s in resolutions if s["doc_period"]), default=None)
        actions = [s for r, s in linked[iid] if r["kind"] == "addresses"]
        last_action = max((s["doc_period"] for s in actions if s["doc_period"]), default=None)
        contradictions = [s for r, s in linked[iid] if r["kind"] == "contradicts"]
        latest_upload = latest_period.get((ms[-1]["module"], ms[-1]["issuer"]))
        if last_res and (not last_ongoing or last_res > last_ongoing):
            derived = f"Resolution reported on {last_res}"
            code = "resolution_reported"
        elif last_res and last_res == last_ongoing:
            derived = f"Contradictory reports on {last_res}: ongoing and resolved"
            code = "contradictory"
        elif last_ongoing:
            if last_ongoing == latest_upload:
                derived = f"Reported ongoing in the latest uploaded report ({last_ongoing})"
                code = "ongoing_latest"
            else:
                age = (_d(latest_upload) - _d(last_ongoing)).days if _d(latest_upload) and _d(last_ongoing) else None
                derived = (f"Last reported ongoing on {last_ongoing}; no later update in the uploaded reports"
                           + (f" ({age} day{'' if age == 1 else 's'} before the latest uploaded report of this family)" if age is not None else ""))
                code = "ongoing_not_updated"
        else:
            derived, code = "State not stated in the source", "unknown"
        if last_action and code in ("ongoing_latest", "ongoing_not_updated") and last_action >= (last_ongoing or ""):
            derived += f". Action reported on {last_action}; an action does not establish resolution"
        if contradictions:
            derived += f". {len(contradictions)} contradicting statement(s) recorded"
        a = assess.get(iid, {})
        out.append({"issue_id": iid, "title": a.get("title") or ms[0]["quote"][:140], "module": ms[-1]["module"], "issuer": ms[-1]["issuer"],
                    "events": sorted(it["events"]), "topics": sorted(it["topics"]), "places": sorted(it["places"]),
                    "first_seen": ms[0]["doc_period"], "last_seen": ms[-1]["doc_period"], "reports": len({s["doc_id"] for s in ms}),
                    "members": [{"statement_id": s["id"], "item_key": s["item_key"], "period": s["doc_period"], "state": s["state"],
                                 "quote": s["quote"], "membership": o, "filename": s["filename"], "page": s["page"]} for s, o in it["members"]],
                    "relations": [{"rel_key": r["rel_key"], "kind": r["kind"], "status": r["status"], "basis": r["basis"],
                                   "statement_id": s["id"], "period": s["doc_period"], "quote": s["quote"], "filename": s["filename"]}
                                  for r, s in sorted(linked[iid], key=lambda x: x[1]["doc_period"] or "")],
                    "latest_source_state": ms[-1]["state"], "derived_status": derived, "derived_code": code,
                    "provisional": provisional, "last_evidence": max(filter(None, [last_ongoing, last_res, last_action, ms[-1]["doc_period"]]), default=None),
                    "assessment": a.get("assessment"), "assessed_by": a.get("assessed_by"), "assessed_at": a.get("assessed_at"),
                    "assessment_note": a.get("assessment_note")})
    return sorted(out, key=lambda x: (-x["reports"], x["issue_id"]))


# ---------------- reviewer decisions ----------------

def set_membership(store, issue_id, statement_key, action, reviewer, note):
    if action not in ("confirm", "reject"):
        raise ValueError("Unknown membership action")
    if not store.conn.execute("SELECT 1 FROM statements WHERE item_key=?", (statement_key,)).fetchone():
        raise KeyError("No such statement")
    store.decide("issue_member", f"{issue_id}:{statement_key}", action, {}, reviewer, note)
    store.conn.execute("INSERT OR IGNORE INTO issues (issue_id, title) VALUES (?, NULL)", (issue_id,))
    status = "confirmed" if action == "confirm" else "rejected"
    store.conn.execute("INSERT INTO issue_members (issue_id, statement_key, origin, status, reviewer, at, note) VALUES (?,?,?,?,?,?,?) "
                       "ON CONFLICT(issue_id, statement_key) DO UPDATE SET status=excluded.status, origin='reviewer', reviewer=excluded.reviewer, at=excluded.at, note=excluded.note",
                       (issue_id, statement_key, "reviewer", status, reviewer.strip(), now(), note.strip()))
    if action == "confirm":
        store.conn.execute("UPDATE issue_members SET status='rejected', origin='reviewer', reviewer=?, at=?, note=? "
                           "WHERE statement_key=? AND issue_id != ? AND status='confirmed'", (reviewer.strip(), now(), f"moved to {issue_id}", statement_key, issue_id))
    store.commit()


def set_relation(store, rel_key_, action, reviewer, note, create=None):
    """Confirm or reject a proposed relation, or create one (create = {kind, from_key, to_key})."""
    if create:
        if create.get("kind") not in REL_KINDS + ("supersedes",):
            raise ValueError("Unknown relation type")
        kind = create["kind"]
        fk, tk = create["from_key"], create["to_key"]
        fkind = "candidate" if fk.startswith("C-") else "statement"
        tkind = "candidate" if tk.startswith("C-") else "statement"
        rel_key_ = rel_key(kind, fk, tk)
        store.conn.execute("INSERT OR IGNORE INTO relations (rel_key, kind, from_kind, from_key, to_kind, to_key, origin, status, basis, at) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?)", (rel_key_, kind, fkind, fk, tkind, tk, "reviewer", "proposed", "created by reviewer", now()))
        action = "confirm"
    if action not in ("confirm", "reject"):
        raise ValueError("Unknown relation action")
    row = store.conn.execute("SELECT * FROM relations WHERE rel_key=?", (rel_key_,)).fetchone()
    if row is None:
        raise KeyError("No such relation")
    store.decide("relation", rel_key_, action, {"kind": row["kind"], "from": row["from_key"], "to": row["to_key"]}, reviewer, note)
    store.conn.execute("UPDATE relations SET status=?, reviewer=?, at=?, note=? WHERE rel_key=?",
                       ("confirmed" if action == "confirm" else "rejected", reviewer.strip(), now(), note.strip(), rel_key_))
    store.commit()
    return rel_key_


def assess_issue(store, issue_id, assessment, reviewer, note):
    if assessment not in ASSESSMENTS:
        raise ValueError("Unknown assessment")
    store.decide("issue", issue_id, "assess", {"assessment": assessment}, reviewer, note)
    store.conn.execute("INSERT OR IGNORE INTO issues (issue_id) VALUES (?)", (issue_id,))
    store.conn.execute("UPDATE issues SET assessment=?, assessed_by=?, assessed_at=?, assessment_note=? WHERE issue_id=?",
                       (assessment, reviewer.strip(), now(), note.strip(), issue_id))
    store.commit()

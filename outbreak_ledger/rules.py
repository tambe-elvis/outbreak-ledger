"""Deterministic rule checks.

Rules flag values for human review and explain why; they never change or
delete a value. Conflicting sources and conflicting tables are retained side
by side rather than resolved.
"""
import json
from collections import defaultdict
from datetime import date

from .store import Store
from .views import select_claims, effective

RULE_FLAGS = {"cumulative_decrease", "explained_by_revision_note", "explained_by_adjustment_statement",
              "sudden_jump", "cumulative_jump", "subtotal_mismatch", "child_sum_mismatch",
              "conflicting_source", "cross_table_mismatch", "new_cumulative_mismatch", "reporting_gap",
              "revised_in_later_report", "cross_source_difference", "related_definitions_differ",
              "consistent_with_revision"}
STEP_DAYS = {"daily": 1, "weekly": 7}


def complete(c):
    return c["value"] is not None and "unknown" not in (c["indicator"], c["classification"], c["count_type"])


def meaning_key(c):
    return (c["disease"], c["geo"], c["geo_level"], c["indicator"], c["classification"], c["count_type"])


def run_rules(store: Store, module) -> dict:
    cands = store.rows("SELECT c.* FROM candidates c JOIN documents d USING(doc_id) WHERE d.module=? OR d.module IS NULL", (module.name,))
    for c in cands:
        c["value"] = effective(c)
    flags = {c["id"]: [f for f in json.loads(c["flags"]) if f not in RULE_FLAGS] for c in cands}
    notes = defaultdict(list)
    revs = store.rows("SELECT r.* FROM revision_notes r JOIN documents d USING(doc_id) WHERE d.module=? OR d.module IS NULL", (module.name,))
    pub = {d["doc_id"]: (d["publication_date"] or "", d["report_no"] or 0, d["filename"])
           for d in store.rows("SELECT doc_id, publication_date, report_no, filename FROM documents WHERE module=? OR module IS NULL", (module.name,))}
    rules = module.rules
    conflicts, findings = [], []

    def flag(c, name, note=None):
        flags[c["id"]].append(name)
        if note:
            notes[c["id"]].append(note)

    def adjustment_for(c):
        return [r for r in revs if r["doc_id"] == c["doc_id"] and (r["kind"] or "").startswith("stated_adjustment")
                and (c["is_total"] or r["geo"] in (c["geo"], c["parent_geo"], "unspecified"))]

    usable = [c for c in cands if complete(c) and c["status"] != "rejected"]

    # Select an unambiguous effective claim per issuer, meaning and period.
    # Approved claims take precedence; contradictory approved claims abstain.
    chosen, ambiguous = select_claims(usable, approved_only=False)
    primary = {meaning_key(c) + (c["issuer"], c["period_end"]): c for c in chosen}
    for conflict in ambiguous:
        for cid in conflict['ids']:
            flags[cid].append('conflicting_source')
            notes[cid].append('competing effective claims; analytical view abstains')

    # 1. Time series within one issuer
    series = defaultdict(list)
    for c in primary.values():
        series[(c["issuer"],) + meaning_key(c)].append(c)
    new_lookup = {(c["issuer"], c["disease"], c["table_group"], c["geo"], c["geo_level"], c["classification"], c["period_end"]): c
                  for c in primary.values() if c["indicator"] == "cases" and c["count_type"] == "new"}
    for key, items in series.items():
        items.sort(key=lambda x: x["period_end"] or "")
        for i in range(1, len(items)):
            prev, cur = items[i - 1], items[i]
            gap_days = None
            if prev["period_end"] and cur["period_end"]:
                gap_days = (date.fromisoformat(cur["period_end"]) - date.fromisoformat(prev["period_end"])).days
            step = STEP_DAYS.get(module.period_mode)
            if step and gap_days and gap_days > step and cur["table_group"] != "narrative":
                flag(cur, "reporting_gap", f"no value for this series in the previous period; last value {gap_days} days earlier")
            if cur["count_type"] == "cumulative":
                inc = cur["value"] - prev["value"]
                if inc < 0:
                    flag(cur, "cumulative_decrease", f"fell from {prev['value']} to {cur['value']} ({inc})")
                    if any(r["kind"] == "stated_revision" and r["geo"] == cur["geo"] and r["old_value"] == prev["value"]
                           and r["new_value"] == cur["value"] and r["doc_id"] == cur["doc_id"] for r in revs):
                        flag(cur, "explained_by_revision_note", "matches a revision stated in the report text")
                if i >= 2:
                    prev_inc = prev["value"] - items[i - 2]["value"]
                    if inc >= rules.get("cumulative_jump_min", 50) and prev_inc >= 0 and \
                            inc > rules.get("cumulative_jump_factor", 5) * max(prev_inc, 1):
                        flag(cur, "cumulative_jump", f"increase of {inc} after previous increase of {prev_inc}")
                if (inc < 0 or "cumulative_jump" in flags[cur["id"]]) and adjustment_for(cur):
                    flag(cur, "explained_by_adjustment_statement",
                         "possibly related adjustment statement; magnitude unstated, explanation unverified")
                if cur["indicator"] == "cases" and (step is None or gap_days == step):
                    n = new_lookup.get((cur["issuer"], cur["disease"], cur["table_group"], cur["geo"], cur["geo_level"], cur["classification"], cur["period_end"]))
                    if n is not None and inc != n["value"]:
                        msg = (f"cumulative rose by {inc} but new cases reported = {n['value']} "
                               f"(difference {inc - n['value']:+d})")
                        flag(cur, "new_cumulative_mismatch", msg)
                        # A later report may have revised the previous period's count: the
                        # cumulative then absorbs the revision. Check whether that explains it.
                        prev_new = [x for x in usable if x["issuer"] == cur["issuer"] and x["disease"] == cur["disease"]
                                    and x["classification"] == cur["classification"] and x["indicator"] == "cases"
                                    and x["count_type"] == "new" and x["period_end"] == prev["period_end"]
                                    and x["table_group"] == cur["table_group"] and x["geo"] == cur["geo"]]
                        if len({x["doc_id"] for x in prev_new}) > 1:
                            ordered = sorted(prev_new, key=lambda x: pub.get(x["doc_id"], ("", 0, "")))
                            delta = ordered[-1]["value"] - ordered[0]["value"]
                            if delta == inc - n["value"]:
                                flag(cur, "consistent_with_revision",
                                     f"difference equals the later revision of the previous period ({delta:+d})")
                        findings.append({"type": "new_cumulative_mismatch", "candidate": cur["id"], "geo": cur["geo"],
                                         "period_end": cur["period_end"], "note": msg})
            if cur["count_type"] == "new" and prev["value"] > 0 and cur["value"] >= rules.get("jump_min_value", 10) \
                    and cur["value"] > rules.get("jump_factor", 5) * prev["value"]:
                flag(cur, "sudden_jump", f"{cur['value']} after {prev['value']}")
        for c in items:
            if "cumulative_jump" in flags[c["id"]] or "cumulative_decrease" in flags[c["id"]]:
                findings.append({"type": "cumulative_break", "candidate": c["id"], "geo": c["geo"],
                                 "period_end": c["period_end"], "note": "; ".join(notes[c["id"]])})

    # 2. Totals and child sums within one table group of one document
    groups = defaultdict(list)
    for c in usable:
        groups[(c["doc_id"], c["table_group"], c["indicator"], c["classification"], c["count_type"])].append(c)
    for items in groups.values():
        top = [c for c in items if not c["is_total"] and not c["parent_geo"]]
        for t in (c for c in items if c["is_total"]):
            ssum = sum(c["value"] for c in top)
            if top and ssum != t["value"]:
                flag(t, "subtotal_mismatch", f"rows sum to {ssum}, total states {t['value']}")
        children = defaultdict(list)
        for c in items:
            if c["parent_geo"]:
                children[c["parent_geo"]].append(c)
        for parent in (c for c in items if c["geo"] in children and not c["parent_geo"]):
            ssum = sum(c["value"] for c in children[parent["geo"]])
            if ssum != parent["value"]:
                flag(parent, "child_sum_mismatch",
                     f"health zones (incl. unallocated) sum to {ssum}, province row states {parent['value']}")

    # 3. Same document, same meaning, different tables
    same = defaultdict(list)
    for c in usable:
        same[(c["doc_id"],) + meaning_key(c)].append(c)
    for items in same.values():
        if len({c["table_group"] for c in items}) > 1 and len({c["value"] for c in items}) > 1:
            desc = "; ".join(f"{c['table_group']}={c['value']}" for c in items)
            for c in items:
                flag(c, "cross_table_mismatch", f"tables disagree within the report: {desc}")
            findings.append({"type": "cross_table_mismatch", "candidate": items[0]["id"], "geo": items[0]["geo"],
                             "period_end": items[0]["period_end"], "note": desc})

    # 4. Conflicts across issuers for the same meaning and period
    by_meaning = defaultdict(list)
    for c in primary.values():
        by_meaning[meaning_key(c) + (c["period_end"],)].append(c)
    for key, items in by_meaning.items():
        if len({c["issuer"] for c in items}) > 1 and len({c["value"] for c in items}) > 1:
            for c in items:
                flag(c, "conflicting_source")
            conflicts.append({"key": key, "claims": [(c["issuer"], c["value"], c["id"]) for c in items]})

    # 5. Same issuer reports a different value for the same meaning and period in a later report
    same_period = defaultdict(list)
    for c in usable:
        same_period[(c["issuer"],) + meaning_key(c) + (c["period_end"],)].append(c)
    for items in same_period.values():
        docs = sorted({c["doc_id"] for c in items}, key=lambda d: pub.get(d, ("", 0, "")))
        if len(docs) < 2 or len({c["value"] for c in items}) < 2:
            continue
        first = next(c for c in items if c["doc_id"] == docs[0])
        for c in items:
            if c["doc_id"] != docs[0] and c["value"] != first["value"]:
                msg = (f"{pub[docs[0]][2]} reported {first['value']} for this period; "
                       f"{pub[c['doc_id']][2]} reports {c['value']} ({c['value'] - first['value']:+d})")
                flag(c, "revised_in_later_report", msg)
                flag(first, "revised_in_later_report", msg)
                findings.append({"type": "revised_in_later_report", "candidate": c["id"], "geo": c["geo"],
                                 "period_end": c["period_end"], "note": msg})

    for cid, f in flags.items():
        store.set_flags(cid, f, " | ".join(dict.fromkeys(notes[cid])))
    store.commit()
    return {"checked": len(cands), "flagged": sum(1 for f in flags.values() if f),
            "conflicts": conflicts, "findings": findings}


def run_cross_source(store) -> list:
    """Checks that span report families: different issuers, or table versus narrative.

    Run after each module's own rules. Values are never merged: differences are
    flagged on every claim involved, with the competing figures in the note.
    """
    rows = [c for c in store.rows("SELECT c.*, d.module, d.filename FROM candidates c JOIN documents d USING(doc_id)")
            if complete({**c, "value": effective(c)}) and c["status"] != "rejected"]
    for c in rows:
        c["value"] = effective(c)
    add = defaultdict(list)
    findings = []
    by = defaultdict(list)
    for c in rows:
        by[meaning_key(c) + (c["period_end"],)].append(c)
    for key, items in by.items():
        if len({c["issuer"] for c in items}) > 1 and len({c["value"] for c in items}) > 1:
            desc = "; ".join(sorted({f"{c['issuer']} ({c['table_group']}) = {c['value']}" for c in items}))
            for c in items:
                add[c["id"]].append(("cross_source_difference", "different issuers report different values: " + desc))
            findings.append({"type": "cross_source_difference", "candidate": items[0]["id"], "geo": items[0]["geo"],
                             "period_end": items[0]["period_end"], "note": desc})
    related = defaultdict(list)
    for c in rows:
        related[(c["doc_id"], c["disease"], c["geo"], c["indicator"], c["count_type"], c["period_end"])].append(c)
    for items in related.values():
        groups = {c["table_group"] for c in items}
        classes = {c["classification"] for c in items}
        if len(groups) > 1 and len(classes) > 1 and len({c["value"] for c in items}) > 1:
            desc = "; ".join(sorted({f"{c['table_group']} {c['classification']} = {c['value']}" for c in items}))
            for c in items:
                add[c["id"]].append(("related_definitions_differ",
                                     "same report, related figures under different definitions (not merged): " + desc))
            findings.append({"type": "related_definitions_differ", "candidate": items[0]["id"], "geo": items[0]["geo"],
                             "period_end": items[0]["period_end"], "note": desc})
    for cid, extra in add.items():
        row = store.rows("SELECT flags, flag_notes FROM candidates WHERE id=?", (cid,))[0]
        f = json.loads(row["flags"]) + [x[0] for x in extra]
        notes = " | ".join([n for n in [row["flag_notes"]] if n] + list(dict.fromkeys(x[1] for x in extra)))
        store.set_flags(cid, f, notes)
    store.commit()
    return findings


def run_all_rules(store, modules: dict) -> dict:
    """Run each present module's rules, then cross-source checks. modules: name -> Module."""
    present = [r["module"] for r in store.rows("SELECT DISTINCT module FROM documents")]
    out = {"checked": 0, "flagged": 0, "conflicts": [], "findings": []}
    for name in present:
        if name in modules:
            r = run_rules(store, modules[name])
            for k in ("checked", "flagged"):
                out[k] += r[k]
            out["conflicts"] += r["conflicts"]
            out["findings"] += r["findings"]
    out["findings"] += run_cross_source(store)
    out["flagged"] = sum(1 for r in store.rows("SELECT flags FROM candidates") if r["flags"] != "[]")
    return out

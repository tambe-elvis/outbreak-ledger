"""Evaluation of extraction against an expert-curated reference dataset.

Reference CSV columns: issuer, report_no, geo, indicator, classification,
count_type, value. Only observations with a complete meaning are scored;
ambiguous or unmapped columns are reported as abstentions, not errors.
"""
import csv

from .extract import verify_provenance


def evaluate(store, reference_csv: str) -> dict:
    with open(reference_csv, encoding="utf-8") as fh:
        ref = {}
        for r in csv.DictReader(fh):
            k = (r["issuer"], int(r["report_no"]), r["geo"], r["indicator"], r["classification"], r["count_type"])
            if k in ref:
                raise ValueError("Reference contains duplicate observation keys; disambiguate the benchmark before scoring")
            ref[k] = int(r["value"])
    docs = {d["doc_id"]: d for d in store.rows("SELECT doc_id, issuer, report_no FROM documents")}
    extracted, abstained = {}, 0
    competing = {}
    for c in store.rows("SELECT * FROM candidates"):
        if "unknown" in (c["indicator"], c["classification"], c["count_type"]) or c["value"] is None:
            abstained += 1
            continue
        d = docs[c["doc_id"]]
        k = (d["issuer"], d["report_no"], c["geo"], c["indicator"], c["classification"], c["count_type"])
        competing.setdefault(k, set()).add(c["value"])
    conflicting_keys = [k for k, values in competing.items() if len(values) > 1]
    extracted = {k: next(iter(values)) for k, values in competing.items() if len(values) == 1}
    correct = sum(1 for k, v in ref.items() if extracted.get(k) == v)
    wrong_value = [k for k, v in ref.items() if k in extracted and extracted[k] != v]
    missed = [k for k in ref if k not in extracted]
    spurious = [k for k in extracted if k not in ref]
    prov = verify_provenance(store)
    return {
        "reference_observations": len(ref),
        "extracted_complete_meaning": len(competing),
        "conflicting_extraction_keys": conflicting_keys,
        "correct_value_and_meaning": correct,
        "wrong_value": wrong_value,
        "missed": missed,
        "spurious": spurious,
        "abstained_cells": abstained,
        "recall": round(correct / len(ref), 3) if ref else None,
        "precision": round(correct / len(competing), 3) if competing else None,
        "provenance_verified": sum(p["provenance_ok"] for p in prov),
        "provenance_checked": len(prov),
    }


SHEET_COLS = ["candidate_id", "source_file", "report_no", "page", "location", "source_line_or_cell", "geography",
              "level", "indicator", "classification", "count_type", "extracted_value", "flags",
              "expert_value", "meaning_correct_yes_no", "expert_note"]


def write_review_sheet(store, out_csv, n_per_group=40, seed=7):
    """Write a stratified verification sheet for an expert to complete.

    The expert fills expert_value and meaning_correct_yes_no by checking each
    row against the source PDF. score_review_sheet() then computes accuracy.
    """
    import random
    rng = random.Random(seed)
    rows = store.rows("SELECT c.*, d.filename, d.report_no FROM candidates c JOIN documents d USING(doc_id)")
    groups = {}
    for r in rows:
        groups.setdefault(r["table_group"], []).append(r)
    picked = []
    for g, items in sorted(groups.items()):
        flagged = [r for r in items if r["flags"] != "[]"]
        clean = [r for r in items if r["flags"] == "[]"]
        k_flag = min(len(flagged), n_per_group // 4)
        picked += rng.sample(flagged, k_flag) + rng.sample(clean, min(len(clean), n_per_group - k_flag))
    picked.sort(key=lambda r: (r["report_no"] or 0, r["page"], r["row_index"], r["col_index"]))
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(SHEET_COLS)
        for r in picked:
            loc = (f"line {r['row_index']} token {r['col_index']}" if r["table_index"] == -1
                   else f"table {r['table_index']} row {r['row_index']} col {r['col_index']}")
            w.writerow([r["id"], r["filename"], r["report_no"], r["page"], loc,
                        r.get("source_text") or r["raw_value"], r["geo"], r["geo_level"], r["indicator"],
                        r["classification"], r["count_type"], r["value"], r["flags"], "", "", ""])
    return len(picked)


def score_review_sheet(store, sheet_csv):
    """Score completed, source-checked rows against immutable extraction values.

    This measures sampled-row accuracy, not corpus recall. Blank expert fields
    are incomplete, not agreement; a partially completed row raises an error.
    """
    with open(sheet_csv, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    candidates = {c["id"]: c for c in store.rows("SELECT id, table_group, value FROM candidates")}
    by, seen, verified = {}, set(), 0
    for r in rows:
        ev = r.get("expert_value", "").strip().upper()
        meaning = r.get("meaning_correct_yes_no", "").strip().lower()
        if not ev and not meaning:
            continue
        if not ev or meaning not in ("yes", "no"):
            raise ValueError("Each reviewed row needs expert_value (integer or NA/BLANK) and meaning_correct_yes_no (yes/no)")
        cid = int(r["candidate_id"])
        if cid not in candidates or cid in seen:
            raise ValueError("Unknown or duplicate candidate ID in verification sheet")
        seen.add(cid)
        expert = None if ev in ("NA", "BLANK") else int(ev)
        if expert is not None and expert < 0:
            raise ValueError("Expert count must be non-negative or NA/BLANK")
        c = candidates[cid]
        b = by.setdefault(c["table_group"], {"rows": 0, "value_correct": 0, "meaning_correct": 0})
        b["rows"] += 1; verified += 1
        b["value_correct"] += int(expert == c["value"])
        b["meaning_correct"] += int(meaning == "yes")
    for b in by.values():
        b["value_accuracy"] = round(b["value_correct"] / b["rows"], 3)
        b["meaning_accuracy"] = round(b["meaning_correct"] / b["rows"], 3)
    return {"verified_rows": verified, "by_table": by,
            "note": "Sampled-row verification only; not a recall estimate. Uncompleted rows are excluded."}

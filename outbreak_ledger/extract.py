"""Document ingestion and source-linked extraction.

Two table adapters are provided, selected per module in YAML:

- header: a table with a header row (searched in the first rows) whose
  column labels map to explicit meanings.
- positional: a table with a complex multi-row header and merged cells that
  may continue across pages without repeating its header. Values are read in
  a declared order, and the meaning source (for example the table caption) is
  recorded in the module, not guessed.

Every candidate records page, table, row, column and raw cell text. A
local-model extractor is planned as another method behind the same interface,
to be retained only where it measurably outperforms these baselines.
"""
import hashlib
import json
import os
import re

import pdfplumber

from .harmonize import Module, normalize, parse_date, epi_week_bounds
from .store import Store, now

METHOD = "table-rules-v0.2"
MONTH_WORDS = ("janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|"
               "january|february|march|april|may|june|july|august|september|october|november|december")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canon_geo(text):
    return normalize(text).replace(" ", "-")


def parse_value(raw):
    """Return (integer or None, flags). Never guesses a value.

    Accepts digits with thousands spaces ('3 388', '10 141'). A trailing
    footnote marker is removed but flagged. 'NA' is an explicit 'not
    available', distinct from a blank cell.
    """
    flags = []
    if raw is None:
        return None, ["blank_cell"]
    s = str(raw).strip()
    if s == "":
        return None, ["blank_cell"]
    if s.upper() in ("NA", "N/A", "ND"):
        return None, ["explicit_na"]
    if s.endswith("*"):
        flags.append("footnote_marker")
        s = s.rstrip("* ").strip()
    compact = re.sub(r"[\s\u202f\u00a0]", "", s)
    if re.fullmatch(r"\d+", compact):
        return int(compact), flags
    return None, flags + ["non_numeric_value"]


def read_metadata(text, module):
    norm = normalize(text)
    meta, flags = {}, []
    pats = module.meta_patterns
    m = pats["report_no"].search(norm)
    meta["report_no"] = int(m.group(1)) if m else None
    if "period" in pats:
        m = pats["period"].search(norm)
        meta["period_start"], meta["period_end"] = (parse_date(m.group(1)), parse_date(m.group(2))) if m else (None, None)
    elif "reporting_date" in pats:
        m = pats["reporting_date"].search(norm)
        d = parse_date(m.group(1)) if m else None
        meta["period_start"] = meta["period_end"] = d
    elif "epi_week" in pats:
        mw, my = pats["epi_week"].search(norm), pats["epi_year"].search(norm) if "epi_year" in pats else None
        meta["period_start"] = meta["period_end"] = None
        if mw and my:
            try:
                meta["period_start"], meta["period_end"] = epi_week_bounds(int(my.group(1)), int(mw.group(1)), module.week_convention)
                meta["epi_week"], meta["epi_year"] = int(mw.group(1)), int(my.group(1))
            except ValueError:
                pass
    m = pats["publication_date"].search(norm) if "publication_date" in pats else None
    meta["publication_date"] = parse_date(m.group(1)) if m else None
    meta["issuer"] = None
    if "issuer" in pats:
        m = pats["issuer"].search(norm)
        meta["issuer"] = m.group(1).upper() if m else None
    for name, pat in module.issuer_patterns.items():
        if pat.search(norm):
            meta["issuer"] = name
            break
    for k, v in meta.items():
        if v is None:
            flags.append(f"missing_metadata:{k}")
    return meta, flags


def pdfium_pages(path):
    from .qualitative import page_texts
    return page_texts(path)


def detect_module(path, modules):
    """Return the first module whose fingerprint matches the document's opening pages, else None."""
    try:
        pages = pdfium_pages(path)[:2]
    except Exception:
        return None
    norm = normalize(" ".join(t for _, t in pages))
    for m in modules:
        if m.matches(norm):
            return m
    return None


def sentences(text):
    flat = re.sub(r"\s*\n\s*", " ", text)
    for s in re.split(r"(?<=[.;!?])\s+(?=[A-Z\u00c0-\u00dd*])", flat):
        s = s.strip()
        if 20 < len(s) < 600:
            yield s


def classify_state(norm_sentence, module):
    padded = f" {norm_sentence} "
    for state in ("denied", "resolved", "persisting"):
        if any(f" {cue} " in padded for cue in module.state_cues.get(state, [])):
            return {"persisting": "asserted_ongoing"}.get(state, state)
    return "asserted_unclear"


def date_note(norm_sentence):
    dates = re.findall(rf"\b(\d{{1,2}} (?:{MONTH_WORDS}) \d{{4}})\b", norm_sentence)
    if dates:
        return "date mentioned in text: " + ", ".join(dates) + " (check whether event or report date)"
    return "event date not stated; publication date is not the event date"


class Extractor:
    def __init__(self, module, store, doc_id, meta, meta_flags):
        self.m, self.s, self.doc_id, self.meta, self.meta_flags = module, store, doc_id, meta, meta_flags
        self.n = 0

    def add(self, *, geo, geo_level, parent, is_total, group, meaning, raw_label, raw, page, t, r, c, extra_flags=(),
            source_text=None, disease=None, period=None, method=None):
        value, vflags = parse_value(raw)
        flags = list(extra_flags) + vflags + self.meta_flags
        if meaning is None:
            flags.append("unmapped_label")
        elif not meaning.is_complete:
            flags.append("ambiguous_definition")
        self.s.add_candidate({
            "doc_id": self.doc_id, "disease": disease or self.m.disease, "issuer": self.meta["issuer"],
            "period_end": (period or {}).get("end", self.meta["period_end"]),
            "period_start": (period or {}).get("start", self.meta.get("period_start")),
            "period_label": (period or {}).get("label"), "geo": geo, "geo_level": geo_level, "parent_geo": parent,
            "is_total": int(is_total), "table_group": group,
            "indicator": meaning.indicator if meaning else "unknown",
            "classification": meaning.classification if meaning else "unknown",
            "count_type": meaning.count_type if meaning else "unknown",
            "value": value, "raw_label": raw_label, "raw_value": raw, "source_text": source_text,
            "page": page, "table_index": t, "row_index": r, "col_index": c,
            "method": method or METHOD, "flags": json.dumps(sorted(set(flags)))})
        self.n += 1

    def geo_of(self, raw_name, default_level):
        """Return (geo, level, is_total, flags) for a row label."""
        name = (raw_name or "").strip()
        if not name:
            return "UNNAMED", default_level, False, ["missing_geo_name"]
        if self.m.is_total(name):
            return self.m.total_geo, self.m.total_level, True, []
        if self.m.is_unallocated(name):
            return "UNALLOCATED", "unallocated", False, []
        if self.m.is_province(name):
            return canon_geo(name), "province", False, []
        return canon_geo(name).replace("zs-", ""), default_level, False, []

    # Adapter 1: header table
    def header_table(self, adapter, page_no, t_idx, table):
        for h_idx, row in enumerate(table[:3]):
            cells = [c or "" for c in row]
            geo_col = next((i for i, h in enumerate(cells) if normalize(h) in adapter["geo_column_labels"]), None)
            if geo_col is not None:
                break
        else:
            return False
        header = [c or "" for c in table[h_idx]]
        for r_idx in range(h_idx + 1, len(table)):
            row = table[r_idx]
            geo, level, is_total, gflags = self.geo_of(row[geo_col], adapter["geo_level"])
            if geo == "UNNAMED" and not any((x or "").strip() for x in row):
                continue
            for c_idx, label in enumerate(header):
                if c_idx == geo_col or self.m.is_ignored(label):
                    continue
                self.add(geo=geo, geo_level=level, parent=None, is_total=is_total, group=adapter["name"],
                         meaning=self.m.meaning(label), raw_label=label.replace("\n", " "),
                         raw=row[c_idx] if c_idx < len(row) else None,
                         page=page_no, t=t_idx, r=r_idx, c=c_idx, extra_flags=gflags)
        return True

    # Adapter 2: positional reading of a table from the page's text layer
    def positional_text(self, adapter, pages):
        """Read a multi-page table line by line from layout text.

        Used where cell detection fails (merged cells, multi-row headers, names
        wrapped over several lines, tables continuing without headers). Each
        value records page, line number, token position and the full source line.
        """
        from .harmonize import Meaning
        fields = [Meaning(**f) for f in adapter["fields"]]
        active, started, province = False, False, None
        for page_no, layout in pages:
            lines = layout.split("\n")
            i = 0
            pending = []
            while i < len(lines):
                li, line = i, lines[i]
                toks = line.split()
                i += 1
                if not toks:
                    continue
                if not active:
                    if adapter["start_marker"] in normalize(line):
                        active = True
                    continue
                k = next((j for j, t in enumerate(toks) if VALUE_TOKEN.fullmatch(t)), None)
                if k is None:
                    pending.append(line.strip())
                    continue
                name_toks, vals = toks[:k], toks[k:]
                extra = []
                if not name_toks:
                    nxt = lines[i].split() if i < len(lines) else []
                    nxt_is_name = bool(nxt) and not any(VALUE_TOKEN.fullmatch(t) for t in nxt)
                    if not pending and not nxt_is_name:
                        continue  # stray value spilled from another column
                    parts = pending[-1:] + ([lines[i].strip()] if nxt_is_name else [])
                    if nxt_is_name:
                        i += 1
                    name = parts[0]
                    for p_ in parts[1:]:
                        name = name + p_ if name.endswith("-") else name + " " + p_
                    extra.append("name_joined_from_lines")
                else:
                    name = " ".join(name_toks)
                pending = []
                if not started:
                    started = True
                geo, level, is_total, gflags = self.geo_of(name, adapter["geo_level"])
                if level == "province":
                    if province == geo:
                        level = adapter["geo_level"]  # health zone sharing its province's name
                        extra.append("hz_shares_province_name")
                    else:
                        province = geo
                parent = province if level in (adapter["geo_level"], "unallocated") else None
                pct = next((j for j, t in enumerate(vals) if PCT_TOKEN.fullmatch(t) or (j >= 2 and t == "NA")), None)
                if pct is None or pct != len(fields):
                    extra.append("unexpected_row_shape" if pct is None else "ambiguous_number_grouping")
                abstain = "ambiguous_number_grouping" in extra
                for f_idx, meaning in enumerate(fields):
                    raw = None if abstain or f_idx >= len(vals) else vals[f_idx]
                    self.add(geo=geo, geo_level=level, parent=parent, is_total=is_total, group=adapter["name"],
                             meaning=meaning, raw_label=adapter["field_labels"][f_idx], raw=raw,
                             page=page_no, t=-1, r=li, c=k + f_idx, extra_flags=gflags + extra,
                             source_text=line.strip())
                if is_total:
                    return


    # Adapter 3: one row per disease, several periods (multi-event weekly tables)
    def disease_rows_text(self, adapter, pages):
        """Read a multi-disease table line by line from the layout text.

        Each row label maps to a disease and classification in the module.
        Values come in groups (cases, deaths, ratio) per period column; the
        last group may be a year-to-date cumulative. Row labels split over two
        lines are joined; commentary text to the right is ignored.
        """
        rows_cfg = {normalize(k): v for k, v in adapter["rows"].items()}
        per = int(adapter.get("values_per_period", 3))
        year = self.meta.get("epi_year")
        conv = self.m.week_convention
        n_before = self.n
        for page_no, layout in pages:
            lines = layout.split("\n")
            if not any(adapter["start_marker"] in normalize(l) for l in lines[:6]):
                continue
            periods, pending = None, None
            for li, line in enumerate(lines):
                n = normalize(line)
                if not n:
                    continue
                if periods is None:
                    toks = n.split()
                    weekly = [int(m.group(1)) for t in toks for m in [re.fullmatch(r"sem(\d+)", t)] if m]
                    cumul = [int(m.group(1)) for t in toks for m in [re.fullmatch(r"sem1-s(\d+)", t)] if m]
                    if len(weekly) >= 2:
                        periods = [("new", w) for w in weekly] + [("cumulative", c) for c in cumul]
                    continue
                if adapter.get("end_marker") and n.startswith(adapter["end_marker"]):
                    break
                toks = line.split()
                k = next((j for j, t in enumerate(toks) if VALUE_TOKEN.fullmatch(t)), None)
                starts_left = len(line) - len(line.lstrip()) < 3
                if k is None:
                    if starts_left:
                        head = re.split(r"\s{3,}", line.strip())[0]   # row label; commentary may follow on the right
                        pending = head if len(head) < 30 else None
                    continue
                if k == 0 or not starts_left:
                    pending = None
                    continue
                name = " ".join(toks[:k])
                vals = []
                for t in toks[k:]:
                    if VALUE_TOKEN.fullmatch(t):
                        vals.append(t)
                    else:
                        break
                key = normalize(name)
                flags = []
                if key not in rows_cfg and pending and normalize(pending + " " + name) in rows_cfg:
                    key, name = normalize(pending + " " + name), pending + " " + name
                    flags.append("name_joined_from_lines")
                pending = None
                spec = rows_cfg.get(key)
                if spec is None:
                    flags.append("unmapped_row_label")
                if len(vals) != per * len(periods):
                    flags.append("unexpected_row_shape")
                from .harmonize import Meaning
                for p_idx, (ctype, week) in enumerate(periods):
                    try:
                        start, end = epi_week_bounds(year, week, conv)
                        if ctype == "cumulative":
                            start = epi_week_bounds(year, 1, conv)[0]
                    except (TypeError, ValueError):
                        start = end = None
                    label = f"{year}-W{week:02d}" if ctype == "new" else f"{year}-W01..W{week:02d}"
                    for f_idx, ind in enumerate(("cases", "deaths")):
                        pos = p_idx * per + f_idx
                        raw = vals[pos] if pos < len(vals) and "unexpected_row_shape" not in flags else None
                        meaning = Meaning(ind, spec["classification"], ctype) if spec else None
                        self.add(geo=adapter.get("geo", "national"), geo_level="national", parent=None, is_total=False,
                                 group=adapter["name"], meaning=meaning,
                                 raw_label=f"{name} | {'SEM' + str(week) if ctype == 'new' else 'SEM1-S' + str(week)} | {ind}",
                                 raw=raw, page=page_no, t=-1, r=li, c=k + pos, extra_flags=flags,
                                 source_text=line.strip(), disease=spec["disease"] if spec else normalize(name),
                                 period={"start": start, "end": end, "label": label})
            break
        return self.n - n_before


def narrative_claims(ex, module, pdfium_pages_list):
    """Counts stated in prose, kept as separate claims with their own definitions.

    Disease and place are attributed only from evidence: the slide or section
    heading (disease_from: section) or the matched sentence and the text just
    before it (disease_from: sentence). When the evidence names no disease, or
    more than one, the claim abstains (disease 'unknown') and cannot be approved.
    The evidence used is written into the claim's label for review.
    """
    from .harmonize import Meaning
    from .qualitative import vocab, terms
    v = vocab()
    for page_no, text in pdfium_pages_list:
        norm = normalize(text)
        head = normalize(" ".join([l for l in text.split("\n") if l.strip()][:2]))
        page_dis = terms(head, v["diseases"])
        for nc in module.narrative_claims:
            for m in nc["regex"].finditer(norm):
                window = norm[max(0, m.start() - 300):m.end()]
                if nc.get("disease_from") == "sentence":
                    dis = terms(m.group(0), v["diseases"]) or terms(window, v["diseases"])
                else:
                    in_sentence = terms(m.group(0), v["diseases"])
                    dis = page_dis if not in_sentence or in_sentence == page_dis else page_dis + in_sentence
                disease = dis[0] if len(dis) == 1 else None
                if nc.get("country_from") == "sentence":
                    ctry = terms(m.group(0), v["countries"]) or terms(window, v["countries"])
                    geo = ctry[0] if len(ctry) == 1 else None
                else:
                    geo = nc.get("geo") or module.cfg.get("default_country")
                evidence = (f"disease evidence: {', '.join(dis) or 'none'}"
                            + (f"; place evidence: {geo or 'ambiguous or none'}" if nc.get("country_from") else ""))
                flags = ["from_narrative"]
                if disease is None:
                    flags.append("disease_not_identified")
                if geo is None:
                    flags.append("place_not_identified")
                for field, spec in nc["fields"].items():
                    raw = m.group(field)
                    period = None
                    if spec.get("as_of"):
                        d = parse_date(m.group(spec["as_of"]))
                        period = {"start": None, "end": d, "label": f"as of {d}"}
                    elif spec.get("week"):
                        year = int(m.group("year")) if "year" in m.groupdict() and m.group("year") else ex.meta.get("epi_year")
                        week = ex.meta.get("epi_week") if spec["week"] == "current" else int(m.group(spec["week"]))
                        try:
                            st, en = epi_week_bounds(year, week, module.week_convention)
                            period = {"start": st, "end": en, "label": f"{year}-W{week:02d}"}
                        except (TypeError, ValueError):
                            period = None
                    meaning = Meaning(spec["indicator"], spec["classification"], spec["count_type"]) if disease and geo else None
                    ex.add(geo=geo or "undetermined", geo_level="national", parent=None, is_total=False,
                           group="narrative", meaning=meaning,
                           raw_label=f"narrative: {field} | {evidence}", raw=raw, page=page_no, t=-2, r=m.start(field), c=0,
                           extra_flags=flags, source_text=m.group(0)[:300], disease=disease or "unknown", period=period,
                           method="narrative-pattern-v0.5")


def diagnose(path, modules):
    """Explain routing: text available, and which fingerprint patterns matched for each family."""
    try:
        pages = pdfium_pages(path)
    except Exception as exc:
        return {"text_chars": 0, "pages": 0, "families": [], "reason": f"PDF text could not be read: {exc}"}
    norm = normalize(" ".join(t for _, t in pages[:2]))
    fams = []
    for m in modules:
        hits = [(p.pattern, bool(p.search(norm))) for p in m.fingerprint]
        fams.append({"family": m.name, "label": m.family_label, "matched": sum(h for _, h in hits), "required": len(hits),
                     "missing": [p for p, h in hits if not h]})
    chars = len(norm)
    if chars < 200:
        reason = "Little or no text layer on the first pages: probably scanned or image-based. OCR is not included."
    else:
        best = max(fams, key=lambda f: f["matched"] / max(1, f["required"])) if fams else None
        reason = ("No configured report family matched. Closest: " + best["label"] + f" ({best['matched']}/{best['required']} heading patterns)"
                  if best else "No report families configured.")
    return {"text_chars": chars, "pages": len(pages), "families": fams, "reason": reason, "opening_text": norm[:300]}


VALUE_TOKEN = re.compile(r"\d+\*?|NA|\d+,\d+%")
PCT_TOKEN = re.compile(r"\d+,\d+%")


def ingest(path, module: Module, store: Store, metadata_override=None):
    """Ingest one PDF. Re-ingesting identical bytes is refused as a duplicate."""
    doc_id = sha256(path)
    if store.has_document(doc_id):
        return {"doc_id": doc_id, "file": os.path.basename(path), "skipped": "duplicate (same sha256)"}
    with pdfplumber.open(path) as pdf:
        pages = [(i + 1, p.extract_text() or "", p.extract_tables()) for i, p in enumerate(pdf.pages)]
        layouts = [(i + 1, p.extract_text(layout=True) or "") for i, p in enumerate(pdf.pages)]
    try:
        text_pages = pdfium_pages(path)
    except Exception:
        text_pages = [(n, t) for n, t, _ in pages]
    full_text = "\n".join(t for _, t in text_pages) + "\n" + "\n".join(t for _, t, _ in pages)
    meta, meta_flags = read_metadata(full_text, module)
    if metadata_override:
        for k in ("report_no", "period_start", "period_end", "publication_date", "issuer"):
            if metadata_override.get(k) not in (None, ""):
                meta[k] = int(metadata_override[k]) if k == "report_no" else metadata_override[k]
        meta_flags = [f for f in meta_flags if not any(f == f"missing_metadata:{k}" and meta.get(k) for k in meta)]
        meta_flags.append("metadata_supplied_by_reviewer")
    meta_db = {k: meta[k] for k in ("report_no", "period_start", "period_end", "publication_date", "issuer")}
    episode = None
    if module.cfg.get("episode_pattern"):
        mm = re.search(module.cfg["episode_pattern"], normalize(full_text))
        episode = mm.group(1) if mm else None
    store.add_document({"doc_id": doc_id, "path": os.path.abspath(path), "filename": os.path.basename(path),
                        **meta_db, "retrieved_at": now(), "module": module.name, "metadata_flags": ",".join(meta_flags),
                        "episode": episode, "family_origin": "reviewer" if metadata_override else "fingerprint"})
    ex = Extractor(module, store, doc_id, meta, meta_flags)

    done = set()
    for adapter in module.adapters:
        state = {}
        for page_no, _, tables in pages:
            for t_idx, table in enumerate(tables):
                if not table or (page_no, t_idx) in done:
                    continue
                if adapter["type"] == "header":
                    if adapter["name"] in state:
                        continue
                    if ex.header_table(adapter, page_no, t_idx, table):
                        done.add((page_no, t_idx))
                        state[adapter["name"]] = True
        if adapter["type"] == "positional_text":
            ex.positional_text(adapter, layouts)
        if adapter["type"] == "disease_rows_text":
            ex.disease_rows_text(adapter, layouts)
    if module.narrative_claims:
        narrative_claims(ex, module, text_pages)

    # Narrative: revision and adjustment statements, and context signals
    for page_no, text, _ in pages:
        norm_page = normalize(text)
        for pat in module.revision_patterns:
            for m in pat.finditer(norm_page):
                store.add_revision_note({"doc_id": doc_id, "page": page_no, "geo": canon_geo(m.group("geo")),
                                         "kind": "stated_revision", "old_value": int(m.group("old")),
                                         "new_value": int(m.group("new")), "quote": m.group(0)})
        for pat in module.adjustment_patterns:
            for m in pat.finditer(norm_page):
                geos = module.provinces_in(norm_page[m.start():m.end() + 60]) or \
                    module.provinces_in(norm_page[max(0, m.start() - 200):m.start()])
                for geo in geos or ["unspecified"]:
                    store.add_revision_note({"doc_id": doc_id, "page": page_no, "geo": geo,
                                             "kind": "stated_adjustment_magnitude_unstated", "old_value": None,
                                             "new_value": None, "quote": m.group(0)})
    # Qualitative statements (all modules) after numbers, so health zones seen
    # in this report's tables can be recognized in its text.
    from .qualitative import extract_statements
    table_rows = {normalize(" ".join(c or "" for c in row)) for _, _, tables in pages for t in tables for row in t if row}
    n_st = extract_statements(path, module, store, doc_id, meta_db, text_pages, skip_lines=table_rows)
    store.commit()
    return {"doc_id": doc_id, "file": os.path.basename(path), "candidates": ex.n, "statements": n_st,
            "module": module.name, **meta_db}


def _source_path(store, d):
    """Locate a source PDF: the stored path, else <db folder>/sources/<sha256>.pdf (a moved workspace)."""
    if d["path"] and os.path.isfile(d["path"]):
        return d["path"]
    db = store.rows("PRAGMA database_list")[0]["file"]
    alt = os.path.join(os.path.dirname(db), "sources", f"{d['doc_id']}.pdf")
    return alt if os.path.isfile(alt) else d["path"]


def verify_provenance(store):
    """Re-open each source and confirm the cited cell still contains the raw value."""
    docs = {d["doc_id"]: d for d in store.rows("SELECT doc_id, path FROM documents")}
    for d in docs.values():
        d["path"] = _source_path(store, d)
        d["hash_valid"] = bool(d["path"]) and os.path.isfile(d["path"]) and sha256(d["path"]) == d["doc_id"]
    cache, results, narrative_cache = {}, [], {}
    for c in store.rows("SELECT id, doc_id, page, table_index, row_index, col_index, raw_value FROM candidates"):
        if not docs[c["doc_id"]]["hash_valid"]:
            results.append({"id": c["id"], "provenance_ok": False})
            continue
        key = (docs[c["doc_id"]]["path"], c["page"])
        if key not in cache:
            with pdfplumber.open(key[0]) as pdf:
                pg = pdf.pages[c["page"] - 1]
                cache[key] = (pg.extract_tables(), (pg.extract_text(layout=True) or "").split("\n"))
        tables, lines = cache[key]
        try:
            if c["table_index"] == -2:  # narrative claim: the matched span must still be in the page text
                ok = c["raw_value"] is None or normalize(c["source_text"] or "") in normalize(
                    " ".join(t for n, t in narrative_cache.setdefault(key[0], pdfium_pages(key[0])) if n == c["page"]))
            elif c["table_index"] == -1:  # text-line provenance
                toks = lines[c["row_index"]].split()
                ok = c["raw_value"] is None or toks[c["col_index"]] == c["raw_value"]
            else:
                cell = tables[c["table_index"]][c["row_index"]][c["col_index"]]
                ok = (cell or "") == (c["raw_value"] or "")
        except (IndexError, TypeError):
            ok = False
        results.append({"id": c["id"], "provenance_ok": ok})
    return results

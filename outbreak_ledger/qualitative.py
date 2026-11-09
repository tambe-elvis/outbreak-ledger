"""Qualitative evidence extraction: rule-based, source-linked, reviewable.

Narrative text in outbreak reports carries what numbers cannot: operational
constraints, actions taken, assessments of trend and risk, new signals and
data-quality caveats. This module turns that text into statements with:

- the exact source wording, page and section;
- heuristic tags: statement type (challenge, action, assessment,
  recommendation, highlight, signal...), topics, state (ongoing, resolved,
  denied, worsening, improving, new), disease, geography, quantities and
  dates mentioned;
- an issue thread linking the same problem across successive reports, so a
  reviewer can see when it was first reported, whether it persists and
  whether a later report states it was resolved.

Tags are suggestions for human review. They are never used to compute
indicators or scores. A local language model may later be evaluated as an
alternative tagger behind the same interface; this version uses rules only.
"""
import json
import os
import re
from functools import lru_cache

import yaml

from .harmonize import normalize

METHOD = "rule-tagger-v0.4"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONTHS = ("janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre|"
          "january|february|march|april|may|june|july|august|september|october|november|december")
QTY_RE = re.compile(r"(?<![\w/,.])(\d{1,3}(?:[ \u202f\u00a0]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)"
                    r"(?:\s?/\s?(\d{1,3}(?:[ \u202f\u00a0]\d{3})+(?!\d)|\d+))?(\s?%)?")
DATE_RE = re.compile(rf"\b(\d{{1,2}}(?:er)? (?:{MONTHS})(?: \d{{4}})?|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)\b")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\u00c0-\u00dd\u00ab\"(])")
CONTRAST = re.compile(r",?\s+(?:but|however|mais|cependant|toutefois|alors que|while)\s+", re.I)
CONSTRAINT_TOPICS = {"insecurity", "community_resistance", "supply_shortage", "lab_capacity", "isolation_capacity",
                     "transport_logistics", "human_resources", "data_reporting", "silent_zones"}


@lru_cache(maxsize=1)
def vocab():
    with open(os.path.join(ROOT, "config", "qualitative_common.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    out = {}
    for key in ("topics", "states", "statement_cues", "pillars", "diseases", "countries", "provinces", "regions"):
        out[key] = {k: [normalize(t) for t in v] for k, v in cfg.get(key, {}).items()}
    out["stopwords"] = set(cfg.get("stopwords", []))
    return out


def terms(norm, mapping):
    """Keys of `mapping` whose terms occur as whole words in normalized text (in mapping order)."""
    padded = f" {norm} "
    return [k for k, words in mapping.items() if any(f" {w} " in padded for w in words)]


def page_texts(path):
    """Text per page from the PDF text layer (pdfium keeps word spacing and reading order well)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                t = tp.get_text_bounded()
                tp.close()
            finally:
                page.close()
            t = t.replace("\u0002\r\n", "").replace("\u0002", "").replace("\r\n", "\n").replace("\r", "\n")
            out.append((i + 1, t))
        return out
    finally:
        pdf.close()


def numeric_ratio(text):
    toks = text.split()
    if not toks:
        return 1
    return sum(1 for t in toks if re.fullmatch(r"[\d.,%()/+\-]+", t)) / len(toks)


def quantities(text):
    out = []
    for m in QTY_RE.finditer(text):
        val = m.group(1) + ("/" + m.group(2) if m.group(2) else "")
        after = text[m.end():].lstrip(" )(,;:").split()
        before = text[:m.start()].split()
        if after and normalize(after[0]) in MONTHS.split("|"):
            continue
        if before and normalize(before[-1]) in MONTHS.split("|"):
            continue
        if re.fullmatch(r"(19|20)\d\d", val) and not m.group(3):
            continue
        phrase = " ".join(after[:4]).strip(" ,;:.()")
        out.append((val + (m.group(3) or "")).strip() + (" " + phrase if phrase else ""))
        if len(out) >= 8:
            break
    return out


def classify(norm, section_type, v):
    if section_type in ("challenge", "action", "recommendation", "highlight", "commentary"):
        return section_type
    for t in ("recommendation", "assessment", "challenge", "action"):
        if terms(norm, {t: v["statement_cues"][t]}):
            return t
    return "observation"


def state_of(norm, v):
    found = terms(norm, v["states"])
    return {"persisting": "ongoing"}.get(found[0], found[0]) if found else "asserted"


class Segmenter:
    """Splits page text into sections and statement-sized units, per module rules."""

    def __init__(self, module):
        q = module.qualitative or {}
        self.q = q
        self.headings = [re.compile(p) for p in q.get("heading_patterns", [])]
        self.section_types = {normalize(k): v for k, v in (q.get("section_types") or {}).items()}
        self.skip = [re.compile(p) for p in q.get("skip_patterns", [])]
        self.bullets = tuple(q.get("bullets", ["\u2756", "\u2022", "\u25aa", "\u27a2", "\uf0b7"]))
        self.footnote = re.compile(q["footnote_pattern"]) if q.get("footnote_pattern") else None
        self.title_lines = int(q.get("page_title_lines", 0))
        self.clause_split = q.get("clause_split")
        self.contents = bool(q.get("contents_titles"))
        self.summary_start = [re.compile(p) for p in q.get("summary_start_patterns", [])]

    table_rows = frozenset()

    def skipped(self, line):
        n = normalize(line)
        return not n or n in self.table_rows or any(p.search(n) for p in self.skip)

    def units(self, pages):
        """Yield dicts: page, section, section_type, pillar, text."""
        v = vocab()
        section, section_type, pillar = None, None, None
        pillar_by_num = {}
        titles = self.contents_titles(pages) if self.contents else []
        in_contents = False
        for page_no, text in pages:
            lines = [l.strip() for l in text.split("\n")]
            buf = []

            def flush():
                if buf:
                    yield {"page": page_no, "section": section, "section_type": section_type,
                           "pillar": pillar, "text": " ".join(buf)}
                buf.clear()

            title_set = set()
            if self.title_lines:
                head = [l for l in lines if l and not self.skipped(l)][:self.title_lines]
                title_set = set(head)
                if head:
                    section = " ".join(head)
                    section_type = None
                    pillar = (terms(normalize(section), v["pillars"]) or [None])[0]
            for line in lines:
                if not line or line in title_set:
                    continue
                n = normalize(line)
                if self.contents and page_no == 1:
                    if n == "contents":
                        in_contents = True
                        continue
                    if in_contents:
                        if any(n.startswith(t[:40]) and not re.match(r"^\d{3}", line) for t in titles):
                            in_contents = False
                        else:
                            continue
                if self.skipped(line) or (self.footnote and self.footnote.match(line) and len(line) > 60):
                    continue
                # numbered headings, e.g. "2.10.1. Principales actions"
                hit = None
                for p in self.headings:
                    m = p.match(line)
                    if m and len(line) < 100 and not re.search(r"\d{2,}\s*%", line):
                        hit = m
                        break
                if hit:
                    yield from flush()
                    num, title = hit.group(1), hit.group(2).strip()
                    nt = normalize(title)
                    section_type = next((t for k, t in self.section_types.items() if nt.startswith(k)), None)
                    parent = num.rsplit(".", 1)[0]
                    found = (terms(nt, v["pillars"]) or [None])[0]
                    pillar = found or pillar_by_num.get(parent)
                    pillar_by_num[num] = pillar
                    section = title if not section_type else f"{pillar_title(pillar)}{title}"
                    continue
                # keyword headings, possibly followed by text on the same line ("Defis En Ituri, ...")
                key = next((k for k in self.section_types if n == k or n.startswith(k + " ")), None)
                if key and (len(n) < 70 or n.startswith(key + " ")):
                    yield from flush()
                    section_type = self.section_types[key]
                    words = line.split()
                    rest = " ".join(words[len(key.split()):]) if len(n) > len(key) + 2 else ""
                    if rest and len(rest) > 25:
                        buf.append(rest)
                    continue
                if titles and len(line) < 110 and any(n.startswith(t[:40]) for t in titles) and not re.match(r"^\d{3}", line):
                    yield from flush()
                    section = line
                    section_type = None
                    pillar = None
                    continue
                if any(p.match(n) for p in self.summary_start) and section_type != "highlight":
                    yield from flush()
                    section, section_type, pillar = "Synthesis", "highlight", None
                if numeric_ratio(line) > 0.6 and len(line.split()) >= 2:
                    continue  # a table row in the text layer, not narrative
                if line.startswith(self.bullets):
                    yield from flush()
                    line = line.lstrip("".join(self.bullets) + " ")
                buf.append(line)
            yield from flush()

    def contents_titles(self, pages):
        if not pages:
            return []
        lines = [l.strip() for l in pages[0][1].split("\n")]
        titles, cur = [], None
        for l in lines:
            m = re.match(r"^(\d{3})\.\s+(.*)$", l)
            if m:
                if cur:
                    titles.append(cur)
                cur = m.group(2)
            elif cur is not None:
                if not l or re.match(r"^[A-Z][a-z]+ [a-z]", l) and len(l) > 60:
                    titles.append(cur)
                    cur = None
                elif len(l) < 45:
                    cur += " " + l
                else:
                    titles.append(cur)
                    cur = None
        if cur:
            titles.append(cur)
        return [normalize(t) for t in titles if len(t) > 8]

    def statements(self, unit):
        text = re.sub(r"\s+", " ", unit["text"]).strip()
        parts = []
        for s in SENT_SPLIT.split(text):
            if self.clause_split and self.clause_split in s:
                pieces = [p.strip() for p in s.split(self.clause_split)]
                merged = []
                for p in pieces:
                    if merged and len(p) < 25:
                        merged[-1] += f"{self.clause_split} {p}"
                    else:
                        merged.append(p)
                parts.extend(merged)
            else:
                parts.append(s)
        split = []
        for p in parts:
            # "Testing resumed, but the backlog remains" holds two claims with different states
            pieces = CONTRAST.split(p)
            if len(pieces) > 1 and all(len(x.split()) >= 3 for x in pieces):
                split.extend(pieces)
            else:
                split.append(p)
        for p in split:
            p = p.strip(" ;,")
            if 25 <= len(p) <= 700 and numeric_ratio(p) <= 0.35 and len(re.findall(r"[A-Za-z\u00c0-\u00ff]{3,}", p)) >= 4:
                yield p


def pillar_title(pillar):
    return (pillar.replace("_", " ").capitalize() + ": ") if pillar else ""


def gazetteer(store):
    """Health-zone names already seen in quantitative tables of this workspace."""
    names = {r["geo"] for r in store.rows("SELECT DISTINCT geo FROM candidates WHERE geo_level='health_zone'")}
    return {g: g.replace("-", " ") for g in names if g and g not in ("UNNAMED",)}


def tag(sentence, unit, module, v, hz_names, page_disease=None):
    norm = normalize(sentence)
    topics = terms(norm, v["topics"])
    geos = terms(norm, v["provinces"]) + terms(norm, v["countries"]) + terms(norm, v["regions"])
    padded = f" {norm} "
    hzs = sorted({g for g, spaced in hz_names.items() if f" {spaced} " in padded or f" {g} " in padded})
    capitalized = {normalize(w) for w in re.findall(r"\b[A-Z\u00c0-\u00dd][\w\-]+", sentence)}
    hzs += [m.group(1) for m in re.finditer(r"\b(?:zs|zone de sante|aire de sante|health zone)\s+(?:de\s+|d\s+|of\s+)?([a-z][a-z\-]{3,})", norm)
            if m.group(1) in capitalized and m.group(1) not in hzs]
    diseases = terms(norm, v["diseases"])
    if module.disease not in ("multi", None) and not diseases:
        disease = module.disease
    elif len(diseases) == 1:
        disease = diseases[0]
    elif page_disease:
        disease = page_disease
    elif module.disease not in ("multi", None):
        disease = module.disease
    else:
        disease = ",".join(diseases) or None
    st = classify(norm, unit["section_type"], v)
    if st == "observation" and set(topics) & CONSTRAINT_TOPICS and terms(norm, {"c": v["statement_cues"]["challenge"]}):
        st = "challenge"
    return {"statement_type": st, "topics": topics, "state": state_of(norm, v), "disease": disease,
            "geos": geos, "health_zones": sorted(set(hzs)), "quantities": quantities(sentence),
            "dates": [m.group(1) for m in DATE_RE.finditer(norm)][:5]}


def extract_statements(path, module, store, doc_id, meta, pages=None, skip_lines=None):
    """Extract qualitative statements from one document. Returns the number stored."""
    v = vocab()
    pages = pages or page_texts(path)
    seg = Segmenter(module)
    seg.table_rows = {x for x in (skip_lines or ()) if x}
    hz = gazetteer(store)
    n = 0
    for unit in seg.units(pages):
        page_disease = None
        if unit["section"]:
            d = terms(normalize(unit["section"]), v["diseases"])
            page_disease = d[0] if len(d) == 1 else None
        for s in seg.statements(unit):
            t = tag(s, unit, module, v, hz, page_disease)
            store.add_statement({"doc_id": doc_id, "module": module.name, "issuer": meta.get("issuer"),
                                 "period_end": meta.get("period_end"), "page": unit["page"],
                                 "section": (unit["section"] or "")[:160], "pillar": unit["pillar"],
                                 "quote": s, "method": METHOD, **t})
            n += 1
    if module.signals_table:
        n += extract_signals(path, module, store, doc_id, meta)
    if module.products_section:
        n += extract_products(pages, module, store, doc_id, meta, v)
    return n


def extract_signals(path, module, store, doc_id, meta):
    """WER 'Selected new signals' table: region bands defined by the table's horizontal rules."""
    import pdfplumber
    cfg = module.signals_table
    v = vocab()
    n = 0
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages[:3], start=1):
            words = page.extract_words(keep_blank_chars=False, x_tolerance=1.5)
            head = [w for w in words if w["text"] in cfg["header_words"]]
            if len(head) < 2:
                continue
            y0 = max(w["bottom"] for w in head)
            rules = sorted({round(l["top"], 1) for l in page.lines if l["top"] > y0 - 2 and abs(l["top"] - l["bottom"]) < 1})
            bands = [(a, b) for a, b in zip(rules, rules[1:]) if 4 < b - a < cfg.get("max_band_height", 140)]
            for a, b in bands:
                inside = [w for w in words if a - 1 <= w["top"] < b - 1]
                region = " ".join(w["text"] for w in inside if w["x0"] < cfg["region_col_max_x"])
                if len(region) > 40 or re.search(r"\d", region):
                    break  # past the table: footnotes
                rows = {}
                for w in inside:
                    if w["x0"] >= cfg["region_col_max_x"]:
                        rows.setdefault(round(w["top"]), []).append(w)
                hazards = [" ".join(x["text"] for x in sorted(ws, key=lambda x: x["x0"])) for _, ws in sorted(rows.items())]
                if not region:
                    continue
                reg = (terms(normalize(region), v["regions"]) or [normalize(region)])
                for hz_text in hazards:
                    none = normalize(cfg.get("none_phrase", "")) in normalize(hz_text)
                    dis = terms(normalize(hz_text), v["diseases"])
                    store.add_statement({
                        "doc_id": doc_id, "module": module.name, "issuer": meta.get("issuer"),
                        "period_end": meta.get("period_end"), "page": page_no,
                        "section": "Selected new signals of potential public health events",
                        "pillar": "surveillance", "statement_type": "signal", "topics": ["new_signal"],
                        "state": "none_publicly_identified" if none else "new",
                        "disease": None if none else (dis[0] if len(dis) == 1 else normalize(hz_text).replace(" ", "_")),
                        "geos": reg, "health_zones": [], "quantities": [], "dates": [],
                        "quote": f"{region}: {hz_text}", "caveat": cfg.get("caveat") if none else None,
                        "method": METHOD + "+table-bands"})
                    n += 1
            if bands:
                break
    return n


def extract_products(pages, module, store, doc_id, meta, v):
    cfg = module.products_section
    cats = [normalize(c) for c in cfg["categories"]]
    stops = [re.compile(p) for p in cfg.get("stop_patterns", [])]
    titles = Segmenter(module).contents_titles(pages)
    max_items = int(cfg.get("max_items", 8))
    n = 0
    for page_no, text in pages[:3]:
        lines = [l.strip() for l in text.split("\n")]
        try:
            start = next(i for i, l in enumerate(lines) if normalize(l) == normalize(cfg["start"]))
        except StopIteration:
            continue
        cat = None
        for l in lines[start + 1:]:
            nl = normalize(l)
            if not nl:
                continue
            if any(p.search(nl) for p in stops) or len(l) > 110 or n >= max_items \
                    or any(nl.startswith(t[:30]) for t in titles):
                break
            if nl in cats or any(nl == c for c in cats):
                cat = l
                continue
            if cat is None:
                continue
            dis = terms(nl, v["diseases"])
            store.add_statement({
                "doc_id": doc_id, "module": module.name, "issuer": meta.get("issuer"), "period_end": meta.get("period_end"),
                "page": page_no, "section": "Published information products", "pillar": "surveillance",
                "statement_type": "published_product", "topics": ["new_signal"], "state": "new",
                "disease": dis[0] if len(dis) == 1 else None,
                "geos": terms(nl, v["countries"]) + terms(nl, v["regions"]), "health_zones": [],
                "quantities": [], "dates": [], "quote": f"{cat}: {l}", "method": METHOD})
            n += 1
        break
    return n


# ---------- shared helper for issue linking (see issues.py) ----------

def signature(quote, v):
    toks = set(re.findall(r"[a-z][a-z\-]{3,}", normalize(quote)))
    return {t for t in toks if t not in v["stopwords"]}



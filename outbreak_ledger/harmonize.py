"""Label normalization and mapping to explicit outbreak meanings."""
import re
from datetime import date
import unicodedata
from dataclasses import dataclass

import yaml


def normalize(text: str) -> str:
    """Lowercase, strip accents, replace punctuation with spaces, collapse spaces."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("\u2019", " ").replace("'", " ")
    text = re.sub(r"[^a-z0-9\-\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Meaning:
    indicator: str
    classification: str
    count_type: str

    @property
    def is_complete(self) -> bool:
        return "unknown" not in (self.indicator, self.classification, self.count_type)


FR_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
             "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
             "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
             "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}


def parse_date(text):
    """Return ISO date from '2026-06-01' or '26 octobre 2026' (accents removed), else None."""
    if not text:
        return None
    text = normalize(text)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})(?:er)? ([a-z]+) (\d{4})", text)
    if m and m.group(2) in FR_MONTHS:
        try:
            return date(int(m.group(3)), FR_MONTHS[m.group(2)], int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


NUM_RE = r"(?<!\d)(?:\d{1,3}(?: \d{3})+(?!\d)|\d+)"


def epi_week_bounds(year, week, convention="iso"):
    """Return (start, end) ISO dates of an epidemiological week.

    iso: Monday to Sunday (WHO WER). mmwr: Sunday to Saturday, week 1 being
    the week that contains 4 January (used by the DRC weekly profile).
    """
    from datetime import timedelta
    if convention == "mmwr":
        jan4 = date(year, 1, 4)
        start1 = jan4 - timedelta(days=(jan4.weekday() + 1) % 7)
        start = start1 + timedelta(days=7 * (week - 1))
    else:
        start = date.fromisocalendar(year, week, 1)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


class Module:
    """A disease or event module loaded from YAML configuration."""

    def __init__(self, path: str):
        with open(path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        self.cfg = cfg
        self.name = cfg["module"]
        self.disease = cfg["disease"]
        geo = cfg.get("geography", {})
        self.provinces = {normalize(x) for x in geo.get("provinces", [])}
        self.total_labels = {normalize(x) for x in geo.get("total_labels", ["total"])}
        self.unallocated_labels = {normalize(x) for x in geo.get("unallocated_labels", [])}
        self.total_geo = geo.get("total_geo", "TOTAL")
        self.total_level = geo.get("total_level", "aggregate")
        self.labels = {normalize(k): Meaning(**v) for k, v in cfg["labels"].items()}
        self.ignore_labels = {normalize(x) for x in cfg.get("ignore_labels", [])}
        self.meta_patterns = {k: re.compile(v) for k, v in cfg["metadata_patterns"].items()}
        self.issuer_patterns = {k: re.compile(v) for k, v in cfg.get("issuer_patterns", {}).items()}
        self.period_mode = cfg.get("period", "range")
        self.coverage_level = cfg.get("coverage_level", geo.get("level", "health_zone"))
        self.adapters = cfg.get("table_adapters") or [{
            "name": "table", "type": "header",
            "geo_column_labels": geo.get("column_labels", []), "geo_level": geo.get("level", "health_zone")}]
        for a in self.adapters:
            a["geo_column_labels"] = {normalize(x) for x in a.get("geo_column_labels", [])}
        self.revision_patterns = [re.compile(p) for p in cfg.get("revision_patterns", [])]
        self.adjustment_patterns = [re.compile(p) for p in cfg.get("adjustment_patterns", [])]
        self.context_topics = {k: [normalize(w) for w in v] for k, v in cfg.get("context_topics", {}).items()}
        self.state_cues = {k: [normalize(w) for w in v] for k, v in cfg.get("context_state_cues", {}).items()}
        self.rules = cfg.get("rules", {})
        self.family_label = cfg.get("family_label", self.name)
        self.fingerprint = [re.compile(p) for p in cfg.get("fingerprint", [])]
        self.week_convention = cfg.get("week_convention", "iso")
        self.indicator_sets = cfg.get("indicators", [])
        self.qualitative = cfg.get("qualitative", {})
        self.signals_table = cfg.get("signals_table")
        self.products_section = cfg.get("products_section")
        self.narrative_claims = []
        for nc in cfg.get("narrative_claims", []):
            self.narrative_claims.append({**nc, "regex": re.compile(nc["pattern"].replace("{N}", NUM_RE))})

    def matches(self, norm_text: str) -> bool:
        """True when every fingerprint pattern is found in the normalized opening text."""
        return bool(self.fingerprint) and all(p.search(norm_text) for p in self.fingerprint)

    def meaning(self, label: str):
        """Return the Meaning for a column label, or None if the label is unmapped."""
        return self.labels.get(normalize(label))

    def is_ignored(self, label: str) -> bool:
        return normalize(label) in self.ignore_labels

    def is_total(self, label: str) -> bool:
        return normalize(label) in self.total_labels

    def is_province(self, label: str) -> bool:
        return normalize(label) in self.provinces

    def is_unallocated(self, label: str) -> bool:
        return normalize(label) in self.unallocated_labels

    def provinces_in(self, norm_text: str):
        padded = f" {norm_text} "
        return sorted({p.replace(" ", "-") for p in self.provinces if f" {p} " in padded})

"""SQLite storage.

Layers are kept separate: documents (raw sources with hashes), candidates
(machine-proposed values with provenance, rule flags and review status),
context (narrative statements for human interpretation), revision_notes
(revisions asserted in report text) and an append-only review log.
Nothing is deleted: rejected candidates and corrections are preserved.
"""
import json
import sqlite3
from datetime import datetime, timezone
import math

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,          -- sha256 of file bytes
  path TEXT, filename TEXT, issuer TEXT, report_no INTEGER,
  period_start TEXT, period_end TEXT, publication_date TEXT,
  retrieved_at TEXT, module TEXT, metadata_flags TEXT, episode TEXT, family_origin TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, disease TEXT, issuer TEXT, period_end TEXT,
  geo TEXT, geo_level TEXT, parent_geo TEXT, is_total INTEGER, table_group TEXT,
  indicator TEXT, classification TEXT, count_type TEXT,
  value INTEGER, raw_label TEXT, raw_value TEXT, source_text TEXT,
  page INTEGER, table_index INTEGER, row_index INTEGER, col_index INTEGER,
  method TEXT,
  flags TEXT DEFAULT '[]',          -- extraction and rule-check results (JSON list)
  flag_notes TEXT DEFAULT '',       -- human-readable explanation of rule flags
  status TEXT DEFAULT 'candidate',  -- candidate | approved | rejected | corrected
  corrected_value INTEGER, reviewer TEXT, reviewed_at TEXT, review_note TEXT
);
CREATE TABLE IF NOT EXISTS context (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, page INTEGER, topic TEXT, state TEXT, geo TEXT, quote TEXT,
  event_date_note TEXT
);
CREATE TABLE IF NOT EXISTS revision_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, page INTEGER, geo TEXT, kind TEXT, old_value INTEGER, new_value INTEGER, quote TEXT
);
CREATE TABLE IF NOT EXISTS statements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, module TEXT, issuer TEXT, period_end TEXT, page INTEGER,
  section TEXT, pillar TEXT, statement_type TEXT, topics TEXT DEFAULT '[]',
  state TEXT, disease TEXT, geos TEXT DEFAULT '[]', health_zones TEXT DEFAULT '[]',
  quote TEXT, quantities TEXT DEFAULT '[]', dates TEXT DEFAULT '[]', caveat TEXT,
  method TEXT, thread_id INTEGER,
  status TEXT DEFAULT 'candidate',  -- candidate | confirmed | retagged | rejected
  reviewer TEXT, reviewed_at TEXT, review_note TEXT
);
CREATE TABLE IF NOT EXISTS statement_review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  statement_id INTEGER, action TEXT, old_tags TEXT, new_tags TEXT,
  reviewer TEXT, note TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS unrecognized (
  doc_id TEXT PRIMARY KEY, filename TEXT, path TEXT, reason TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, event_key TEXT UNIQUE, event_type TEXT, disease TEXT,
  geo_scope TEXT DEFAULT '[]', start_date TEXT, end_date TEXT, title TEXT, aliases TEXT DEFAULT '[]',
  status TEXT DEFAULT 'proposed',   -- proposed | confirmed | merged
  merged_into TEXT, basis TEXT, created_at TEXT, reviewer TEXT, reviewed_at TEXT, review_note TEXT
);
CREATE TABLE IF NOT EXISTS assignments (
  item_kind TEXT, item_key TEXT, event_id TEXT,
  origin TEXT, status TEXT,          -- origin: rule | reviewer ; status: proposed | confirmed | rejected
  basis TEXT, reviewer TEXT, at TEXT, note TEXT,
  PRIMARY KEY (item_kind, item_key, event_id)
);
CREATE TABLE IF NOT EXISTS relations (
  rel_key TEXT PRIMARY KEY, kind TEXT, from_kind TEXT, from_key TEXT, to_kind TEXT, to_key TEXT,
  origin TEXT, status TEXT,          -- origin: rule | rule_explicit | reviewer ; status: proposed | evidenced | confirmed | rejected
  basis TEXT, reviewer TEXT, at TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS issues (
  issue_id TEXT PRIMARY KEY, title TEXT, assessment TEXT, assessed_by TEXT, assessed_at TEXT, assessment_note TEXT
);
CREATE TABLE IF NOT EXISTS issue_members (
  issue_id TEXT, statement_key TEXT, origin TEXT, status TEXT, reviewer TEXT, at TEXT, note TEXT,
  PRIMARY KEY (issue_id, statement_key)
);
CREATE TABLE IF NOT EXISTS gap_notes (
  module TEXT, issuer TEXT, report_no INTEGER, status TEXT, reviewer TEXT, at TEXT, note TEXT,
  PRIMARY KEY (module, issuer, report_no)
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, target TEXT, action TEXT, payload TEXT,
  reviewer TEXT, note TEXT, at TEXT, policy TEXT
);
CREATE TABLE IF NOT EXISTS situation_briefs (
  brief_id TEXT PRIMARY KEY, saved_at TEXT NOT NULL, title TEXT NOT NULL,
  scope TEXT NOT NULL, payload TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, pdf BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER, action TEXT, old_status TEXT, new_status TEXT,
  value INTEGER, old_effective_value INTEGER, new_effective_value INTEGER, reviewer TEXT, note TEXT, at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(review_log)")}
        for name in ("old_effective_value", "new_effective_value"):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE review_log ADD COLUMN {name} INTEGER")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(candidates)")}
        for name in ("period_start", "period_label", "item_key"):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE candidates ADD COLUMN {name} TEXT")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(documents)")}
        for name in ("episode", "family_origin"):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE documents ADD COLUMN {name} TEXT")
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(statements)")}
        if "item_key" not in cols:
            self.conn.execute("ALTER TABLE statements ADD COLUMN item_key TEXT")
        for t, f in (("candidates", self._candidate_key), ("statements", self._statement_key)):
            for r in self.conn.execute(f"SELECT * FROM {t} WHERE item_key IS NULL").fetchall():
                self.conn.execute(f"UPDATE {t} SET item_key=? WHERE id=?", (self._unique_key(t, f(dict(r))), r["id"]))
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_cand_key ON candidates(item_key)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_stmt_key ON statements(item_key)")
        self.conn.commit()

    def has_document(self, doc_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM documents WHERE doc_id=?", (doc_id,)).fetchone() is not None

    def add_document(self, doc: dict):
        cols = ",".join(doc)
        self.conn.execute(f"INSERT INTO documents ({cols}) VALUES ({','.join('?' * len(doc))})", list(doc.values()))

    # Stable keys: derived from source content and location, never from upload order.
    @staticmethod
    def _candidate_key(c):
        import hashlib
        raw = "|".join(str(c.get(k)) for k in ("doc_id", "page", "table_index", "row_index", "col_index", "raw_label",
                                                "table_group", "indicator", "count_type", "period_end"))
        return "C-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _statement_key(st):
        import hashlib
        raw = "|".join(str(st.get(k)) for k in ("doc_id", "page", "statement_type", "quote"))
        return "S-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _unique_key(self, table, key):
        n, k = 1, key
        while self.conn.execute(f"SELECT 1 FROM {table} WHERE item_key=?", (k,)).fetchone():
            n += 1
            k = f"{key}#{n}"
        return k

    def add_candidate(self, c: dict) -> int:
        if not c.get("item_key"):
            c = {**c, "item_key": self._unique_key("candidates", self._candidate_key(c))}
        cols = ",".join(c)
        cur = self.conn.execute(f"INSERT INTO candidates ({cols}) VALUES ({','.join('?' * len(c))})", list(c.values()))
        return cur.lastrowid

    def add_context(self, c: dict):
        cols = ",".join(c)
        self.conn.execute(f"INSERT INTO context ({cols}) VALUES ({','.join('?' * len(c))})", list(c.values()))

    def add_revision_note(self, r: dict):
        cols = ",".join(r)
        self.conn.execute(f"INSERT INTO revision_notes ({cols}) VALUES ({','.join('?' * len(r))})", list(r.values()))

    def set_flags(self, cid: int, flags: list, notes: str = ""):
        self.conn.execute("UPDATE candidates SET flags=?, flag_notes=? WHERE id=?",
                          (json.dumps(sorted(set(flags))), notes, cid))

    def confirmed_event(self, item_kind, item_key):
        """The reviewer-confirmed event of an item, following merges; None if not confirmed."""
        rows = self.conn.execute("SELECT event_id FROM assignments WHERE item_kind=? AND item_key=? AND status='confirmed'",
                                 (item_kind, item_key)).fetchall()
        return self.resolve_event(rows[0]["event_id"]) if rows else None

    def resolve_event(self, event_id):
        seen = set()
        while event_id and event_id not in seen:
            seen.add(event_id)
            r = self.conn.execute("SELECT status, merged_into FROM events WHERE event_id=?", (event_id,)).fetchone()
            if not r or r["status"] != "merged" or not r["merged_into"]:
                return event_id
            event_id = r["merged_into"]
        return event_id

    def review(self, cid: int, action: str, reviewer: str, note: str = "", value=None, require_event: bool = True):
        """Approve, correct or reject an observation.

        Approval and correction require a reviewer-confirmed event assignment, so that
        no value enters a reviewed output without an identified event. Library callers
        testing review mechanics alone may pass require_event=False; the web API never does.
        """
        if action not in ("approve", "reject", "correct"):
            raise ValueError("Unknown review action")
        if not str(reviewer).strip() or not str(note).strip():
            raise ValueError("A reviewer name and source-review reason are required")
        row = self.conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        if row is None:
            raise KeyError(f"No candidate {cid}")
        original = dict(row)
        old_effective = None if original["status"] == "rejected" else original["corrected_value"] if original["status"] == "corrected" else original["value"]
        if action in ("approve", "correct"):
            if "unknown" in (original["indicator"], original["classification"], original["count_type"]):
                raise ValueError("Unresolved meaning: cannot approve until the mapping is fixed and source reprocessed")
            from .harmonize import parse_date
            if not original["issuer"] or not parse_date(original["period_end"]) or not original["geo"] or original["geo"] == "UNNAMED":
                raise ValueError("Missing or invalid issuer, geography or reporting date")
            if require_event and not self.confirmed_event("candidate", original.get("item_key")):
                raise ValueError("Confirm this observation's event assignment before approving or correcting it")
            candidate_value = value if action == "correct" else original["value"]
            if isinstance(candidate_value, bool) or not isinstance(candidate_value, int) or candidate_value < 0:
                raise ValueError("Approved counts must be non-negative integers; use Correct for a missing/invalid value")
        if action != "correct" and value is not None:
            raise ValueError("Only a correction can supply a replacement value")
        new_status = {"approve": "approved", "reject": "rejected", "correct": "corrected"}[action]
        new_effective = None if action == "reject" else value if action == "correct" else original["value"]
        at = now()
        self.conn.execute(
            "UPDATE candidates SET status=?, corrected_value=?, reviewer=?, reviewed_at=?, review_note=? WHERE id=?",
            (new_status, value if action == "correct" else None, reviewer.strip(), at, note.strip(), cid))
        self.conn.execute(
            "INSERT INTO review_log (candidate_id, action, old_status, new_status, value, old_effective_value, new_effective_value, reviewer, note, at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, action, original["status"], new_status, new_effective, old_effective, new_effective, reviewer.strip(), note.strip(), at))

    def add_statement(self, st: dict) -> int:
        if not st.get("item_key"):
            st = {**st, "item_key": self._unique_key("statements", self._statement_key(st))}
        st = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in st.items()}
        cols = ",".join(st)
        cur = self.conn.execute(f"INSERT INTO statements ({cols}) VALUES ({','.join('?' * len(st))})", list(st.values()))
        return cur.lastrowid

    STATEMENT_TYPES = ("challenge", "action", "assessment", "recommendation", "highlight", "observation", "signal",
                       "published_product", "key_message", "background", "commentary")
    STATES = ("ongoing", "resolved", "denied", "worsening", "improving", "new", "asserted", "none_publicly_identified")

    def review_statement(self, sid: int, action: str, reviewer: str, note: str = "", tags=None):
        """Confirm, reject or retag a qualitative statement. The source wording is never edited.

        Tags are validated before writing: statement_type and state must be known values;
        topics, geos and health_zones must be lists of short strings; disease must be a
        short string or empty. Event assignment is changed through the events API.
        """
        if action not in ("confirm", "reject", "retag"):
            raise ValueError("Unknown statement review action")
        if not str(reviewer).strip() or not str(note).strip():
            raise ValueError("A reviewer name and reason are required")
        row = self.conn.execute("SELECT * FROM statements WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(f"No statement {sid}")
        editable = ("statement_type", "topics", "state", "geos", "health_zones", "disease")
        old = {k: row[k] for k in editable}
        updates = {}
        if action == "retag":
            if not isinstance(tags, dict) or not tags:
                raise ValueError("Retag needs at least one changed tag")
            for k, v in tags.items():
                if k not in editable:
                    raise ValueError(f"Tag {k} cannot be edited")
                if k == "statement_type":
                    if v not in self.STATEMENT_TYPES:
                        raise ValueError(f"Unknown statement type: {v}")
                    updates[k] = v
                elif k == "state":
                    if v not in self.STATES:
                        raise ValueError(f"Unknown state: {v}")
                    updates[k] = v
                elif k in ("topics", "geos", "health_zones"):
                    if not isinstance(v, list) or not all(isinstance(x, str) and 0 < len(x.strip()) <= 60 for x in v):
                        raise ValueError(f"{k} must be a list of short text values")
                    updates[k] = json.dumps(sorted({x.strip().lower().replace(" ", "_") if k == "topics" else x.strip().lower() for x in v}),
                                            ensure_ascii=False)
                elif k == "disease":
                    if v is not None and (not isinstance(v, str) or len(v) > 60):
                        raise ValueError("disease must be a short text value or empty")
                    updates[k] = (v or "").strip().lower() or None
        status = {"confirm": "confirmed", "reject": "rejected", "retag": "retagged"}[action]
        at = now()
        sets = ", ".join(f"{k}=?" for k in updates)
        self.conn.execute(f"UPDATE statements SET {sets + ', ' if sets else ''}status=?, reviewer=?, reviewed_at=?, review_note=? WHERE id=?",
                          list(updates.values()) + [status, reviewer.strip(), at, note.strip(), sid])
        self.conn.execute("INSERT INTO statement_review_log (statement_id, action, old_tags, new_tags, reviewer, note, at) VALUES (?,?,?,?,?,?,?)",
                          (sid, action, json.dumps(old, ensure_ascii=False), json.dumps(updates, ensure_ascii=False), reviewer.strip(), note.strip(), at))

    def decide(self, kind, target, action, payload, reviewer, note, policy=None):
        """Append to the decision log (events, assignments, relations, issues, gaps). Never updated or deleted."""
        if not str(reviewer).strip() or not str(note).strip():
            raise ValueError("A reviewer name and reason are required")
        self.conn.execute("INSERT INTO decisions (kind, target, action, payload, reviewer, note, at, policy) VALUES (?,?,?,?,?,?,?,?)",
                          (kind, target, action, json.dumps(payload, ensure_ascii=False), reviewer.strip(), note.strip(), now(), policy))

    def rows(self, sql: str, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def commit(self):
        self.conn.commit()

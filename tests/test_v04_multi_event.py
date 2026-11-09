"""v0.4 regressions: report routing, multi-disease weekly tables, narrative claims,
qualitative statements, issue threads, statement review and cross-report rules.
Real reports are not bundled; inputs here are constructed to mirror their layouts."""
import base64
import glob
import json
import os
import re

import pytest

from outbreak_ledger.extract import Extractor, detect_module, narrative_claims
from outbreak_ledger.harmonize import Module, epi_week_bounds, normalize
from outbreak_ledger.qualitative import Segmenter, tag, vocab
from outbreak_ledger.rules import run_all_rules
from outbreak_ledger.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = lambda n: Module(os.path.join(ROOT, "config", f"{n}.yaml"))


def test_epi_week_conventions_differ():
    assert epi_week_bounds(2026, 41, "mmwr") == ("2026-10-11", "2026-10-17")   # DRC weekly profile, Sun-Sat
    assert epi_week_bounds(2026, 41, "iso") == ("2026-10-05", "2026-10-11")    # WHO WER, Mon-Sun
    assert epi_week_bounds(2026, 1, "mmwr") == ("2026-01-04", "2026-01-10")


WEEKLY = """              Situation          epidemiologique                  en    RDC,      SE    41-   2026
                       SEM39              SEM40               SEM41                 SEM1-S41
    MALADIE
                 Cas   Deces Letalite Cas Deces Letalite Cas  Deces Letalite  Cas      Deces Letalite      Commentaires
CHOLERA          2974   90    3,0%   2729  97    3,6%   1686    57   3,4%    58801     1731    2,9%
                                                                                                       Diminution de 1 043
DECES
                                                                                                         cas de cholera entre la
MATERNELS         51     0    0,0%    48    0    0,0%    53     0    0,0%     2094      0      0,0%
PALUDISME SUSP  545457  404   0,1%  536628 39    0,0%  506772   40   0,0%   22218366   3060    0,0%     rapport a la SE40
FOOBAR            1      0    0,0%    2     0    0,0%    3      0    0,0%     9         0      0,0%
                                                          Sources : INSP
"""


def weekly_extractor(tmp_path):
    m = CFG("drc_insp_weekly_profile")
    s = Store(str(tmp_path / "w.db"))
    ex = Extractor(m, s, "doc", {"issuer": "INSP-RDC", "period_end": "2026-10-17", "period_start": "2026-10-11",
                                  "epi_week": 41, "epi_year": 2026}, [])
    return m, s, ex


def test_disease_rows_multi_event_table(tmp_path):
    m, s, ex = weekly_extractor(tmp_path)
    ex.disease_rows_text(m.adapters[0], [(2, WEEKLY)])
    rows = s.rows("SELECT * FROM candidates")
    got = {(r["disease"], r["classification"], r["indicator"], r["count_type"], r["period_label"]): r for r in rows}
    c = got[("cholera", "idsr_notified", "cases", "new", "2026-W41")]
    assert c["value"] == 1686 and c["period_end"] == "2026-10-17" and c["period_start"] == "2026-10-11"
    ytd = got[("cholera", "idsr_notified", "cases", "cumulative", "2026-W01..W41")]
    assert ytd["value"] == 58801 and ytd["period_start"] == "2026-01-04"
    assert got[("cholera", "idsr_notified", "deaths", "new", "2026-W39")]["value"] == 90
    mat = got[("maternal_death", "idsr_notified", "cases", "new", "2026-W41")]
    assert mat["value"] == 53 and "name_joined_from_lines" in mat["flags"]      # DECES + MATERNELS
    assert got[("malaria", "suspected", "cases", "new", "2026-W41")]["value"] == 506772   # commentary ignored
    unk = [r for r in rows if r["disease"] == "foobar"]
    assert unk and all("unmapped_row_label" in r["flags"] and r["indicator"] == "unknown" for r in unk)


def test_narrative_claim_keeps_its_own_definition(tmp_path):
    m, s, ex = weekly_extractor(tmp_path)
    page = "Cholera: Analyse hebdomadaire SE41 - 2026\n\u2022 41 2330 cas suspect contre 2709 cas au SE40\n"
    narrative_claims(ex, m, [(9, page)])
    rows = {r["period_label"]: r for r in s.rows("SELECT * FROM candidates")}
    assert rows["2026-W41"]["value"] == 2330          # not '41 2330'
    assert rows["2026-W40"]["value"] == 2709
    assert all(r["classification"] == "suspected" and r["disease"] == "cholera" and r["table_group"] == "narrative"
               and "from_narrative" in r["flags"] for r in rows.values())


SITREP_PAGES = [(5, """5
2. ACTIONS DE RIPOSTE PAR PILIER
2.1. Surveillance epidemiologique
2.1.1. Principales actions
Au 30 octobre 2026, 3 802 alertes ont ete notifiees dans les provinces touchees.
2.1.2. Defis
Au Nord-Kivu, le PoC Butuhe reste ferme depuis l'incident securitaire du 26 septembre 2026 ; au Sud-Kivu, la completude reste faible (56 %).
161 528 100 862 8 957 21 940 16 252 6 873
2.3.Laboratoire
Un total de 464 nouveaux echantillons a ete analyse au laboratoire de Bunia.
Au cours des dernieres 24 heures 81 nouveaux cas confirmes ont ete notifies dans quatre provinces.
""")]


def test_segmenter_sections_types_and_clauses():
    m = CFG("bvd_drc_insp")
    seg, v = Segmenter(m), vocab()
    out = [(u, st) for u in seg.units(SITREP_PAGES) for st in seg.statements(u)]
    texts = [st for _, st in out]
    assert not any("161 528" in t for t in texts)                                  # table residue skipped
    ch = [(u, st) for u, st in out if u["section_type"] == "challenge"]
    assert len(ch) == 2 and ch[0][0]["pillar"] == "surveillance"                   # clause split on ';'
    t = tag(ch[0][1], ch[0][0], m, v, {})
    assert t["statement_type"] == "challenge" and t["state"] == "ongoing"
    assert "nord-kivu" in t["geos"] and {"insecurity", "poe_screening"} <= set(t["topics"])
    lab = [u for u, st in out if "464" in st][0]
    assert lab["pillar"] == "laboratory"                                           # '2.3.Laboratoire' without space
    syn = [u for u, st in out if "81 nouveaux cas" in st][0]
    assert syn["section"] == "Synthesis"                                           # summary block not filed under a pillar
    hz = tag("Insuffisance de produits sanguins dans les ZS de Bunia et de Rwampara", ch[0][0], m, v, {"bunia": "bunia"})
    assert "bunia" in hz["health_zones"]


def test_states():
    m, v = CFG("bvd_drc_insp"), vocab()
    u = {"section_type": "challenge"}
    assert tag("Le blocage du pont de Lolwa a ete leve et la mission a repris ce matin", u, m, v, {})["state"] == "resolved"
    assert tag("Aucune rupture de stock de reactifs n'a ete signalee cette semaine", u, m, v, {})["state"] == "denied"


def add_doc(s, doc, period, module="bvd_drc_insp", issuer="INSP-RDC", no=1):
    s.add_document({"doc_id": doc, "path": "", "filename": f"{doc}.pdf", "issuer": issuer, "report_no": no,
                    "period_start": period, "period_end": period, "publication_date": period, "module": module})


def add_challenge(s, doc, period, quote, topics, geos, state="ongoing"):
    s.add_statement({"doc_id": doc, "module": "bvd_drc_insp", "issuer": "INSP-RDC", "period_end": period, "page": 1,
                     "statement_type": "challenge", "topics": topics, "geos": geos, "health_zones": [],
                     "state": state, "quote": quote, "quantities": [], "dates": []})


def test_statement_review_preserves_wording(tmp_path):
    s = Store(str(tmp_path / "r.db"))
    add_doc(s, "d0", "2026-10-26")
    add_challenge(s, "d0", "2026-10-26", "Original wording stays", ["insecurity"], ["ituri"])
    with pytest.raises(ValueError):
        s.review_statement(1, "retag", "tester", "checked", {"quote": "edited"})
    with pytest.raises(ValueError):
        s.review_statement(1, "confirm", "", "")
    s.review_statement(1, "retag", "tester", "topic was wrong", {"topics": ["supply_shortage"], "state": "resolved"})
    row = s.rows("SELECT * FROM statements")[0]
    assert row["quote"] == "Original wording stays" and json.loads(row["topics"]) == ["supply_shortage"]
    assert row["status"] == "retagged" and len(s.rows("SELECT * FROM statement_review_log")) == 1


def cand(s, doc, **kw):
    base = dict(doc_id=doc, issuer="INSP-RDC", disease="mpox", geo="drc", geo_level="national", is_total=0,
                indicator="cases", classification="idsr_notified", table_group="disease_table", flags="[]")
    base.update(kw)
    s.add_candidate(base)


def test_weekly_revision_rules_and_definitions(tmp_path):
    s = Store(str(tmp_path / "x.db"))
    add_doc(s, "se41", "2026-10-17", module="drc_insp_weekly_profile", no=41)
    add_doc(s, "se42", "2026-10-24", module="drc_insp_weekly_profile", no=42)
    cand(s, "se41", period_end="2026-10-17", count_type="new", value=594)
    cand(s, "se41", period_end="2026-10-17", count_type="cumulative", value=33484)
    cand(s, "se42", period_end="2026-10-17", count_type="new", value=587)          # week 41 revised
    cand(s, "se42", period_end="2026-10-24", count_type="new", value=539)
    cand(s, "se42", period_end="2026-10-24", count_type="cumulative", value=34016)  # +532, not +539
    cand(s, "se42", period_end="2026-10-24", count_type="new", value=510, classification="suspected",
         table_group="narrative")
    s.commit()
    r = run_all_rules(s, {"drc_insp_weekly_profile": CFG("drc_insp_weekly_profile")})
    flags = {(x["period_end"], x["count_type"], x["doc_id"], x["classification"]): json.loads(x["flags"])
             for x in s.rows("SELECT * FROM candidates")}
    assert "revised_in_later_report" in flags[("2026-10-17", "new", "se42", "idsr_notified")]
    cum = flags[("2026-10-24", "cumulative", "se42", "idsr_notified")]
    assert "new_cumulative_mismatch" in cum and "consistent_with_revision" in cum
    assert "related_definitions_differ" in flags[("2026-10-24", "new", "se42", "suspected")]
    assert {f["type"] for f in r["findings"]} >= {"revised_in_later_report", "related_definitions_differ"}


def test_routing_recognises_and_refuses(tmp_path):
    from samples.make_samples import build_all
    build_all(str(tmp_path / "reports"))
    pdfs = sorted(glob.glob(str(tmp_path / "reports" / "*.pdf")))
    real = [CFG(n) for n in ("bvd_drc_insp", "drc_insp_weekly_profile", "who_wer")]
    assert all(detect_module(p, real) is None for p in pdfs)
    assert all(detect_module(p, real + [CFG("synthetic_demo")]).name == "synthetic_demo" for p in pdfs)


def test_events_workspace_lists_unrecognised_without_counting(tmp_path):
    from outbreak_ledger import web
    from samples.make_samples import build_all
    build_all(str(tmp_path / "reports"))
    files = [{"name": os.path.basename(p), "data": base64.b64encode(open(p, "rb").read()).decode()}
             for p in sorted(glob.glob(str(tmp_path / "reports" / "*.pdf")))]
    res = web.import_files(str(tmp_path / "data"), "events", files)
    assert all("nothing was counted" in r["error"] for r in res)
    st = web.state(str(tmp_path / "data"), "events")
    assert st["stats"]["candidates"] == 0 and len(st["unrecognized"]) == 4 and st["events_list"] == []
    assert all(u["diagnosis"]["reason"] for u in st["unrecognized"])
    res2 = web.import_files(str(tmp_path / "data"), "synthetic_demo", files)
    assert all(r.get("candidates") for r in res2)
    st2 = web.state(str(tmp_path / "data"), "synthetic_demo")
    assert st2["stats"]["statements"] > 0 and st2["events_list"]


REAL = os.environ.get("OL_REAL_REPORTS")


@pytest.mark.skipif(not REAL, reason="set OL_REAL_REPORTS to a folder of the real reports to run")
def test_optional_real_reports(tmp_path):
    from outbreak_ledger.extract import ingest
    mods = {n: CFG(n) for n in ("bvd_drc_insp", "drc_insp_weekly_profile", "who_wer")}
    s = Store(str(tmp_path / "real.db"))
    seen = set()
    for p in sorted(glob.glob(os.path.join(REAL, "*.pdf"))):
        m = detect_module(p, list(mods.values()))
        if m:
            ingest(p, m, s)
            seen.add(m.name)
    run_all_rules(s, mods)
    assert seen, "no supported report found"
    assert s.rows("SELECT COUNT(*) AS n FROM statements")[0]["n"] > 0

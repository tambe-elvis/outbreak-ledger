"""Tests for features needed by real report formats (no real reports bundled)."""
import os

from outbreak_ledger.extract import Extractor, parse_value
from outbreak_ledger.harmonize import Module, parse_date
from outbreak_ledger.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parse_value_variants():
    assert parse_value("3 388") == (3388, [])
    assert parse_value("10 141") == (10141, [])
    assert parse_value("3 769 *") == (3769, ["footnote_marker"])
    assert parse_value("NA") == (None, ["explicit_na"])
    assert parse_value("") == (None, ["blank_cell"])
    assert parse_value("46,8%")[0] is None


def test_parse_french_dates():
    assert parse_date("26 octobre 2026") == "2026-10-26"
    assert parse_date("1er novembre 2026") == "2026-11-01"


LAYOUT = """Tableau 2. Repartition des cas et deces confirmes par province et zone
 Province /
 Zone de sante   Cas (n)   Deces (n)   Letalite
 Tshopo   89   27   30,3%   3   0
 Bafwasende   18   3   16,7%
 Makiso-
          34   14   41,2%   1
 Kisangani
 Tshopo   5   0   0,0%
                          3
 A ventiler   NA   12   NA
 Total   146   56   38,4%
"""


def test_positional_text_reader(tmp_path):
    m = Module(os.path.join(ROOT, "config", "bvd_drc_insp.yaml"))
    s = Store(str(tmp_path / "t.db"))
    ex = Extractor(m, s, "doc", {"issuer": "TEST", "period_end": "2026-10-27"}, [])
    ex.positional_text(next(a for a in m.adapters if a["type"] == "positional_text"), [(1, LAYOUT)])
    rows = s.rows("SELECT geo, geo_level, parent_geo, indicator, value, flags FROM candidates WHERE indicator='cases'")
    got = {(r["geo"], r["geo_level"]): r for r in rows}
    assert got[("tshopo", "province")]["value"] == 89
    assert got[("makiso-kisangani", "health_zone")]["value"] == 34
    assert "name_joined_from_lines" in got[("makiso-kisangani", "health_zone")]["flags"]
    assert got[("tshopo", "health_zone")]["parent_geo"] == "tshopo"
    assert "hz_shares_province_name" in got[("tshopo", "health_zone")]["flags"]
    assert got[("UNALLOCATED", "unallocated")]["value"] is None  # explicit NA, not zero
    assert got[("drc", "national")]["value"] == 146
    assert len(rows) == 6  # the stray '3' line is not read as a row

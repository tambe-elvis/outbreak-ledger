import csv

from outbreak_ledger.cli import main


def test_demo_exports_reviewed_practice_values(tmp_path):
    main(["demo", "--outdir", str(tmp_path)])
    with (tmp_path / "approved_observations.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows
    assert all(row["status"] == "approved" for row in rows)
    assert (tmp_path / "audit_report.html").is_file()

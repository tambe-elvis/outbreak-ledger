"""Synthetic acceptance-test reports (TEST DATA). Each spec becomes one digital PDF."""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def report(no, date, pub, rows, text=(), issuer="TEST-MOH", episode=3, columns=None):
    return {"no": no, "date": date, "pub": pub, "rows": rows, "text": list(text), "issuer": issuer, "episode": episode,
            "columns": columns or ["Province", "New confirmed cases", "Cumulative confirmed cases"]}


def build(spec, folder, name=None):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name or f"acc_{spec['issuer']}_{spec['no']:02d}_{spec['pub']}.pdf")
    st = getSampleStyleSheet()
    story = [Paragraph(f"Acceptance test situation report No {spec['no']} (TEST DATA)", st["Heading2"]),
             Paragraph(f"Issuer: {spec['issuer']}", st["Normal"]),
             Paragraph(f"Reporting date: {spec['date']}", st["Normal"]),
             Paragraph(f"Publication date: {spec['pub']}", st["Normal"]),
             Paragraph(f"Subject: cholera outbreak no {spec['episode']} in Testland, synthetic test data only.", st["Normal"]),
             Spacer(1, 10)]
    t = Table([spec["columns"]] + [[str(v) for v in r] for r in spec["rows"]])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    story += [t, Spacer(1, 10)] + [Paragraph(x, st["Normal"]) for x in spec["text"]]
    SimpleDocTemplate(path, pagesize=A4).build(story)
    return path

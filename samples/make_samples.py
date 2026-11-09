"""Generate a small SYNTHETIC test corpus of outbreak situation reports.

All issuers, health zones and figures are fictional. The corpus is designed
to exercise known difficulties: changing layouts and labels, French and
English, a stated revision, a missing health zone, a subtotal mismatch,
ambiguous columns, an unmapped column and a conflict between two issuers.
Replace with real reports and an expert-curated reference for evaluation.
"""
import csv
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPORTS = [
    {
        "file": "msp_demo_sitrep_10_fr.pdf", "issuer": "MSP-DEMO", "no": 10,
        "header": ["Rapport de situation No 10 - Maladie a virus Bundibugyo (DONNEES SYNTHETIQUES)",
                   "Émetteur : MSP-DEMO", "Période : 2026-06-01 au 2026-06-07", "Date de publication : 2026-06-08"],
        "columns": ["Zone de santé", "Nouveaux cas confirmés", "Cas confirmés cumulés", "Décès cumulés"],
        "rows": [["Alpha", 3, 10, 2], ["Beta", 1, 4, 1], ["Gamma", 1, 2, 0], ["Total", 5, 16, 3]],
        "text": ["L'insécurité limite le suivi des contacts dans la ZS Beta depuis le 5 juin."],
    },
    {
        "file": "msp_demo_sitrep_11_fr.pdf", "issuer": "MSP-DEMO", "no": 11,
        "header": ["Rapport de situation No 11 - Maladie a virus Bundibugyo (DONNEES SYNTHETIQUES)",
                   "Émetteur : MSP-DEMO", "Période : 2026-06-08 au 2026-06-14", "Date de publication : 2026-06-15"],
        "columns": ["ZS", "Confirmés (cumul)", "Confirmés (nouveaux)", "Probables", "Décès"],
        "rows": [["Alpha", 12, 2, 3, 2], ["Beta", 3, 0, 1, 1], ["Gamma", 2, 0, 0, 0], ["Total", 17, 2, 4, 3]],
        "text": ["Le cumul des cas confirmés de la ZS Beta a été révisé de 4 à 3 suite à la reclassification d'un cas.",
                 "Le suivi des contacts a repris dans la ZS Beta après amélioration de la sécurité."],
    },
    {
        "file": "msp_demo_sitrep_12_fr.pdf", "issuer": "MSP-DEMO", "no": 12,
        "header": ["Rapport de situation No 12 - Maladie a virus Bundibugyo (DONNEES SYNTHETIQUES)",
                   "Émetteur : MSP-DEMO", "Période : 2026-06-15 au 2026-06-21", "Date de publication : 2026-06-22"],
        "columns": ["ZS", "Confirmés (cumul)", "Confirmés (nouveaux)", "Probables", "Décès"],
        "rows": [["Alpha", 13, 1, 3, 2], ["Beta", 3, 0, 1, 1], ["Total", 18, 1, 4, 3]],
        "text": ["Aucune rupture de stock de réactifs n'a été signalée cette semaine."],
    },
    {
        "file": "agency_demo_sitrep_05_en.pdf", "issuer": "AGENCY-DEMO", "no": 5,
        "header": ["External situation report No 5 - Bundibugyo virus disease (SYNTHETIC DATA)",
                   "Issuer: AGENCY-DEMO", "Period: 2026-06-08 to 2026-06-14", "Publication date: 2026-06-16"],
        "columns": ["Health zone", "New confirmed cases", "Cumulative confirmed cases",
                    "Deaths among confirmed cases (cumulative)", "Suspected cases under investigation"],
        "rows": [["Alpha", 2, 12, 2, 5], ["Beta", 0, 4, 1, 2], ["Gamma", 0, 2, 0, 1], ["Total", 2, 18, 3, 8]],
        "text": ["A laboratory backlog of 40 samples was reported in Alpha health zone."],
    },
]

# Ground truth for cells whose meaning is fully defined by the module.
MEANINGS = {
    "Nouveaux cas confirmés": ("cases", "confirmed", "new"),
    "Cas confirmés cumulés": ("cases", "confirmed", "cumulative"),
    "Décès cumulés": ("deaths", "unspecified", "cumulative"),
    "Confirmés (cumul)": ("cases", "confirmed", "cumulative"),
    "Confirmés (nouveaux)": ("cases", "confirmed", "new"),
    "New confirmed cases": ("cases", "confirmed", "new"),
    "Cumulative confirmed cases": ("cases", "confirmed", "cumulative"),
    "Deaths among confirmed cases (cumulative)": ("deaths", "confirmed", "cumulative"),
}


def build_pdf(spec, path):
    styles = getSampleStyleSheet()
    story = [Paragraph(spec["header"][0], styles["Heading2"])]
    story += [Paragraph(h, styles["Normal"]) for h in spec["header"][1:]]
    story.append(Spacer(1, 12))
    t = Table([spec["columns"]] + [[str(v) for v in r] for r in spec["rows"]])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                           ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    story += [t, Spacer(1, 12)]
    story += [Paragraph(x, styles["Normal"]) for x in spec["text"]]
    SimpleDocTemplate(path, pagesize=A4).build(story)


def build_all(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    ref_path = os.path.join(os.path.dirname(out_dir), "reference_synthetic.csv")
    with open(ref_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["issuer", "report_no", "geo", "indicator", "classification", "count_type", "value"])
        for spec in REPORTS:
            build_pdf(spec, os.path.join(out_dir, spec["file"]))
            for row in spec["rows"]:
                geo = "TOTAL" if row[0] == "Total" else row[0].lower()
                for col, val in zip(spec["columns"][1:], row[1:]):
                    if col in MEANINGS:
                        w.writerow([spec["issuer"], spec["no"], geo, *MEANINGS[col], val])
    return ref_path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print(build_all(os.path.join(here, "reports")))

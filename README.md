# Outbreak Ledger

Outbreak Ledger is an open-source local prototype led by Tambe Elvis Akem. It helps outbreak analysts review claims from situation reports, trace figures to their source pages and save a one-page briefing with the evidence behind it.

**[90-second walkthrough](docs/example/README.md)** · **[Example brief](docs/example/01_original.pdf)** · **[Example evidence record](docs/example/01_original.json)**

The walkthrough uses fictional data. It shows how an approved cumulative count changes after a simulated correction while the earlier saved brief preserves the original reviewed figure.

## Quick start

Use Python 3.12 from the repository root. The first installation downloads dependencies. The application then runs locally.

```sh
python -m venv .venv
# On macOS or Linux
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m outbreak_ledger.web --open
```

On Windows, run these in PowerShell after creating the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m outbreak_ledger.web --open
```

Windows users can also use [the batch launcher](READ_ME_FIRST.txt). If the browser does not open, visit `http://127.0.0.1:8765`. Open **Review workspace → Manage reports → Load practice reports**. Confirm the event and its links, approve the figures you have checked, then open **Overview** and save a brief.

### Scripted command-line demo

Use the same Python executable as above to run `python -m outbreak_ledger demo`. The scripted review uses four fictional reports and writes an audit report and CSV to `demo_output/`. It is separate from the interactive walkthrough.

### Tests

Run `python -m pytest -q` using the project environment. The optional real-report test requires a local corpus. For wheel installs, launch with an explicit writable workspace, for example `python -m outbreak_ledger.web --data ./local_data --open`.

## What 0.5 is for

Outbreak Ledger keeps an inspectable evidence record across successive
outbreak reports. Evidence accumulates with every upload. Figures keep their
definitions and periods. Every change in a displayed result follows a
versioned rule (policy OL-RP-1) or a named reviewer decision, so the same
evidence and decisions always give the same result, whatever the upload order.

## Reader dashboard and review workspace

Overview, What changed, Response updates and Reports and sources present
reviewed evidence in English. Confirmed events and reviewed figures retain
their dates, scope and original page links. A reviewed English response note
can be saved alongside the original statement. Review workspace contains
report management, source comparisons, review decisions and evidence links.

See [Reader and reviewer guide](README_PUBLIC_VIEW.txt) for the browser workflow.

## Core rules

Events. Three types: outbreak episode, surveillance stream, public-health
signal. Identifiers derive from type, disease, place and episode, so they do
not depend on upload order. Rules propose events and evidence links; a
reviewer confirms them. An observation can only be approved once its event
link is confirmed. A statement can be linked to several events (a shared
constraint), but only explicitly.

Figures. A series is a set of comparable claims: same event, issuer, report
family, place and level, indicator, classification, count type and period
kind. Within a series the timeline follows the reporting period; publication
date and report number order versions of claims about the same period.
- Cumulative snapshots are never added; differences are labelled net
  reported change, which may include revisions or reclassification.
- New counts belong only to their stated period; gaps are never filled.
- A gap is called missing reports only when the report numbering is known to
  be consecutive; otherwise coverage is unknown. A reviewer can record that a
  number was not issued.
- Same issuer, same period, different value: superseded only if the later
  report states the revision (evidenced) or a reviewer confirms it.
  Otherwise the period is unresolved and both claims stay visible.
- Different issuers are shown side by side; no consensus is computed.
- Latest reported claim and latest reviewed value are always separate.

Statements. Each statement is a dated claim in the report's exact words.
Challenges about the same problem form an issue with a stable identifier,
within one event, topic and place. Other statements relate to issues as
updates, addresses, reports resolution, contradicts or recurs. Rules propose
links; reviewers confirm or reject them. The source's stated state, the
reviewer's assessment and the derived status are kept apart. An action does
not establish resolution, and absence from a later report never means
resolved.

## Exports

- Reviewed observations CSV: every approved or corrected claim, with event,
  disease, family, full period bounds, supersession and conflict flags.
  Competing reviewed claims are exported individually, flagged.
- Reconciled series CSV: one row per series and period with a single
  reviewed value; periods whose reviewed claims conflict are listed as
  withheld.
- Reviewed statements CSV: confirmed or retagged statements with events,
  issue and source hash.
All three check source integrity and start with a line recording the policy,
configuration fingerprint and generation time.

## Processing real reports

    python -m outbreak_ledger run FOLDER --outdir OUT

Recognises each PDF, extracts, proposes events, reconciles and links, then
writes events.csv, series_timeline.csv, issues.csv, statements.csv,
findings.csv, unrecognised.csv (with reasons) and review_sheet.csv.
Optional test on your own reports: set OL_REAL_REPORTS to a folder of PDFs
and run python -m pytest -q.

## Supported inputs

Digital PDFs of three report families: DRC INSP daily Ebola (BVD) sitreps,
the DRC INSP weekly epidemiological profile, and the WHO Weekly
Epidemiological Record. Other layouts are listed as unrecognised with the
reason; a reviewer can assign a family and metadata, and the file is
accepted only if extraction then yields readable content.

## Known limits

See [validation and limitations](VALIDATION.md). In short: no OCR, no AI model, no Word/Excel/web inputs;
qualitative tags and relation proposals are rule-based with unmeasured
accuracy (relation proposals on real reports are noisy and need review);
expert verification of extracted values is pending; the browser receives the
whole workspace state (about 6.6 MB for the development corpus); the
decision log is append-only by design but not tamper-proof.

> **Independent review, 4 November 2026:** this copy contains a startup guard and clearer setup errors. The extraction engine is unchanged. Read REVIEW_2026_11_04.md for confirmed unresolved attribution, export and event-separation defects before evaluating multi-event results. Extract the complete ZIP before running the launcher.

# Outbreak Ledger - Local Pilot v0.4

A local browser application that reads public outbreak and public-health reports, turns their numbers into reviewable observations and their narrative into reviewable qualitative statements, and keeps every item linked to its source page.

## New in v0.4

- One workspace for several outbreaks and events. Each uploaded PDF is recognised by its report family and routed automatically. Supported families: DRC INSP daily Ebola (BVD) situation reports; the DRC INSP weekly epidemiological profile (22 diseases per report); the WHO Weekly Epidemiological Record. A file matching no family is listed as unrecognised; nothing is counted from it, and its absence is never treated as zero.
- Event register: every disease or event found, with its report families, latest reported count, review progress, statements and open issues. Events are shown side by side and never summed.
- Qualitative evidence: narrative text becomes statements kept in the report's exact words, with page, section and response pillar, and suggested tags for statement type (challenge, action, assessment, recommendation, highlight, signal, published product), topic, state (ongoing, resolved, denied, worsening, improving, new), event, place, quantities and dates mentioned. Reviewers confirm, correct or reject tags; the wording is never edited.
- Issue tracker: challenge statements are linked across successive reports of the same issuer, showing first and last report and whether the issue is open in the latest report. "Not mentioned in latest report" is never shown as resolved.
- WHO signals: the WER table of new signals is read region by region; "no publicly available signals identified" keeps WHO's caveat that this does not imply absence of signal activity.
- Weekly rules: values revised in a later report, year-to-date totals inconsistent with new counts (and whether the revision explains the gap), and related figures under different definitions in the same report (for example a table count versus a slide figure) are flagged, never merged.
- Narrative counts: figures stated in prose (such as WHO's cumulative BVD totals) are kept as separate claims with their own definition.
- Epidemiological weeks follow each source's convention: Sunday to Saturday for the DRC weekly profile, Monday to Sunday (ISO) for WHO.
- Larger batches in the browser: up to 25 files, 20 MB each, 60 MB per batch. Folders of any size: `python -m outbreak_ledger run FOLDER --outdir OUT`.

Project lead: **Tambe Elvis Akem · IRNR Group**

## Start on Windows

1. **Extract the entire ZIP** into a new folder. Do not run the launcher from inside the ZIP, and keep your earlier prototype folder separate.
2. Double-click **START_OUTBREAK_LEDGER.bat**. Windows may hide the `.bat` extension; choose the Windows Batch File bearing this name.
3. The first launch creates a private Python environment and downloads the required packages. An internet connection and Python are required for this setup. Python 3.12 is recommended; the Python installation you already used for the prototype should work.
4. Your browser opens **http://127.0.0.1:8765**. Leave the launcher window open while using the application.
5. On later visits, double-click the same launcher. Routine processing works without internet and requires no typed commands.

The Python program powers the application locally. Closing a browser tab does not stop it; close the launcher window or press Ctrl+C to stop. The application does not run automatically after the computer restarts.

## Try the complete workflow

1. Click **Load synthetic example**. It loads four clearly labelled test PDFs into a separate workspace; it does not approve any values.
2. Select an observation in **Review observations**.
3. Inspect the number, date, area, classification and count type against the displayed source page. **Open original PDF** provides the complete document. Source-cell indices are zero-based extraction positions, not highlighted coordinates.
4. Enter your reviewer name. Choose **Approve extracted value**, **Correct count**, or **Reject observation**. Enter a reason, confirm that you checked the source, then save.
5. Open **Review history** to see the old and new effective values. The original extracted value remains unchanged.
6. Use **Export approved CSV**. Only approved or corrected, unambiguous observations are exported. Conflicting approved values for the same issuer/meaning/period block export until a reviewer resolves them explicitly.

To try your reports, keep **Outbreak workspace (automatic recognition)** selected, choose digital PDF files of any supported family and click **Process reports**. Upload at most 25 files, 20 MB per file and 60 MB per batch. Layout changes can require adapter changes. The real reports used for development are not included.

Open **Event register** first, then **Qualitative evidence** to read statements and the issue tracker, and **Review observations** for numbers.

Note for users of v0.3: earlier workspaces (`local_data/bvd_drc_insp`) are kept but not shown. Upload the reports again into the new outbreak workspace.

## Automatic processing and human responsibility

Upload triggers source archiving, deterministic extraction, data-quality checks and the review queue. Saving a review recalculates checks and approved indicators. No values are automatically approved through this interface. Passing a rule check is not verification. Flags can remain on an approved observation and require interpretation.

Approval uses the originally extracted number. To retain or change a previous correction, choose **Correct count**. Rejecting does not erase the original record or its earlier reviews. Unknown indicator meanings cannot be approved; changing an unresolved classification, geography or date requires an adapter change and reprocessing in a fresh test workspace in this version.

Analytical selection keeps issuers separate. Conflicting agency figures remain separate claims; the application does not produce an agency-combined total or choose an authoritative issuer. Identical approved claims within one issuer/meaning/period are represented once in the exported analytical view, while all original records remain in the database.

The included indicators cover explicit approved new-confirmed counts and elapsed days since a positive reporting period. Missing expected daily reports or unresolved subsequent counts withhold the elapsed result. This is not time since infection, symptom onset, transmission or outbreak closure. The BVD module currently checks coverage at province level; its health-zone adapter extracts cumulative counts. HZ-OPI is not implemented.

## Saved records and backup

The application creates **local_data** beside the code. It contains separate workspaces, SQLite databases, original uploaded PDFs named by SHA-256, and configuration snapshots. Browser reloads and application restarts preserve these files. Your reviewer name is a label in the audit log, not an authenticated identity.

After stopping the application, copy the **entire local_data folder** to a safe backup location. Back up the full project folder as well if you need to preserve the exact code and configuration. Do not edit archived PDFs: source display and export check their hashes. The review log is application-maintained, not cryptographically protected against someone editing the database.

No uploaded files or review content are sent to an external service by this application. Normal launch binds only to 127.0.0.1. Do not expose the development server to the internet or use it as a multi-user institutional server. Public reports can contain names, small counts or sensitive locations; inspect exports before sharing them.

## What this pilot does not yet provide

- OCR for scans or phone photographs; local language-model extraction or tagging; general understanding of arbitrary PDF layouts; Word, Excel, CSV or web-page inputs.
- Validated qualitative tagging: tags come from word lists and headings, with known errors; their precision has not been measured.
- Scheduled retrieval of official reports, unattended background service or email alerts.
- Institutional accounts, shared hosting, tamper-evident logs or full historical indicator replay.
- Validated cross-disease generalization, operational HZ-OPI or a completed independent benchmark. Weekly-profile coverage is national only (no provincial rows).

This release demonstrates the local upload, recognise, extract, inspect, review and export pathway for numbers and narrative across several report families. It is an engineering pilot for evaluation, not a validated epidemic-response system. Do not describe the existing extractor as AI-powered in fellowship evidence.

## Troubleshooting

**Nothing opens:** read the launcher window. Python 3.10+ is required; 3.12 is recommended. If first setup failed, check internet connectivity and double-click the launcher again. Do not bypass organizational installation restrictions.

**Address already in use:** close an earlier Outbreak Ledger launcher, then reopen this one. Existing records remain available.

**The tab cannot connect:** keep the launcher running and reopen http://127.0.0.1:8765.

**No supported table found:** check the selected report family. Scanned files and other layouts are not supported. An unsupported file is not evidence of zero cases.

**A source image fails:** try Open original PDF and check the launcher. A changed/missing source file fails the integrity check. Restore original files from backup.

**Export is disabled:** approve at least one observation and resolve conflicting approved claims. Use the All observations filter and review flags to inspect competing records.

**Run checks:** after initial setup, double-click **CHECK_INSTALLATION.bat**. This runs the included regression suite and leaves its result visible.

## Technical entry points

- Browser app: `python -m outbreak_ledger.web --open`
- Optional storage location: `python -m outbreak_ledger.web --data PATH --open`
- Optional port: `python -m outbreak_ledger.web --port 8766 --open`
- Folder run with automatic routing: `python -m outbreak_ledger run FOLDER --outdir OUT` (writes statements.csv, issue_threads.csv, findings.csv, unrecognised.csv, review_sheet.csv)
- Legacy CLI commands: `python -m outbreak_ledger --help`
- Optional test on your own reports: set `OL_REAL_REPORTS` to a folder of PDFs, then `python -m pytest -q`
- Regression tests: `python -m pytest -q`

The developer-only `--preview` flag deliberately binds beyond loopback for the managed QA environment. It is never used by the Windows launcher and is not a deployment mode.

The legacy CLI synthetic demo scripts approvals for demonstration. Its perfect synthetic extraction score is a fixture check, not real-world accuracy evidence. Expert verification sheets measure sampled-row accuracy only; independent full-report annotation is needed to estimate recall. The extracted-value column in a verification sheet is not used as its own ground truth.

See **VALIDATION.md** for checks and verification limits. Code: MIT license, included in LICENSE.

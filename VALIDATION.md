# Validation record - Outbreak Ledger 0.5.0

Checked on 4 November 2026, Python 3.12, Linux. Windows was not available.

## Automated tests

52 passed, 1 skipped. The skipped test runs on a folder of real reports
(set OL_REAL_REPORTS); it passed on the 13-PDF development corpus.

The ten acceptance behaviours of the 0.5 brief are automated in
tests/test_v05_acceptance.py, on synthetic reports of a test-only family:
1. Uploading reports in different orders gives identical series, reviewed
   values, event identifiers and issues under the same decisions.
2. Cumulative 100 and 250 never produce 350; the difference is labelled net
   reported change; new counts stay in their periods; reports 2 to 9 are
   named missing only because numbering is consecutive (otherwise "coverage
   unknown"); a reviewer can record a number as not issued.
3. A stated same-issuer revision supersedes (evidenced); an unexplained
   different value leaves the period unresolved; a suspected-case claim stays
   a separate series; a reviewer can confirm a supersession.
4. Conflicting issuers are kept side by side with no consensus value.
5. Two episodes of the same disease stay separate through review and export;
   approval is refused until the event link is confirmed.
6. Stock-out, then reagents dispatched (addresses, not resolution), then
   "testing resumed, but backlog remains": resolution reported for the
   stock-out; the backlog is a separate open issue.
7. A later report that does not mention the backlog leaves it "last reported
   ongoing ... no later update".
8. Rejecting a statement or observation updates summaries and keeps the
   record and its log; malformed tags are refused.
9. An older report uploaded late fills history without becoming latest.
10. Reopening the database preserves identities, reviews and confirmed links.

Regression tests also cover the reviewed defects (narrative attribution,
cross-event issue linking, rejected statements in summaries) and the
reviewer-assigned report family.

## Development corpus (not bundled)

Seven INSP daily BVD sitreps (No 165 to 171), two INSP weekly profiles (SE41,
SE42), two WHO WER issues (33, 41) and two non-report PDFs.
- All eleven reports routed to the right family; the two other PDFs listed as
  unrecognised with the reason "no configured report family matched".
- 1,635 observations, 1,348 statements, 41 proposed events (one outbreak
  episode, 19 surveillance streams, 21 signals), 256 series, 23 unresolved
  periods (mostly week-41 values restated in SE42), 68 issues.
- The WHO prose figure (9,066 cases, 4,381 deaths as of 11 October) is
  attributed to BVD in the DRC from words in the sentence and proposed for the
  17th epidemic; no INSP figure exists for that date in this corpus.
- Command-line run of the 13 PDFs: about 27 seconds.

## Browser checks (headless Chromium, real corpus, workspace moved after import)

Confirming the BVD event with its links; approving the national cumulative
figure (latest reported claim and latest reviewed value then both show
10,532 for 1 November); rejecting a proposed relation; retagging a statement
with a health zone; recording an issue assessment; timeline, sources and
decision history views. No page errors and no failed requests. Source pages
loaded after the workspace folder was moved.

## Not verified

- Windows launcher run of this package (the setup files are those of 0.4.1).
- The three early sitreps (31 May to 2 June): not available in this project.
- Accuracy of qualitative tags and relation proposals. On real reports the
  "addresses" proposals are noisy (about 80) and need review.
- Expert verification of extracted values.
- Performance beyond the development corpus; the browser receives the whole
  workspace state (about 6.6 MB here).


## Revision 0.5.1 validation, 5 November 2026

54 tests passed and 1 skipped in Python 3.12 on Linux. The optional original
development-corpus test remains skipped. Added checks cover reviewed versus
reported values during pending revisions, the cumulative practice series,
source header removal from the practice excerpt, and the response-note
review gate and withdrawal history. Both JavaScript files passed syntax checks.

Local Chrome checks used an isolated reviewed practice workspace. Confirmed
navigation, the empty overview, reviewed cumulative changes, an English
response note with its source-page link, and editing that note from the
review workspace. These checks did not use or change real outbreak records.

The three early BVD PDFs were available in this project and were tested
for recognition. All three still require manual report identification.
Windows execution and real-report extraction accuracy remain to be verified.


## Revision 0.5.2 validation

The suite passed 55 tests with 1 skipped. A new regression checks that a
brief requires reviewed evidence, contains one A4 PDF page, retains its
evidence digest and preserves the saved figure after a later correction
and database reopen. The Chrome check used fictional practice evidence
and verified the save button and appearance of the saved brief with PDF
and evidence links. A generated PDF was rendered and visually checked.
Windows execution of this revision still needs confirmation.

## Revision 0.5.3 release checks

The Linux regression suite passed 56 tests, with the optional real-report
test skipped. The scripted demo imported four fictional PDFs, completed
simulated event and observation review, and exported 34 approved observations.
The two example briefs were generated from fictional data before and after
a simulated correction. Each has one PDF page and a corresponding evidence
record. These checks do not establish accuracy on held-out real reports.

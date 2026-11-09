# Outbreak Ledger v0.5.1
## Product review and revision notes, 5 November 2026

### What the review found

- The opening view served reviewers and readers at the same time. It showed internal event IDs, candidate status, reporting rules and extraction counts beside epidemiological figures. Some panels used raw source excerpts or machine-generated labels as presentation text.
- The response workflow had rich underlying records, but the reader had to reconstruct the story from statement lists and relationship identifiers. The practice PDF's cover lines also appeared inside extracted response statements. The original report remains necessary evidence, especially for French source wording and mixed statements.
- A browser check found that the practice reports' cumulative snapshots split into separate series because their source reports covered different weeks. The cumulative count is an as-of observation. Its date can advance while the reporting interval changes.
- Reconciliation could drop an earlier reviewed figure when a later stated revision was still awaiting review. Cross-source comparisons matched period endings without requiring matching starts. A persisted supersession could also suppress a claim after its assignment moved to another series.
- The original validation notes said the three early BVD reports were unavailable. They were available in the project for this review. Direct tests on the three files dated 31 May, 1 June and 2 June confirmed that each still needs manual report identification. Their extraction accuracy remains unverified.

### What changed in this package

The English reader view starts with Overview, What changed, Response updates and Reports and sources. Report import, original wording, proposed tags, reconciliation controls and the audit log sit in the Review workspace. The reader view uses confirmed event assignments and approved or corrected figures. It displays dates, area, reporting organisation, classification, reporting series and original page links. Pending periods and disagreements have plain-language notices. It preserves separate source series.

What changed uses the reviewed timeline. Cumulative differences are labelled net changes in reported figures. Individual new counts keep their reporting windows. Readers can expand the reviewed history and open the original evidence. The practice data now compare cumulative snapshots by as-of date. Reconciliation preserves an earlier reviewed value while its stated revision awaits approval and checks that supersessions still connect claims in the same series and period. Cross-source comparisons require both period dates to match.

Reviewers can save or withdraw an English response note after confirming the source statement and its event assignment. Saving appends a review decision. Later retagging, rejection or removal of the event assignment makes that version of the note ineligible for display. The original excerpt remains available in the review workspace. Confirmed relationships between visible notes show how an action addresses a problem, how a later source updates it, or whether a resolution was reported. An English note requires editorial review. The interface has no automatic translation or English verification.

### Where the product differs from document chat

The demonstration should start with two successive reports for the same event. Show the latest reviewed figure, the earlier figure and the source page behind each. Then introduce a correction and a pending claim. The interface should expose the review decision, the period affected and the value still used. A second useful demonstration follows a reported constraint to an action and then to an explicitly reported outcome. This uses the database and versioned reconciliation rules already underlying the application.

The next product investment should be a saved situation brief that records the exact evidence set, review decisions and rule version used to generate it. This would support recreating a previous brief and comparing it with a later one. Before wider use, measure event attribution, numeric accuracy, qualitative note fidelity, and review time on independently annotated reports. Prioritise representative early and late PDF layouts and the reporting gaps that affect actual decisions.

### Validation and limits

The original source files can remain in their original language. Public response prose is entered and checked by a reviewer. Original PDF pages and the analyst workspace retain source wording and technical evidence needed for verification. Existing v0.5 reviews remain valid records. English notes are opt-in editorial decisions, so previous statements do not automatically appear as public summaries. The prior pilot's full historical replay and real-corpus accuracy remain open.

The package remains a local Windows-launchable application. The reader view is a presentation layer inside that application. This review uses practice data for browser testing. It does not establish Windows execution of this particular revision or extraction accuracy for real reports.

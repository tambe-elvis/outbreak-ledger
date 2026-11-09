# Response to REVIEW_2026_11_04.md in version 0.5

1. Narrative attribution. The WHO pattern no longer supplies BVD or DRC.
   Disease and country must be named in the matched text or the text just
   before it; otherwise the claim abstains (disease unknown, meaning
   unknown) and cannot be approved. The evidence used is written into the
   claim's label. Regression test: the Kenya mpox counterexample now gives
   mpox and Kenya; a sentence naming no disease abstains.
2. Disease context in exports and review. Every numeric export row and
   review card shows event, disease, family and full period bounds. The
   observation queue can be filtered by event and searched by disease.
3. Indicator scoping. Indicators are replaced by reconciliation series keyed
   by event, issuer, family, place, level, indicator, classification, count
   type and period kind. Daily and weekly reports from the same issuer no
   longer share a reporting calendar.
4. Issue threads across diseases. Issues require overlapping events, topic
   and place. Identifiers derive from the first member's stable key and are
   persisted; reviewers confirm or unlink members, and links across events
   are explicit.
5. Rejected statements in totals. Excluded from event summaries and issues;
   kept in history. The register now groups by event identity, not disease
   string.
6. Unrecognised early reports. Routing now records why a file failed (text
   layer size, matched heading patterns per family). A reviewer can assign a
   family and metadata; acceptance requires readable extracted content. The
   three early reports were not available in this project and remain
   untested.
7. Tag validation and audit. Types and permitted values are validated before
   writing; disease and health-zone corrections are exposed; the decision
   history shows numeric, statement and reconciliation decisions, and its
   export is always available.
8. Integrity. All three exports check source hashes and record policy and
   configuration fingerprint.
9. Capacity claims. Unchanged interface limits; no claim of tested capacity
   beyond the 13-PDF development corpus.

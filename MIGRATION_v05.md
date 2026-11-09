# Moving from 0.4 or 0.4.1 to 0.5

1. Keep your current folder (for example D:\OL) unchanged. It remains a
   record of your 0.4 work.
2. Extract the 0.5 package to a new short folder, D:\OL5, and start
   START_OUTBREAK_LEDGER.bat there. The Python runtime that 0.4.1 installed
   under Local AppData is reused, because the requirements are unchanged.
3. Upload the same PDFs again into "Outbreak workspace (automatic
   recognition)". Identical files give identical source hashes and evidence
   keys, whatever order you upload them in.
4. In Events and issues, check each proposed event and confirm it (with its
   proposed evidence links) or edit, merge or separate it first.
5. Repeat the observation and statement reviews you need. 0.4 review records
   are not imported: in 0.5 a value can only be approved under a confirmed
   event, so earlier approvals would lack that check.

Why no automatic conversion: 0.4 databases have no event identities, no
stable evidence keys and no relation records. Converting them would attach
past approvals to events nobody confirmed.

Backups: stop the launcher, then copy the whole local_data folder. A 0.5
workspace can be moved; source PDFs are located by their hash.

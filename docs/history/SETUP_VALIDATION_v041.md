# Setup revision 0.4.1 — validation

Prepared 4 November 2026 for the reported Windows path-length failure.

- Full existing application suite: 40 passed, 1 skipped on Python 3.12/Linux. The optional original real-corpus test is skipped because that corpus folder is unavailable.
- New Python bootstrap compiled successfully.
- Both Windows launchers were checked for duplicate/missing goto targets.
- Bootstrap dependency check and sample-PDF rendering passed using the verified Linux environment.
- Browser JavaScript syntax check passed.
- Analytical application source matches the reviewed v0.4 application. No analytical defects are claimed fixed by this setup revision.
- Windows 64-bit PDFium 5.14.0 wheel was inspected during diagnosis: it contains pypdfium2_raw bindings and pdfium.dll.
- Windows execution, creation of a Windows Local AppData runtime and first-install behavior have not been executed in this Linux environment.

## Behavior

The batch launcher prefers Python 3.12 and invokes launch_ledger.py. The bootstrap derives a runtime directory from the Python installation and requirements. On Windows this directory is under Local AppData/OutbreakLedger, outside the application checkout. An import/render check must pass before a setup marker is written. Subsequent starts repeat those checks, then reuse the environment. A failed check triggers a fresh package reinstall into that runtime without deleting the old project environments. Errors are recorded in startup_log.txt.

Both application and runtime path checks stop with an explicit message if paths still approach the Windows limit. No registry changes or administrator privileges are requested by these scripts. Successful installation still depends on the user's Python installation, filesystem permissions, network access and Windows configuration.

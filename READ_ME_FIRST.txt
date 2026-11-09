OUTBREAK LEDGER 0.5.3 - COMPLETE PACKAGE (WINDOWS SETUP OF REVISION 0.4.1)

1. Right-click this ZIP and choose Extract All.
2. Extract to a short path, such as C:\OL53 or D:\OL53.
3. Open the extracted folder containing START_OUTBREAK_LEDGER.bat and double-click it.
4. First setup requires internet. Progress stays visible in the window.
5. Wait for "PDF rendering check passed" and for your browser to open.
6. Keep the launcher window open while working.

If the browser does not open, visit http://127.0.0.1:8765.
For later sessions, double-click START_OUTBREAK_LEDGER.bat again.
No separate repair file or Python activation is needed.

WINDOWS PATH-LENGTH FIX
The Python runtime is now installed under your Windows Local AppData folder,
away from the long project path that caused PDFium installation to fail.
The launcher checks imports and renders a sample PDF before opening the app.
A successful setup is reused. Failed setup is recorded in startup_log.txt.
The old .venv and .venv_pdfium_repair are neither used nor deleted.
Do not copy either old environment into this package.

DATA AND MIGRATION FROM v0.4
New uploads and decisions are saved in local_data beside the application.
v0.4 workspaces are not converted: v0.5 links evidence to events through new
stable keys. Upload the same PDFs again into the v0.5 outbreak workspace and
repeat the reviews you need. See MIGRATION_v05.md. The v0.4 folder can stay
as a read-only record. A v0.5 workspace can be moved or backed up as a whole
local_data folder; source PDFs are found by their hash.

SCOPE
Version 0.5 adds persistent event identities, temporal reconciliation of
figures, reviewed relationships between statements, and the fixes for the
review findings in docs/history/.
The Windows setup approach is unchanged from 0.4.1. If your 0.4.1 setup
worked, the saved Python runtime is reused because requirements did not change.

CHECKS
Double-click CHECK_INSTALLATION.bat to run the regression suite.
The application tests and PDF rendering are checked on Linux. Windows batch
execution still needs confirmation on your laptop.

IF STARTUP FAILS
Send startup_log.txt from the OL folder and the final visible error.

READER DASHBOARD
Open README_PUBLIC_VIEW.txt for the public-facing workflow. See
docs/history/REVIEW_PUBLIC_PRESENTATION_v051.md for the earlier product review.

"""Outbreak Ledger 0.5 launcher (setup approach of revision 0.4.1, unchanged)."""
from pathlib import Path
import argparse, hashlib, json, os, subprocess, sys, traceback
from datetime import datetime
from outbreak_ledger import __version__
ROOT=Path(__file__).resolve().parent
LOG=ROOT/'startup_log.txt'

def log(text):
    print(text,flush=True)
    with LOG.open('a',encoding='utf-8') as f:f.write(text+'\n')

def run(command):
    env=dict(os.environ,PYTHONUTF8='1',PYTHONNOUSERSITE='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
    with subprocess.Popen([str(c) for c in command],cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace') as process:
        for line in process.stdout:log(line.rstrip('\n'))
        code=process.wait()
    if code:raise RuntimeError(f'Command failed with exit code {code}')

def runtime_path(requirements):
    key=hashlib.sha256(requirements+sys.version.encode()+str(sys._base_executable).encode()).hexdigest()[:12]
    if os.name=='nt':
        local=os.environ.get('LOCALAPPDATA')
        if not local:raise RuntimeError('LOCALAPPDATA is unavailable. Start from a normal Windows user session.')
        base=Path(local)/'OutbreakLedger'/('py'+str(sys.version_info.major)+str(sys.version_info.minor)+'-'+key)
        licence=base/'Lib/site-packages/pypdfium2-5.14.0.dist-info/licenses/data/windows_x64/BUILD_LICENSES'
        if len(str(licence))>=240:raise RuntimeError('Runtime path is too long. Send startup_log.txt; installation has not started.')
    else:base=ROOT/'.runtime'/key
    return base

def check_runtime(py):
    run([py,'-I','-c','import sys,pdfplumber,pypdfium2,pypdfium2_raw,yaml,reportlab; print("Required modules loaded successfully"); print("Python:",sys.executable)'])
    run([py,'-I','-c','import sys,pypdfium2 as pdf; d=pdf.PdfDocument(sys.argv[1]); p=d[0]; b=p.render(scale=0.2); b.close(); p.close(); d.close(); print("PDF rendering check passed")',ROOT/'samples/reports/msp_demo_sitrep_10_fr.pdf'])

def ensure_runtime(runtime,requirements):
    py=runtime/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    marker=runtime/'ledger_setup_ready.json'
    if marker.exists() and py.exists():
        try:check_runtime(py);return py
        except Exception as exc:log('Saved runtime check failed; reinstalling: '+str(exc))
    if not py.exists():
        runtime.parent.mkdir(parents=True,exist_ok=True)
        log('Creating a separate runtime: '+str(runtime))
        run([sys.executable,'-I','-m','venv',runtime])
    log('Installing requirements from PyPI. Internet is required for this setup.')
    run([py,'-m','pip','install','--index-url','https://pypi.org/simple','--no-cache-dir','--force-reinstall','--only-binary=pypdfium2','-r',ROOT/'requirements.txt'])
    check_runtime(py)
    marker.write_text(json.dumps({'setup_revision':'0.5.0','requirements_sha256':hashlib.sha256(requirements).hexdigest(),'checked_at':datetime.now().isoformat()}),encoding='utf-8')
    return py

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');args=parser.parse_args()
    try:
        LOG.write_text(f'Outbreak Ledger {__version__} (setup revision 0.4.1 approach)\n'+datetime.now().isoformat()+'\n',encoding='utf-8')
        log('Application: '+str(ROOT))
        if sys.version_info<(3,10):raise RuntimeError('Python 3.10 or newer required; Python 3.12 recommended.')
        for name in ('requirements.txt','outbreak_ledger/web.py','config/qualitative_common.yaml','samples/reports/msp_demo_sitrep_10_fr.pdf'):
            if not (ROOT/name).is_file():raise RuntimeError('Missing '+name+'. Extract the complete ZIP before launching.')
        if os.name=='nt' and len(str(ROOT/'outbreak_ledger/web_assets/index.html'))>=240:raise RuntimeError('Application path is too long. Extract to a short path such as C:\\OL53 and launch again.')
        requirements=(ROOT/'requirements.txt').read_bytes();runtime=runtime_path(requirements);log('Runtime: '+str(runtime))
        py=ensure_runtime(runtime,requirements)
        if args.check:run([py,'-m','pytest','-q']);return 0
        log('Starting Outbreak Ledger: http://127.0.0.1:8765')
        log('Keep this window open. Press Ctrl+C to stop. Records: '+str(ROOT/'local_data'))
        env=dict(os.environ,PYTHONUTF8='1',PYTHONNOUSERSITE='1')
        return subprocess.call([str(py),'-m','outbreak_ledger.web','--open'],cwd=str(ROOT),env=env)
    except KeyboardInterrupt:return 0
    except Exception:
        try:log(traceback.format_exc())
        except OSError:traceback.print_exc()
        print('Startup failed. Send startup_log.txt from this application folder.',flush=True)
        return 1
if __name__=='__main__':raise SystemExit(main())

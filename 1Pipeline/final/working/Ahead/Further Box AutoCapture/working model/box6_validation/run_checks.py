"""Repeatable syntax/regression check with an auditable local report."""
import contextlib
import hashlib
import json
from pathlib import Path
import py_compile
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))


def main():
    files = [ROOT/name for name in ('pipeline_cli_box6.py', 'box6_audio.py',
        'box6_capture.py','box6_runtime.py','box6_translation.py','piweb_cli3.py')]
    files += sorted(HERE.glob('*.py'))
    started = time.monotonic()
    for path in files: py_compile.compile(str(path),doraise=True)
    with (HERE/'regression_checks.log').open('w',encoding='utf-8') as log, \
         contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        suite = unittest.defaultTestLoader.discover(str(HERE),pattern='test_*.py')
        result = unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    report = dict(tests=result.testsRun, failures=len(result.failures),
        errors=len(result.errors), skipped=len(result.skipped), passed=result.wasSuccessful(),
        syntax_files=len(files), elapsed_seconds=time.monotonic()-started,
        executable=sys.executable, hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest().upper()
            for path in files[:6]+[ROOT/'pipeline_cli_box5.py',ROOT/'piweb_cli2.py']})
    (HERE/'regression_checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
    if not result.wasSuccessful(): raise SystemExit(1)


if __name__ == '__main__': main()

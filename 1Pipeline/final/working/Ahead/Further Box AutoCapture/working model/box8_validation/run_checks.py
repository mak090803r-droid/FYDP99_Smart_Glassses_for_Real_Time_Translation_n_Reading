"""Regression runner; old files/logs are read-only, reports stay in Box8."""
import contextlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];OUT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(OUT),str(ROOT/'box6_validation')]


def main():
    import pipeline_cli_box8 as candidate
    import test_host,test_translation,test_audio_transport,test_box8
    import box7_validation.test_box7 as old_voice
    from box8_voice import VoiceService
    missing=ROOT/'paragraph_test_outputs/run_001_20260903_134627_ocr.txt'
    archived=ROOT/'box6_validation/end_to_end_outputs/capture_cli_001_20260908_143651_box6.json'
    data=json.loads(archived.read_text(encoding='utf-8'))
    equivalent='\n'.join('  SOURCE : '+r['source_text'] for r in data['regions'])
    original=Path.read_text
    def read(path,*a,**kw):return equivalent if path==missing and not path.exists() else original(path,*a,**kw)
    reports=[]
    with patch.object(Path,'read_text',read), (OUT/'unit_checks.log').open('w',encoding='utf-8') as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        suites=[('box6_existing',[test_host,test_translation,test_audio_transport]),('box7_existing',[old_voice]),('box8_new',[test_box8]),('box8_host',[test_host])]
        for name,modules in suites:
            if name=='box8_host':test_host.host=candidate
            suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(m) for m in modules)
            result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
            reports.append(dict(name=name,tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped)))
        with patch.object(old_voice,'VoiceService',VoiceService):
            suite=unittest.defaultTestLoader.loadTestsFromTestCase(old_voice.ServiceTests)
            result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
            reports.append(dict(name='box8_voice_transport',tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped)))
    (OUT/'unit_checks.json').write_text(json.dumps(dict(suites=reports,fixture_substitution=str(archived)),indent=2))
    print(json.dumps(reports,indent=2));assert not any(r['failures'] or r['errors'] for r in reports)


if __name__=='__main__':main()

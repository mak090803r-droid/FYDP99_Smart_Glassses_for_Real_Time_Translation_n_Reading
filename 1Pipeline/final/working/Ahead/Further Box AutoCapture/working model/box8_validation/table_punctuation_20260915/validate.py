"""Reproduce and validate the suspicious Page 2 percentage cell."""
import base64,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import cv2
import pipeline_cli_box8 as host
from box8_jobs import Job

source=json.loads((ROOT/'paragraph_test_outputs/capture_cli_006_20260915_120623_box8.json').read_text(encoding='utf-8'))
engine=host.load_ocr_engine('english')
_,_,image,_=host.run_ocr(engine,source['image'],False,None)
with host.OCR_LOCK:raw=list(engine.predict(image))
words=host._extract_ocr_lines(raw,image.shape)
ok,encoded=cv2.imencode('.png',image);assert ok
source_ok,source_encoded=cv2.imencode('.png',cv2.imread(source['image']));assert source_ok
request=dict(op='tables',image=base64.b64encode(encoded).decode(),original_image=base64.b64encode(source_encoded).decode(),words=words)
started=time.perf_counter();job=Job(ROOT,request).start()
assert job.done.wait(150),'table worker timeout';assert not job.error,job.error
report=dict(source_image=source['image'],wall_seconds=round(time.perf_counter()-started,3),worker_seconds=job.seconds,tables=job.result)
(OUT/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
assert len(job.result)==1,job.result
table=job.result[0];assert table['safe'],table['warnings']
values=[row[1]['text'] for row in table['rows']]
assert values==['97.4%','93.1%','88.7%','81.9%'],values
retry=next(r for r in table['cell_ocr_retries'] if r['original_ocr']=='93 1%')
assert retry['final_value']=='93.1%' and 'accepted' in retry['reason'],retry
print(json.dumps(dict(values=values,retry=retry,safe=table['safe'],warnings=table['warnings'],wall_seconds=report['wall_seconds']),indent=2))

import sys,json,base64,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));OUT=Path(__file__).resolve().parent
from box8_jobs import Job
results=[]
for p in sorted(OUT.glob('capture*.json')):
    d=json.loads(p.read_text(encoding='utf-8'));start=time.perf_counter()
    job=Job(ROOT,dict(op='tables',image=base64.b64encode(p.with_suffix('.png').read_bytes()).decode(),words=d['words'])).start()
    if not job.done.wait(150):job.cancel();raise TimeoutError(p.name)
    r=dict(image=p.name,seconds=time.perf_counter()-start,error=job.error,tables=job.result)
    results.append(r)
    (OUT/'model_results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(dict(image=p.name,seconds=r['seconds'],error=job.error,tables=[dict(headers=t['headers'],rows=[[c['text'] for c in row] for row in t['rows']],safe=t['safe'],warnings=t['warnings']) for t in job.result or []])),flush=True)

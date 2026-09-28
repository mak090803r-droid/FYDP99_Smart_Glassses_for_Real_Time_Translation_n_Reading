import sys,json,base64,io,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));OUT=Path(__file__).resolve().parent
from PIL import Image
from box8_jobs import Job
import psutil
result=[]
page=json.loads((ROOT/'paragraph_test_outputs/capture_cli_003_20260914_143821_box8.json').read_text(encoding='utf-8'))
for name,image in [('blank',Image.new('RGB',(1000,800),'white')),('real_prose',Image.open(page['image']).convert('RGB'))]:
    buffer=io.BytesIO();image.save(buffer,format='PNG');j=Job(ROOT,dict(op='tables',image=base64.b64encode(buffer.getvalue()).decode(),words=[]))
    j.pause(True);j.start();deadline=time.monotonic()+10
    while j.process is None and time.monotonic()<deadline:time.sleep(.02)
    p=psutil.Process(j.process.pid);a=p.cpu_times();time.sleep(.3);b=p.cpu_times();delta=b.user+b.system-a.user-a.system
    j.pause(False);assert j.done.wait(120);assert not j.error,j.error
    r=dict(name=name,tables=j.result,suspended_cpu_seconds=delta,seconds=j.seconds);result.append(r);print(json.dumps(r),flush=True)
(OUT/'negative_results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
assert all(not r['tables'] and r['suspended_cpu_seconds']<.03 for r in result)

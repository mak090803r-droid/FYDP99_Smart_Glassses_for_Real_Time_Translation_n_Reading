"""Verify an actual optional process can yield CPU and resume correctly."""
from pathlib import Path
import sys
import time
import json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from box8_jobs import Job

if __name__=='__main__':
    import psutil
    request=dict(op='qa',question='What voltage is required?',sources=[dict(text='The camera requires 5 V.',region_id=1)])
    job=Job(ROOT,request)
    job.pause(True);job.start()
    deadline=time.monotonic()+10
    while job.process is None and not job.done.is_set() and time.monotonic()<deadline:time.sleep(.02)
    assert job.process is not None
    process=psutil.Process(job.process.pid)
    before=process.cpu_times();time.sleep(.3);after=process.cpu_times()
    delta=(after.user+after.system)-(before.user+before.system)
    assert delta<.03,delta
    assert not job.done.is_set()
    job.pause(False)
    assert job.done.wait(60)
    assert not job.error,job.error
    assert job.result['answer']=='5 V',job.result
    report=dict(suspended_cpu_seconds=delta,resumed_answer=job.result['answer'],passed=True)
    (Path(__file__).parent/'scheduling_checks.json').write_text(json.dumps(report,indent=2))
    print(report)

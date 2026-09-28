"""Bounded cancellable subprocesses. No model imports in the host."""
import json
import os
import sys
import subprocess
import threading
from pathlib import Path


class Job:
    def __init__(self,root,request):
        self.done=threading.Event();self.cancelled=threading.Event()
        self.result=None;self.error=None;self.seconds=None;self.process=None
        self.root=Path(root);self.request=request
        self._process_lock=threading.Lock();self._paused=False
    def start(self):
        threading.Thread(target=self._run,daemon=True,name='box8-optional-worker').start()
        return self
    def _run(self):
        try:
            if self.cancelled.is_set():return
            flags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0
            with self._process_lock:
                process=subprocess.Popen([sys.executable,'-u',str(self.root/'box8_worker.py')],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
                    encoding='utf-8',creationflags=flags)
                self.process=process
                if self._paused:
                    import psutil
                    psutil.Process(process.pid).suspend()
            if self.cancelled.is_set():process.terminate()
            stdout,stderr=process.communicate(json.dumps(self.request),timeout=180)
            if self.cancelled.is_set():return
            lines=[line for line in stdout.splitlines() if line.startswith('{')]
            if not lines:raise RuntimeError(stderr[-1500:] or 'Optional worker exited without a result')
            response=json.loads(lines[-1])
            if response.get('error'):raise RuntimeError(response['error'])
            self.result=response['result'];self.seconds=response['seconds']
        except Exception as exc:
            if not self.cancelled.is_set():self.error=str(exc)
        finally:
            try:
                if self.process and self.process.poll() is None:
                    self.process.kill();self.process.wait(timeout=3)
            finally:self.done.set()
    def cancel(self):
        self.cancelled.set()
        process=self.process
        if process and process.poll() is None:
            try:process.kill()
            except OSError:pass
    def pause(self,paused):
        with self._process_lock:
            if self._paused==paused:return
            self._paused=paused
            if self.process and self.process.poll() is None:
                import psutil
                try:
                    process=psutil.Process(self.process.pid)
                    process.suspend() if paused else process.resume()
                except psutil.NoSuchProcess:pass


def check_assets(root,kind):
    names=['qa'] if kind=='qa' else ['table_detection','table_structure']
    for name in names:
        path=Path(root)/'box8_models'/name
        if not (path/'config.json').is_file() or not any((path/f).is_file() for f in ('model.safetensors','pytorch_model.bin')):
            raise RuntimeError('Box8 model missing: '+name+'. Run setup_box8_models.py explicitly.')

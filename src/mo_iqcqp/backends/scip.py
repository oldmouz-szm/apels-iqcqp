"""Process-isolated SCIP task backend with the NativeSession worker contract."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

from .native import NativeSession, ROOT


class ScipSession(NativeSession):
    def __init__(self, deadline=float('inf'), executable=None):
        self.deadline=deadline
        cache=ROOT/'data/cache';cache.mkdir(parents=True,exist_ok=True)
        self.diagnostics=tempfile.TemporaryFile(dir=cache)
        python=executable or os.environ.get('MO_IQCQP_SCIP_PYTHON') or sys.executable
        env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        from mo_iqcqp.experiment.lifecycle import child_environment
        env=child_environment(env)
        self.process=subprocess.Popen([str(python),'-m','mo_iqcqp.backends.scip_worker'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.diagnostics,
            start_new_session=True,bufsize=0,env=env)
        self.buffer=b'';self.selector=selectors.DefaultSelector();self.selector.register(self.process.stdout,selectors.EVENT_READ)
        self.model=None;self.task=None

    def arm_deadline(self):
        if time.monotonic()>=self.deadline:raise TimeoutError('SCIP global deadline')

    def _send(self,command,**data):
        self.arm_deadline()
        return self.request(json.dumps(dict(command=command,deadline=min(self.deadline,time.monotonic()+3600),**data),separators=(',',':'),allow_nan=False),self.deadline)

    def load_model(self,model,input_limits=None):
        if self.model is not None:raise ValueError('model already loaded')
        source=model.source['path']
        from mo_iqcqp.experiment.resources import input_limits as checked_limits
        result=self._send('LOAD',path=source,sha256=model.source['sha256'],input_limits=checked_limits(input_limits))
        self.model=model
        return result

    def set_seed(self,seed):return self._send('SEED',seed=seed)
    def set_structure(self,structure):raise ValueError('SCIP structure enhanced task unavailable')
    def set_task(self,weights,eps=None):
        eps=eps or [None]*len(weights)
        if len(weights)!=len(self.model.objectives) or len(eps)!=len(weights):raise ValueError('task dimension')
        task=(tuple(weights),tuple(eps))
        if task==self.task:return {'status':'TASK_CONTINUED'}
        result=self._send('TASK',weights=weights,eps=[str(e) if e is not None else None for e in eps])
        self.task=task
        return result
    def warm_start(self,x):return self._send('WARM',x=x)
    def run_slice(self,seconds,steps=2**31-1,*,stop_on_feasible=False):
        seconds=max(0,min(seconds,self.deadline-time.monotonic()))
        return self._send('SLICE',seconds=seconds,stop_on_feasible=stop_on_feasible)
    def collect_candidates(self):return self._send('COLLECT')['x']
    def neighbors(self,seconds,limit=64,offset=0):return self._send('NEIGHBORS',seconds=seconds,limit=limit,offset=offset)
    def get_statistics(self):return self._send('STATS')

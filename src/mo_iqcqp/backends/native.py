"""One process-isolated persistent native session; no per-slice LP serialization."""
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import tempfile
from mo_iqcqp.experiment.resources import input_limits as checked_input_limits

ROOT=Path(__file__).resolve().parents[3]
def num(value):
    try:converted=float(value)
    except (OverflowError,ValueError) as exc:
        raise ValueError('Native protocol coordinate is outside finite floating range') from exc
    if not math.isfinite(converted):
        raise ValueError('Native protocol coordinate must be finite')
    return format(converted,'.18g')

class NativeSession:
    def __init__(self, deadline=float('inf'), executable=None):
        self.deadline=deadline
        cache=ROOT/'data/cache';cache.mkdir(parents=True,exist_ok=True)
        self.diagnostics=tempfile.TemporaryFile(dir=cache)
        from mo_iqcqp.experiment.lifecycle import child_environment
        self.process=subprocess.Popen([str(executable or os.environ.get('MO_IQCQP_NATIVE') or ROOT/'build/ls_worker')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.diagnostics,start_new_session=True,bufsize=0,env=child_environment())
        self.buffer=b'';self.selector=selectors.DefaultSelector();self.selector.register(self.process.stdout,selectors.EVENT_READ)
        self.model=None;self.task=None

    def request(self, text, deadline=None):
        # A large model load/rebuild has the run's global budget, not an
        # unrelated ten-second request timeout. The one-hour bound protects
        # standalone sessions created without a finite experiment deadline.
        deadline=min(self.deadline,deadline if deadline is not None else self.deadline,
                     time.monotonic()+3600)
        if time.monotonic()>=deadline:raise TimeoutError('native request deadline')
        # Input can be large: nonblocking writes share the same deadline/watchdog.
        fd=self.process.stdin.fileno();os.set_blocking(fd,False);data=(text+'\n').encode();offset=0
        with selectors.DefaultSelector() as writer:
            writer.register(fd,selectors.EVENT_WRITE)
            while offset<len(data):
                left=deadline-time.monotonic()
                if left<=0:raise TimeoutError('native write deadline')
                if writer.select(left):
                    try:offset+=os.write(fd,data[offset:offset+65536])
                    except BlockingIOError:pass
        while b'\n' not in self.buffer:
            left=deadline-time.monotonic()
            if left<=0:raise TimeoutError('native read deadline')
            if not self.selector.select(left):raise TimeoutError('native watchdog')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:
                self.diagnostics.seek(0)
                raise RuntimeError('NATIVE_EXIT: '+self.diagnostics.read(4096).decode(errors='replace'))
            self.buffer+=chunk
        line,self.buffer=self.buffer.split(b'\n',1)
        try:result=json.loads(line)
        except json.JSONDecodeError:raise RuntimeError('NATIVE_PROTOCOL: '+line.decode(errors='replace'))
        if result['status']=='ERROR':
            if result['error'] in ('GLOBAL_DEADLINE','SLICE_DEADLINE'):
                raise TimeoutError(result['error'])
            raise RuntimeError(result['error'])
        return result

    def arm_deadline(self):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('native global deadline')
        if remaining==float('inf'):remaining=3600
        return self.request(f'GUARD {remaining:.17g}',self.deadline)

    @staticmethod
    def expression(expr,deadline=float('inf')):
        out=[str(len(expr.terms))]
        for i,(key,a) in enumerate(sorted(expr.terms.items())):
            if i % 128 == 0 and time.monotonic()>=deadline:raise TimeoutError('native serialization deadline')
            out.append(f'{key[0] if key else -1} {key[1] if len(key)>1 else -1} {num(a)}')
        return '\n'.join(out)

    def load_model(self, model, input_limits=None):
        if self.model is not None:raise ValueError('model already loaded')
        caps=checked_input_limits(input_limits)
        expressions=list(model.objectives)+[c.expr for c in model.constraints]
        if (len(model.variables)>caps['max_variables'] or len(model.constraints)>caps['max_constraints']
            or sum(len(e.terms) for e in expressions)>caps['max_terms']
            or any(len(e.terms)>caps['max_expression_terms'] for e in expressions)):
            raise ValueError('RESOURCE_LIMIT: native model exceeds configured input limits')
        self.arm_deadline()
        self.model=model
        out=[f'LOAD {len(model.variables)} {len(model.objectives)} {len(model.constraints)} '
             f'{caps["max_variables"]} {caps["max_constraints"]} {caps["max_terms"]} {caps["max_expression_terms"]}']
        for v in model.variables:out.append(f'{int(v.kind=="B")} {int(v.lower is not None)} {num(v.lower or 0)} {int(v.upper is not None)} {num(v.upper or 0)}')
        for e,d in zip(model.objectives,model.directions):out.append(self.expression(e.scaled(1 if d=='min' else -1,self.deadline),self.deadline))
        for c in model.constraints:out.append(f'{c.sense[0]} {num(c.rhs)}\n'+self.expression(c.expr,self.deadline))
        return self.request('\n'.join(out),self.deadline)

    def set_seed(self,seed):return self.request('SEED '+str(seed))
    def set_structure(self,structure):
        self.arm_deadline()
        out=[f'STRUCTURE {len(structure["matrix"])} {len(structure["lifts"])}']
        out.extend(' '.join(map(str,row)) for row in structure['matrix'])
        for j,e in structure['lifts'].items():out.append(str(j)+'\n'+self.expression(e,self.deadline))
        return self.request('\n'.join(out),self.deadline)
    def set_task(self,weights,eps=None):
        eps=eps or [None]*len(weights)
        if len(weights)!=len(self.model.objectives) or len(eps)!=len(weights):raise ValueError('task dimension')
        task=(tuple(weights),tuple(eps))
        if self.task==task:return {'status':'TASK_CONTINUED'}
        self.arm_deadline()
        r=self.request('TASK '+' '.join(num(w) for w in weights)+' '+' '.join(f'{int(e is not None)} {num(e or 0)}' for e in eps),self.deadline)
        self.task=task;return r
    def warm_start(self,x):
        if len(x)!=len(self.model.variables):raise ValueError('warm start dimension')
        self.arm_deadline()
        return self.request('WARM '+' '.join(num(y) for y in x),self.deadline)
    def run_slice(self,seconds,steps=2**31-1,*,stop_on_feasible=False):
        self.arm_deadline()
        seconds=max(0,min(seconds,self.deadline-time.monotonic()))
        command='BOOTSTRAP_SLICE' if stop_on_feasible else 'SLICE'
        return self.request(f'{command} {seconds:.9g} {steps}',self.deadline)
    def collect_candidates(self):return self.request('COLLECT')['x']
    def neighbors(self,seconds,limit=64,offset=0):
        self.arm_deadline()
        return self.request(f'NEIGHBORS {seconds} {limit} {offset}',self.deadline)
    def get_statistics(self):return self.request('STATS')
    def debug_move(self,i,delta):return self.request(f'MOVE {i} {num(delta)}')
    def close(self):
        p=self.process
        if p.poll() is None and time.monotonic()+.15<self.deadline:
            try:
                self.request('CLOSE',time.monotonic()+.1);p.wait(timeout=.1)
            except (TimeoutError,RuntimeError,subprocess.TimeoutExpired,BrokenPipeError):pass
        if p.poll() is None:
            # Signal only our newly-created session/process group, never broad pkill.
            try:os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:p.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                try:os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                p.wait()
        self.selector.close()
        self.diagnostics.seek(0);diagnostic=self.diagnostics.read(65536).decode(errors='replace')
        for stream in (p.stdin,p.stdout,self.diagnostics):stream.close()
        if any(x in diagnostic for x in ['ERROR: AddressSanitizer','runtime error:','LeakSanitizer']):raise RuntimeError('SANITIZER: '+diagnostic[:6000])
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

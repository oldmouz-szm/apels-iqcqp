"""Bounded asynchronous I/O around independent persistent C++ processes.

There is exactly one outstanding operation per session. Python I/O threads never
share a mutable LS instance; all archive mutations happen on the coordinator.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time
import os
from mo_iqcqp.backends.native import NativeSession


class WorkerPool:
    def __init__(self, model, count, deadline, seed, structure=None, initial=None, input_limits=None,backend='ls_iqcqp'):
        self.deadline = deadline
        self.sessions = []
        self.pending = {}
        self.previous = {}
        self.serial = 0
        self.executor = None
        self.closed = False
        self.records = []
        self.idle_since = {}
        try:
            for worker in range(count):
                if backend=='scip':
                    from mo_iqcqp.backends.scip import ScipSession
                    session=ScipSession(deadline)
                elif backend=='ls_iqcqp':session=NativeSession(deadline)
                else:raise ValueError('Unknown search backend')
                self.sessions.append(session)
                if input_limits is None:session.load_model(model)
                else:session.load_model(model,input_limits=input_limits)
                worker_seed = (seed + worker * 1000003) % (2**64)
                session.set_seed(worker_seed)
                if structure:
                    session.set_structure(structure)
                if initial is not None:
                    session.warm_start(initial)
                self.records.append(dict(worker=worker, pid=session.process.pid,
                                         seed=worker_seed, model_loads=1, tasks=0, steps=0,
                                         affinity=sorted(os.sched_getaffinity(session.process.pid)),
                                         idle_wait_wall=0.))
                self.idle_since[worker]=time.monotonic()
            self.executor = ThreadPoolExecutor(max_workers=count, thread_name_prefix='native-io')
        except BaseException:
            self.close()
            raise

    @property
    def idle(self):
        return [i for i in range(len(self.sessions)) if i not in self.pending]

    def pending_counts(self):
        if self.closed:return {}
        counts = {}
        for item in self.pending.values():
            kind = item['task']['kind']
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    def reservations(self):
        result = {}
        for item in self.pending.values():
            k = item['task']['kind']
            result[k] = result.get(k, 0.) + item['reservation']
        return result

    def submit(self, worker, task, seconds, offset=0, steps=2**31-1):
        if worker in self.pending or self.closed:
            raise RuntimeError('Worker is not idle')
        self.serial += 1
        self.records[worker]['idle_wait_wall']+=time.monotonic()-self.idle_since[worker]
        task = deepcopy(task)  # Never refer to a live archive/normalization object.
        token = (worker, self.serial, task['scale_version'])
        task.update(worker=worker, task_id=self.serial)
        # A semantically identical task can continue without WARM/resetting tabu/RNG.
        signature = repr({k:v for k,v in task.items() if k not in ('task_id','worker','gap','effective_shares','decision')})
        continued = self.previous.get(worker) == signature
        self.previous[worker] = signature
        future = self.executor.submit(self._execute, worker, task, token, seconds, offset, steps, continued)
        self.pending[worker] = dict(future=future, task=task, token=token, reservation=seconds)
        self.records[worker]['tasks'] += 1
        return token

    def _execute(self, worker, task, token, seconds, offset, steps, continued):
        begin = time.monotonic()
        session = self.sessions[worker]
        result = dict(token=token, task=task, worker=worker, candidates=[], continued=continued,
                      began=begin, error=None, timeout=False)
        try:
            session.set_task(task['weights'], task['eps'])
            if task['seed'] is not None and not continued:
                session.warm_start(task['seed'])
            result['setup_elapsed']=time.monotonic()-begin
            search_begin=time.monotonic()
            remaining = min(seconds, max(0., self.deadline-time.monotonic()-.003))
            if remaining <= 0:
                raise TimeoutError('Task setup reached global deadline')
            if task['kind'] == 'pls':
                session.neighbors(remaining, 64, offset)
            else:
                if task.get('bootstrap_strategy')=='persistent_unit_v2' and task.get('bootstrap_reason')=='empty_archive':
                    stats = session.run_slice(remaining, steps, stop_on_feasible=True)
                else:
                    stats = session.run_slice(remaining, steps)
                self.records[worker]['steps'] = stats['steps']
                result['native_slice']=stats
                for key in ('task_requests','warm_requests','reset_count','deadline_recoveries','first_original_feasible_monotonic'):
                    if key in stats:self.records[worker][key]=stats[key]
                if stats.get('first_original_feasible_x') is not None:
                    self.records[worker]['first_original_feasible_x']=stats['first_original_feasible_x']
                if 'slice_steps' in stats:self.records[worker]['zero_step_slices']=self.records[worker].get('zero_step_slices',0)+int(stats['slice_steps']==0)
            result['candidates'] = [
                dict(worker=worker, task_id=token[1], scale_version=token[2], x=x)
                for x in session.collect_candidates()
            ]
            result['search_communication_elapsed']=time.monotonic()-search_begin
        except TimeoutError as exc:
            result.update(timeout=True, error=str(exc))
        except Exception as exc:
            result['error'] = type(exc).__name__ + ': ' + str(exc)
        result['finished'] = time.monotonic()
        result['service_elapsed'] = result['finished']-begin
        result.setdefault('setup_elapsed',time.monotonic()-begin)
        result.setdefault('search_communication_elapsed',0.)
        return result

    def take(self, worker):
        item = self.pending[worker]
        result = item['future'].result()
        if result['token'] != item['token']:
            raise RuntimeError('Mismatched worker/task/version response')
        del self.pending[worker]
        result['consumed']=time.monotonic()
        result['response_wait_elapsed']=max(0.,result['consumed']-result['finished'])
        self.idle_since[worker]=result['consumed']
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        # A request is bounded by the shared deadline. Interrupt active owned
        # processes first, then join I/O, then close streams (no concurrent close).
        for worker, item in self.pending.items():
            p = self.sessions[worker].process
            if not item['future'].done() and p.poll() is None:
                try:
                    p.terminate()
                except ProcessLookupError:
                    pass
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
        errors = []
        for session in self.sessions:
            try:
                session.close()
            except Exception as exc:
                errors.append(str(exc))
        for record in self.records:
            worker=record['worker']
            if worker not in self.pending:
                record['idle_wait_wall']+=time.monotonic()-self.idle_since[worker]
            record['exit_code'] = self.sessions[record['worker']].process.returncode
        if errors:
            raise RuntimeError('; '.join(errors))

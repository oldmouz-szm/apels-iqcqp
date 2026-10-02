import hashlib
import json
import os
from pathlib import Path
import random
import time
from mo_iqcqp.archive import Archive
from mo_iqcqp.backends.pool import WorkerPool
from mo_iqcqp.experiment.resources import configuration, plan, tree, rss, available_memory, input_limits, INPUT_DEFAULTS, resource_observation
from concurrent.futures import wait, FIRST_COMPLETED
from mo_iqcqp.io import load_lp
from mo_iqcqp.model import SCHEMA as MODEL_SCHEMA
from mo_iqcqp.search import Scheduler
from mo_iqcqp.experiment.events import EventStream, inspect_event_log

RUN_PROTOCOL='end-to-end-cold-manyobj-batch-v9'
QUEUE_IDENTITY_SCHEMA='queue-run-manyobj-batch-v9'
CONFIGURATION_SCHEMA='apels-effective-v2'
DEFAULT_SHARES={'direction':.3,'epsilon':.5,'pls':.2}
ALGORITHM_CONFIG_KEYS={'jobs','workers','core_budget','memory_mib','tree_memory_mib',
                       'work_capacity','rho','shares'}


def canonical_json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def file_sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def implementation_fingerprint():
    """Source/protocol identity plus the actual native executable selected for this run."""
    root=Path(__file__).resolve().parents[3]
    h=hashlib.sha256()
    files=sorted((root/'src').rglob('*.py'))
    files += [root/'native/worker.cpp',root/'scripts/build_native.py',root/'native/upstream.patch']
    for file in files:
        h.update(str(file.relative_to(root)).encode())
        h.update(file.read_bytes())
    native=Path(os.environ.get('MO_IQCQP_NATIVE') or root/'build/ls_worker')
    return {'source_sha256':h.hexdigest(),'native_sha256':file_sha256(native),'controller_sha256':file_sha256(__file__)}


def effective_configuration(config=None):
    from mo_iqcqp.search.adaptive import REWARD_VERSION,R2_REWARD_VERSION
    import math
    raw=configuration(config)
    if not isinstance(raw,dict):raise ValueError('Invalid configuration')
    frozen=raw.get('configuration_schema')==CONFIGURATION_SCHEMA
    mode=raw.get('scheduler_mode','fixed')
    backend=raw.get('search_backend','ls_iqcqp')
    if backend not in ('ls_iqcqp','scip'):raise ValueError('Unknown search_backend')
    if mode not in ('fixed','adaptive','adaptive_r2'):raise ValueError('Unknown scheduler_mode')
    common={'jobs','workers','core_budget','memory_mib','tree_memory_mib','work_capacity','rho',
            'scheduler_mode','hv_spec','r2_spec','search_backend','task_slice_seconds','bootstrap_strategy','feasibility_slice_seconds'}
    mode_keys={'shares'} if mode=='fixed' else {'adaptive','enabled_arms'}
    allowed=common|mode_keys|({'affinity','configuration_schema','hv_spec_sha256','r2_spec_sha256'} if frozen else set())
    if set(raw)-allowed:raise ValueError('Unknown or ambiguous algorithm configuration key')
    limits=plan(raw)
    if 'affinity' in raw and raw['affinity']!=limits['affinity']:
        raise ValueError('Effective affinity changed before dispatch')
    capacity=raw.get('work_capacity',512)
    if type(capacity) is not int or capacity<1:raise ValueError('Invalid work_capacity')
    rho=float(raw.get('rho',.001))
    if not math.isfinite(rho) or rho<0:raise ValueError('Invalid rho')
    spec=raw.get('hv_spec')
    if spec is not None and not isinstance(spec,dict):raise ValueError('hv_spec must be inline fixed content')
    spec_hash=hashlib.sha256(canonical_json(spec).encode()).hexdigest() if spec is not None else None
    r2_spec=raw.get('r2_spec')
    if r2_spec is not None and not isinstance(r2_spec,dict):raise ValueError('r2_spec must be inline fixed content')
    r2_hash=hashlib.sha256(canonical_json(r2_spec).encode()).hexdigest() if r2_spec is not None else None
    if mode=='adaptive_r2' and (r2_spec is None or spec is not None):
        raise ValueError('Adaptive-R2 requires r2_spec and no hv_spec')
    if mode=='adaptive' and r2_spec is not None:
        raise ValueError('Legacy Adaptive requires HV, not R2')
    expected=dict(**limits,work_capacity=capacity,rho=rho,scheduler_mode=mode,
                  hv_spec=spec,hv_spec_sha256=spec_hash,configuration_schema=CONFIGURATION_SCHEMA)
    bootstrap=raw.get('bootstrap_strategy','persistent_unit_v2' if backend=='ls_iqcqp' else 'persistent_unit_v1')
    if bootstrap not in ('persistent_unit_v2','persistent_unit_v1','legacy_v1'):raise ValueError('Invalid bootstrap_strategy')
    if bootstrap=='persistent_unit_v2' and backend!='ls_iqcqp':
        raise ValueError('persistent_unit_v2 requires the LS-IQCQP backend')
    feasibility_slice=float(raw.get('feasibility_slice_seconds',10.))
    if not math.isfinite(feasibility_slice) or feasibility_slice<=0 or feasibility_slice>300:
        raise ValueError('Invalid feasibility_slice_seconds')
    expected.update(bootstrap_strategy=bootstrap,feasibility_slice_seconds=feasibility_slice)
    if backend=='scip' or 'search_backend' in raw:expected['search_backend']=backend
    if 'task_slice_seconds' in raw:
        task_slice=float(raw['task_slice_seconds'])
        if not math.isfinite(task_slice) or task_slice<=0 or task_slice>10:
            raise ValueError('Invalid task_slice_seconds')
        expected['task_slice_seconds']=task_slice
    if mode=='adaptive_r2' or r2_spec is not None:
        expected.update(r2_spec=r2_spec,r2_spec_sha256=r2_hash)
    if mode=='fixed':
        shares=raw.get('shares',DEFAULT_SHARES)
        if not isinstance(shares,dict) or set(shares)!=set(DEFAULT_SHARES):
            raise ValueError('Invalid time shares')
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in shares.values()):
            raise ValueError('Invalid time shares')
        total=sum(shares[k] for k in DEFAULT_SHARES)
        if not math.isfinite(total) or total<=0:raise ValueError('Invalid time shares')
        if frozen and (abs(total-1)>1e-12 or any(type(v) is not float for v in shares.values())):
            raise ValueError('Invalid frozen shares')
        expected['shares']=dict(shares) if frozen else {k:float(shares[k])/total for k in DEFAULT_SHARES}
    else:
        if mode=='adaptive' and spec is None:raise ValueError('Adaptive requires a fixed hv_spec')
        if 'enabled_arms' in raw:
            from mo_iqcqp.search.adaptive import ARMS
            enabled=raw['enabled_arms']
            if (not isinstance(enabled,list) or not enabled or
                any(type(arm) is not str or arm not in ARMS for arm in enabled) or
                len(set(enabled))!=len(enabled) or enabled!=[arm for arm in ARMS if arm in enabled]):
                raise ValueError('enabled_arms must be a nonempty canonical subset of search arms')
            expected['enabled_arms']=list(enabled)
        parameters={'window':30,'reward_version':R2_REWARD_VERSION if mode=='adaptive_r2' else REWARD_VERSION}
        actual=raw.get('adaptive',parameters)
        if actual!=parameters or type(actual.get('window')) is not int:
            raise ValueError('Adaptive requires window=30 and the selected fixed reward version')
        expected['adaptive']=parameters
    if frozen and raw!=expected:raise ValueError('Effective configuration snapshot changed')
    return expected


def queue_run_identity(task,effective,source_sha,implementation,objective_dimension=None):
    allowed={'path','budget','seed','algorithm','variant','algorithm_config'}|set(INPUT_DEFAULTS)
    if set(task)-allowed or 'path' not in task:raise ValueError('Unknown or missing queue task key')
    import math
    budget=task.get('budget',10)
    seed=task.get('seed',1)
    caps=input_limits({key:task[key] for key in INPUT_DEFAULTS if key in task})
    if objective_dimension is None:
        from mo_iqcqp.io.lp import ParseError
        try:objective_dimension=len(load_lp(task['path'],**caps).objectives)
        except ParseError:pass  # Legacy mocked queue tests; a real run still rejects the LP.
    variant=task.get('variant','Generic')
    algorithm=task.get('algorithm','apels')
    if type(budget) not in (int,float) or not math.isfinite(budget) or budget<=0:
        raise ValueError('budget must be finite and positive')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    if variant not in ('Generic','Structure-enhanced') or algorithm!='apels':
        raise ValueError('Unknown variant or algorithm')
    payload=dict(schema=QUEUE_IDENTITY_SCHEMA,protocol=RUN_PROTOCOL,model_schema=MODEL_SCHEMA,
                 source=dict(path=str(Path(task['path']).resolve(strict=True)),sha256=source_sha),
                 implementation=implementation,algorithm=algorithm,variant=variant,
                 budget=float(budget),seed=seed,input_limits=caps,
                 effective_configuration=effective,objective_dimension=objective_dimension)
    digest=hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    return dict(digest=digest,payload=payload)


def _identity_matches(result,identity,output):
    if not isinstance(result,dict):return False
    payload=identity['payload']
    matches=(result.get('status')=='COMPLETED'
            and result.get('queue_identity')==identity
            and result.get('algorithm_configuration')==payload['effective_configuration']
            and isinstance(result.get('model_source'),dict)
            and result['model_source'].get('sha256')==payload['source']['sha256']
            and result.get('implementation_fingerprint')==payload['implementation']
            and result.get('protocol')==payload['protocol'])
    if not matches:return False
    from .results import intact,validate_samples
    if not intact(result) or not validate_samples(result,payload['source']['path'],payload['input_limits']):
        return False
    saved=result.get('events_log')
    expected=Path(str(output)+'.events.jsonl').resolve()
    if not isinstance(saved,dict) or not isinstance(saved.get('path'),str) or Path(saved['path']).resolve()!=expected:
        return False
    try:observed=inspect_event_log(expected)
    except (OSError,ValueError):return False
    return bool(observed and observed['complete'] and saved.get('complete')
                and all(observed[k]==saved.get(k) for k in ('records','bytes','sha256')))


def classify(checked,eps):
    checked['task_feasibility']='ORIGINAL_INFEASIBLE' if not checked['valid'] else 'EPSILON_FEASIBLE' if all(e is None or f<=e for e,f in zip(eps,checked['internal'])) else 'ORIGINAL_FEASIBLE_EPSILON_VIOLATED'
    return checked

def atomic_json(path,data):
    from .results import seal
    seal(data)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w') as f:json.dump(data,f,indent=2,default=str,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def require_new_output(output, request_sha256=None):
    """A run owns a whole artifact group; never overwrite a prior attempt."""
    if output is None:return
    occupied=[Path(str(output)+suffix) for suffix in
              ('','.events.jsonl','.checkpoint','.lifecycle','.tmp','.request','.request.tmp','.unverified-final')
              if Path(str(output)+suffix).exists()
              and not (suffix=='.request' and request_sha256 is not None
                       and file_sha256(Path(str(output)+suffix))==request_sha256)]
    if occupied:
        raise FileExistsError(f'Output artifacts already exist: {occupied[0]}. '
                              'Choose a new --output path.')


def atomic_json_new(path,data):
    """Publish a new result atomically without replacing a concurrent result."""
    import tempfile
    from .results import seal
    seal(data)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.result-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as target:
            json.dump(data,target,indent=2,default=str,allow_nan=False)
            target.flush();os.fsync(target.fileno())
        os.link(temporary,path)
    finally:
        os.unlink(temporary)


def admit(model, archive, candidate, task, deadline, start, metrics=None):
    """Only the coordinator validates and admits; old scales are frozen, not stale models."""
    if (candidate['worker'],candidate['task_id'],candidate['scale_version']) != (task['worker'],task['task_id'],task['scale_version']):
        raise RuntimeError('Candidate worker/task/version mismatch')
    if time.monotonic()>=deadline:
        if metrics is not None:metrics['dropped_before_validation']+=1
        return False
    validation_start=time.monotonic()
    if metrics is not None:metrics['validation_started']+=1
    try:checked=classify(model.validate(candidate['x'],deadline=deadline),task['eps'])
    except TimeoutError:
        if metrics is not None:metrics['validation_timed_out']+=1
        raise
    except Exception:
        if metrics is not None:metrics['validation_errors']+=1
        raise
    finally:
        if metrics is not None:metrics['validation_wall']+=time.monotonic()-validation_start
    if metrics is not None:
        metrics['validation_completed']+=1
        if checked['valid'] and checked['task_feasibility']=='ORIGINAL_FEASIBLE_EPSILON_VIOLATED':
            metrics['epsilon_violated_original_feasible']+=1
    checked.update(worker=candidate['worker'],task_id=candidate['task_id'],
                   scale_version=candidate['scale_version'],validated_by_elapsed=time.monotonic()-start)
    if checked['valid'] and hasattr(archive,'normalization_checker'):
        try:archive.normalization_checker(checked['internal'])
        except ValueError:
            if metrics is not None:metrics['normalization_violation_audit']=checked
            raise
    archive_start=time.monotonic()
    reason=archive.insert_with_reason(checked,deadline)
    if metrics is not None:
        metrics['archive_update_wall']+=time.monotonic()-archive_start
        metrics['archive_outcomes'][reason]+=1
    return reason=='ADMITTED'


def run(path,budget=10,seed=1,algorithm='apels',output=None,max_bytes=40_000_000,
        max_terms=500_000,max_variables=100_000,max_constraints=100_000,
        max_expression_terms=500_000,started=None,variant='Generic',algorithm_config=None,
        algorithm_config_json=None,queue_identity_json=None,worker_request_sha256=None):
    import math, resource
    if algorithm!='apels':raise ValueError('Only the apels algorithm is available')
    if not math.isfinite(budget) or budget<=0:raise ValueError('budget must be finite and positive')
    config=effective_configuration(json.loads(algorithm_config_json) if algorithm_config_json is not None else algorithm_config)
    caps=input_limits(dict(max_bytes=max_bytes,max_terms=max_terms,max_variables=max_variables,
                           max_constraints=max_constraints,max_expression_terms=max_expression_terms))
    limits=plan(config)
    queue_identity=json.loads(queue_identity_json) if queue_identity_json is not None else None
    require_new_output(output,worker_request_sha256)
    old_affinity=os.sched_getaffinity(0);old_as=resource.getrlimit(resource.RLIMIT_AS)
    start=started if started is not None else time.monotonic();deadline=start+budget
    archive=Archive();stream=None;pool=None;model=None;scheduler=None;reward=None;first_feasible=None
    run_observation=None;applied_address_space_mib=None
    metrics=dict(candidates_returned=0,batch_distinct_assignments=0,batch_repeat_assignments=0,
        validation_started=0,validation_completed=0,validation_timed_out=0,validation_errors=0,
        dropped_before_validation=0,dropped_on_close=0,
        epsilon_violated_original_feasible=0,archive_outcomes={k:0 for k in
        ('ADMITTED','SAME_OBJECTIVES','DOMINATED','ORIGINAL_INFEASIBLE','DEADLINE')},
        reward_wall=0.,scheduler_wall=0.,feedback_wall=0.,coordinator_batch_wall=0.,
        coordinator_wait_wall=0.,completed_response_wait_wall=0.,
        worker_setup_wall=0.,worker_search_communication_wall=0.,worker_service_wall=0.,
        validation_wall=0.,archive_update_wall=0.,event_write_wall=0.,checkpoint_wall=0.,
        native_submission=dict(slices=0,stop_reasons={},collect_calls=0,duplicate_observations=0,
            observed_original_feasible=0,observed_original_infeasible=0,
            selected_original_feasible=0,selected_repair=0,selection_replacements=0))
    def count_native_slice(response):
        native=response.get('native_slice')
        if not native:return
        summary=metrics['native_submission'];summary['slices']+=1
        observed=native.get('first_original_feasible_monotonic')
        if observed is not None:
            at=observed-start
            if metrics.get('first_native_observation_elapsed') is None or at<metrics['first_native_observation_elapsed']:
                metrics['first_native_observation_elapsed']=at
        reason=native.get('stop_reason','UNKNOWN')
        summary['stop_reasons'][reason]=summary['stop_reasons'].get(reason,0)+1
        for key in ('collect_calls','duplicate_observations','observed_original_feasible',
                    'observed_original_infeasible','selected_original_feasible','selected_repair',
                    'selection_replacements'):
            summary[key]+=native.get(key,0)
    status='COMPLETED';error=None;last_checkpoint=start;stop_reason=None;code_hash=None;impl=None
    # ASan reserves a huge virtual shadow; its explicit diagnostic mode still has RSS watchdog.
    sanitizer='sanitize' in os.environ.get('MO_IQCQP_NATIVE','')
    stream=EventStream(output)
    try:
        os.sched_setaffinity(0,set(limits['affinity']))
        cap=limits['memory_mib']*1024**2
        if not sanitizer:
            if old_as[1]>=0 and cap>old_as[1]:
                raise ValueError('Configured memory_mib exceeds inherited RLIMIT_AS hard cap')
            resource.setrlimit(resource.RLIMIT_AS,(cap,old_as[1]))
        run_observation=resource_observation()
        soft_limit=resource.getrlimit(resource.RLIMIT_AS)[0]
        applied_address_space_mib=None if soft_limit<0 else soft_limit/1024**2
        free=available_memory()
        if free is not None and free<1024**3:raise MemoryError('Less than 1 GiB available; search deferred')
        impl=implementation_fingerprint()
        code_hash=impl['source_sha256']
        if queue_identity:
            expected=queue_identity['payload']
            if (queue_identity['digest']!=hashlib.sha256(canonical_json(expected).encode()).hexdigest()
                or expected['schema']!=QUEUE_IDENTITY_SCHEMA
                or expected['protocol']!=RUN_PROTOCOL or expected['model_schema']!=MODEL_SCHEMA
                or expected['effective_configuration']!=config or expected['input_limits']!=caps
                or expected['implementation']!=impl):
                raise RuntimeError('QUEUE_IDENTITY_MISMATCH before model load')
        archive.capacity=int(config.get('work_capacity',512))
        if archive.capacity<1:raise ValueError('work_capacity must be positive')
        model=load_lp(path,deadline,**caps)
        if len(model.objectives)<2:raise ValueError('Solver requires at least two objectives')
        if queue_identity and queue_identity['payload'].get('objective_dimension') not in (None,len(model.objectives)):
            raise RuntimeError('QUEUE_OBJECTIVE_DIMENSION_CHANGED before search')
        if queue_identity and model.source['sha256']!=queue_identity['payload']['source']['sha256']:
            raise RuntimeError('QUEUE_SOURCE_CHANGED before search')
        from mo_iqcqp.metrics.online import FixedHV
        from mo_iqcqp.metrics.r2 import FixedR2
        if config['hv_spec'] is not None:
            reward=FixedHV(config['hv_spec'],model,deadline)
        if config.get('r2_spec') is not None:
            reward=FixedR2(config['r2_spec'],model,deadline)
        if isinstance(reward,FixedR2):archive.normalization_checker=reward.check_point
        structure=None;initial=None
        if variant=='Structure-enhanced':
            from mo_iqcqp.model.structure import identify,from_permutation
            structure=identify(model)
            if structure is None:raise ValueError('STRUCTURE_NOT_PROVEN: enhanced variant unavailable')
            initial=from_permutation(model,structure,list(range(len(structure['matrix']))))
            initial_task=dict(worker=None,task_id=None,scale_version=0,eps=[None]*len(model.objectives))
            initial_candidate=dict(worker=None,task_id=None,scale_version=0,x=initial)
            initial_start=time.monotonic()
            try:
                inserted=admit(model,archive,initial_candidate,initial_task,deadline,start,metrics)
            finally:
                metrics['initialization_admission_wall']=time.monotonic()-initial_start
            if not inserted:
                if time.monotonic()>=deadline:raise TimeoutError('structured initialization deadline')
                raise ValueError('STRUCTURED_INITIALIZATION_FAILS_OTHER_ORIGINAL_CONSTRAINTS')
            first_feasible=time.monotonic()-start
        from mo_iqcqp.search.adaptive import AdaptiveScheduler
        adaptive=config['scheduler_mode']!='fixed'
        scheduler=(AdaptiveScheduler if adaptive else Scheduler)(len(model.objectives),seed,config=config)
        pool=WorkerPool(model,limits['workers'],deadline,seed,structure,initial,input_limits=caps,
                        backend=config.get('search_backend','ls_iqcqp'))
        from mo_iqcqp.search.bootstrap import PersistentBootstrap
        bootstrap=PersistentBootstrap(len(model.objectives),config['bootstrap_strategy'])
        slice_seconds=config.get('task_slice_seconds',.1 if budget<=10 else .5 if budget<=60 else 1.)
        while time.monotonic()<deadline-.003:
            if rss(tree(os.getpid()))>limits['tree_memory_mib']*1024**2:
                raise MemoryError('Process tree RSS limit')
            for worker in pool.idle:
                if time.monotonic()>=deadline-.003:break
                decision_started=time.monotonic()
                persistent=not archive.points and config['bootstrap_strategy'] in ('persistent_unit_v1','persistent_unit_v2')
                task=(bootstrap.task(worker) if persistent else
                      scheduler.next(archive,reserved=pool.pending_counts() if adaptive else pool.reservations()))
                metrics['scheduler_wall']+=time.monotonic()-decision_started
                if task is not None and not persistent:
                    task['decision']=(scheduler.last_decision if adaptive else
                        dict(eligible=list(scheduler.eligible),pending=pool.pending_counts(),
                             forced_reason=None))
                if task is None:break
                if not persistent and not archive.points and task['seed'] is None and scheduler.turn>1:
                    task['seed']=[]
                    for v in model.variables:
                        lo=math.ceil(v.lower) if v.lower is not None else None
                        hi=math.floor(v.upper) if v.upper is not None else None
                        if lo is not None and hi is not None:y=scheduler.rng.randint(lo,hi)
                        elif lo is not None:y=max(0,lo)+scheduler.rng.randrange(4)
                        elif hi is not None:y=min(0,hi)-scheduler.rng.randrange(4)
                        else:y=scheduler.rng.randrange(-3,4)
                        task['seed'].append(y)
                if time.monotonic()>=deadline-.003:break
                duration=config['feasibility_slice_seconds'] if persistent else slice_seconds
                task['requested_slice_seconds']=duration
                pool.submit(worker,task,min(duration,deadline-time.monotonic()-.003),
                            0 if persistent else scheduler.rng.randrange(2*len(model.variables)))
            if not pool.pending:
                stop_reason='NO_ELIGIBLE_ENABLED_TASK';break
            wait_start=time.monotonic()
            wait([v['future'] for v in pool.pending.values()],
                 timeout=max(0,min(.05,deadline-time.monotonic())),return_when=FIRST_COMPLETED)
            metrics['coordinator_wait_wall']+=time.monotonic()-wait_start
            for worker,item in list(pool.pending.items()):
                if not item['future'].done():continue
                result=pool.take(worker);task=result['task'];validation_start=time.monotonic();accepted=0
                if task['kind']=='feasibility':bootstrap.completions+=1
                candidates=result['candidates'];metrics['candidates_returned']+=len(candidates)
                distinct=len({tuple(c['x']) for c in candidates})
                metrics['batch_distinct_assignments']+=distinct
                metrics['batch_repeat_assignments']+=len(candidates)-distinct
                metrics['completed_response_wait_wall']+=result['response_wait_elapsed']
                metrics['worker_setup_wall']+=result['setup_elapsed']
                metrics['worker_search_communication_wall']+=result['search_communication_elapsed']
                metrics['worker_service_wall']+=result['service_elapsed']
                count_native_slice(result)
                count_keys=('validation_started','validation_completed','validation_timed_out',
                            'validation_errors','dropped_before_validation',
                            'epsilon_violated_original_feasible')
                before={key:metrics[key] for key in count_keys}
                before_outcomes=dict(metrics['archive_outcomes'])
                batch_validation_before=metrics['validation_wall'];batch_archive_before=metrics['archive_update_wall']
                archive_before=len(archive.points);reward_before=None;gain=None
                reward_wall=0.;reward_status='not_enabled';batch_complete=False
                reward_enabled=adaptive and task['kind']!='feasibility'
                try:
                    if reward_enabled:
                        reward_status='incomplete'
                        reward_start=time.monotonic()
                        try:reward_before=reward.value(archive,deadline)
                        except TimeoutError:
                            metrics['dropped_before_validation']+=len(candidates)
                            raise
                        finally:reward_wall+=time.monotonic()-reward_start
                    for index,candidate in enumerate(candidates):
                        if time.monotonic()>=deadline:
                            metrics['dropped_before_validation']+=len(candidates)-index;break
                        try:
                            accepted+=admit(model,archive,candidate,task,deadline,start,metrics)
                            if first_feasible is None and archive.points:
                                first_feasible=time.monotonic()-start
                        except BaseException:
                            # The current candidate is already counted as started; the rest of
                            # this bounded batch never begins validation.
                            metrics['dropped_before_validation']+=len(candidates)-index-1
                            raise
                    batch_complete=(time.monotonic()<deadline and
                                    metrics['dropped_before_validation']==before['dropped_before_validation'])
                    if adaptive and isinstance(reward,FixedR2) and task['kind']=='feasibility' and accepted and batch_complete and not result['error']:
                        reward_start=time.monotonic()
                        try:reward.value(archive,deadline)
                        finally:reward_wall+=time.monotonic()-reward_start
                    if reward_enabled and batch_complete and not result['error']:
                        reward_start=time.monotonic()
                        try:
                            gain=reward.gain(reward_before,reward.value(archive,deadline))
                            if gain<0:raise ValueError('Negative exact full-archive reward gain')
                            reward_status='completed'
                        finally:reward_wall+=time.monotonic()-reward_start
                finally:
                    coordinator_wall=time.monotonic()-validation_start
                    spent=result['service_elapsed']+coordinator_wall
                    metrics['coordinator_batch_wall']+=coordinator_wall
                    metrics['reward_wall']+=reward_wall
                    if reward_enabled:
                        if reward_status=='completed' and time.monotonic()<deadline:
                            feedback_start=time.monotonic()
                            observed=scheduler.observe(task['kind'],gain,spent,deadline=deadline)
                            metrics['feedback_wall']+=time.monotonic()-feedback_start
                            if not observed:reward_status='censored';gain=None
                        else:
                            reward_status='infrastructure_error' if result['error'] and not result['timeout'] else 'censored'
                            gain=None
                    scheduler.elapsed[task['kind']]+=spent
                    event=dict(task=dict(task),
                        elapsed=spent,worker_service_elapsed=result['service_elapsed'],
                        coordinator_batch_wall=coordinator_wall,
                        validation_wall=metrics['validation_wall']-batch_validation_before,
                        archive_update_wall=metrics['archive_update_wall']-batch_archive_before,
                        worker_setup_wall=result['setup_elapsed'],
                        worker_search_communication_wall=result['search_communication_elapsed'],
                        archive_before=archive_before,hv_spec_sha256=config['hv_spec_sha256'],
                        r2_spec_sha256=config.get('r2_spec_sha256'),
                        reward=dict(status=reward_status,gain=str(gain) if gain is not None else None,
                                    type='r2-asf-v1' if config['scheduler_mode']=='adaptive_r2' else 'hv-v1' if adaptive else None,
                                    wall=reward_wall,
                                    gain_per_service=str(gain / __import__('fractions').Fraction(str(spent))) if gain is not None else None),
                        cumulative_service=dict(scheduler.elapsed),
                        validation_deadline_elapsed=budget,
                        last_validation_elapsed=time.monotonic()-start,
                        pending_after=pool.pending_counts(),
                        at=time.monotonic()-start,archive_size=len(archive.points),accepted=accepted,
                        candidates=len(candidates),continued=result['continued'],
                        native_slice=result.get('native_slice'),
                        outcome='NEW_POINT' if accepted else 'SEARCH_FAILED_UNKNOWN',error=result['error'],
                        candidate_accounting=dict(
                            **{key:metrics[key]-before[key] for key in count_keys},
                            archive_outcomes={key:metrics['archive_outcomes'][key]-before_outcomes[key]
                                              for key in before_outcomes}))
                    write_start=time.monotonic();stream.append(event)
                    metrics['event_write_wall']+=time.monotonic()-write_start
                if result['error'] and not result['timeout']:raise RuntimeError(result['error'])
                if result['timeout']:
                    stop_reason=result['error']
                    if time.monotonic()<deadline-.01:
                        status='INTERRUPTED'
                        error='NATIVE_TIMEOUT_BEFORE_GLOBAL_DEADLINE: '+str(result['error'])
                    raise TimeoutError(stop_reason)
            if output and time.monotonic()-last_checkpoint>=.5:
                checkpoint_start=time.monotonic();stream.flush()
                atomic_json(str(output)+'.checkpoint',dict(status='CHECKPOINT',started=start,
                    archive=list(archive.points.values()),validated_by_elapsed=time.monotonic()-start,
                    budget=budget,seed=seed,algorithm=algorithm,source=str(path),variant=variant,
                    implementation_fingerprint=impl,implementation_sha256=code_hash,protocol=RUN_PROTOCOL,
                    algorithm_configuration=config,queue_identity=queue_identity,input_limits=caps,
                    model_source=model.source,model_fingerprint=model.fingerprint(),
                    variable_names=[v.name for v in model.variables],original_directions=model.directions,
                    objective_dimension=len(model.objectives),first_feasible_elapsed=first_feasible,
                    events_log=stream.descriptor(),statistics=dict(metrics)))
                metrics['checkpoint_wall']+=time.monotonic()-checkpoint_start
                last_checkpoint=time.monotonic()
    except TimeoutError as e:stop_reason=str(e)
    except (Exception,KeyboardInterrupt) as e:
        status='INTERRUPTED' if isinstance(e,KeyboardInterrupt) else 'ERROR';error=f'{type(e).__name__}: {e}'
    finally:
        try:
            if output:atomic_json(str(output)+'.lifecycle',dict(stage='before-close',started=start,elapsed=time.monotonic()-start))
        finally:
            try:
                if pool:
                    pool.close()
                    # Unconsumed/late replies are accounted, never admitted during cleanup.
                    for worker,item in list(pool.pending.items()):
                        if item['future'].cancelled():continue
                        r=pool.take(worker);task=r['task']
                        scheduler.elapsed[task['kind']]+=r['service_elapsed']
                        candidates=r['candidates'];metrics['candidates_returned']+=len(candidates)
                        distinct=len({tuple(c['x']) for c in candidates})
                        metrics['batch_distinct_assignments']+=distinct
                        metrics['batch_repeat_assignments']+=len(candidates)-distinct
                        metrics['dropped_on_close']+=len(candidates)
                        metrics['completed_response_wait_wall']+=r['response_wait_elapsed']
                        metrics['worker_setup_wall']+=r['setup_elapsed']
                        metrics['worker_search_communication_wall']+=r['search_communication_elapsed']
                        metrics['worker_service_wall']+=r['service_elapsed']
                        count_native_slice(r)
                        event=dict(task=dict(task),
                            elapsed=r['service_elapsed'],worker_service_elapsed=r['service_elapsed'],
                            at=time.monotonic()-start,archive_size=len(archive.points),accepted=0,
                            candidates=len(candidates),continued=r['continued'],native_slice=r.get('native_slice'),
                            outcome='LATE_OR_CANCELLED',
                            reward=dict(status='censored',gain=None,wall=0.),
                            cumulative_service=dict(scheduler.elapsed),
                            hv_spec_sha256=config['hv_spec_sha256'],r2_spec_sha256=config.get('r2_spec_sha256'),
                            pending_after=pool.pending_counts(),
                            error=r['error'],candidate_accounting={'dropped_on_close':len(candidates)})
                        write_start=time.monotonic();stream.append(event)
                        metrics['event_write_wall']+=time.monotonic()-write_start
            except Exception as e:status='ERROR';error=f'cleanup: {e}'
            finally:
                if stream:
                    try:stream.close(complete=status=='COMPLETED')
                    except Exception as e:status='ERROR';error=f'event log close: {e}'
                resource.setrlimit(resource.RLIMIT_AS,old_as)
                os.sched_setaffinity(0,old_affinity)
        actual=time.monotonic()-start
        if output:atomic_json(str(output)+'.lifecycle',dict(stage='after-close',started=start,elapsed=actual))
    statistics=dict(metrics,workers=pool.records if pool else [],
                    steps=sum(r['steps'] for r in pool.records) if pool else 0)
    result=dict(status=status,error=error,algorithm=algorithm,variant=variant,protocol=RUN_PROTOCOL,
        source=str(path),seed=seed,budget=budget,actual_wall=actual,overrun=max(0,actual-budget),
        overrun_reason='deadline interruption/worker cleanup' if actual>budget else None,
        **limits,archive=[dict(p,epsilon_status='not required for original archive') for p in archive.points.values()],
        events_log=stream.descriptor() if stream else None,
        recent_events=list(stream.recent) if stream else [],
        time_shares=scheduler.elapsed if scheduler else {},statistics=statistics,
        statistics_complete=status=='COMPLETED',
        model_fingerprint=model.fingerprint() if model else None,model_source=model.source if model else None,
        claim='approximate nondominated validated samples; no optimality/infeasibility proof',
        service_time_definition='sum of worker active task wall plus coordinator batch snapshot/validation/admission/reward wall; concurrent intervals overlap; not CPU time',
        address_space_limit_applied=not sanitizer,
        search_outcome='FEASIBLE_SAMPLES_FOUND' if archive.points else 'NO_FEASIBLE_SAMPLES_FOUND')
    result.update(first_feasible_elapsed=first_feasible,
        bootstrap_statistics=dict(submissions=bootstrap.submissions,completions=bootstrap.completions) if pool else None,
        effective_learning_wall=max(0,budget-first_feasible) if first_feasible is not None else 0.,
        scheduler_mode=config['scheduler_mode'],search_backend=config.get('search_backend','ls_iqcqp'),
        objective_dimension=len(model.objectives) if model else None,
        reward_type='r2-asf-v1' if config['scheduler_mode']=='adaptive_r2' else 'hv-v1' if config['scheduler_mode']=='adaptive' else None,
        hv_spec_sha256=config['hv_spec_sha256'],r2_spec_sha256=config.get('r2_spec_sha256'),
        r2_online=(dict(status='COMPUTED',direction='minimize',exact_value=str(sum(reward._minima)/len(reward._minima)))
                   if config['scheduler_mode']=='adaptive_r2' and reward is not None
                   and reward._minima is not None and all(p in reward._seen for p in archive.points)
                   else dict(status='NOT_COMPUTED',direction='minimize',exact_value=None)
                   if config['scheduler_mode']=='adaptive_r2' else None),
        started=start,implementation_sha256=code_hash,
        implementation_fingerprint=impl if code_hash else None,
        queue_identity=queue_identity,stop_reason=stop_reason,
        algorithm_configuration=config,input_limits=caps,
        resource_observation=run_observation,
        applied_address_space_limit_mib=applied_address_space_mib,
        variable_names=[v.name for v in model.variables] if model else [],
        numeric_validation='exact-rational; zero feasibility tolerance; reject nonintegers',
        search_numeric='C++ long double',original_directions=model.directions if model else [])
    if output:
        atomic_json(str(output)+'.lifecycle',dict(stage='serialization',started=start,elapsed=time.monotonic()-start,
                                                   events=stream.count if stream else 0))
        atomic_json_new(output,result)
        atomic_json(str(output)+'.lifecycle',dict(stage='done',started=start,elapsed=time.monotonic()-start,
            events=stream.count if stream else 0,serialization_and_reporting_wall=time.monotonic()-start-actual))
    return result


def supervise(path,budget=10,seed=1,algorithm='apels',output=None,max_bytes=40_000_000,
              max_terms=500_000,max_variables=100_000,max_constraints=100_000,
              max_expression_terms=500_000,variant='Generic',algorithm_config=None,queue_identity=None):
    """Cold-run watchdog with owned-process death guards and audited recovery."""
    import subprocess,sys,signal,math,resource
    from .lifecycle import child_environment
    from .results import intact,read_recovery
    if algorithm!='apels':raise ValueError('Only the apels algorithm is available')
    if not math.isfinite(budget) or budget<=0:raise ValueError('budget must be finite and positive')
    if output is None:raise ValueError('watchdog needs output path')
    require_new_output(output)
    start=time.monotonic()
    config_snapshot=effective_configuration(algorithm_config)
    caps=input_limits(dict(max_bytes=max_bytes,max_terms=max_terms,max_variables=max_variables,
                           max_constraints=max_constraints,max_expression_terms=max_expression_terms))
    limits=plan(config_snapshot);old=os.sched_getaffinity(0)
    p=None;peak=0;reporting=False;failure=None
    old_as=resource.getrlimit(resource.RLIMIT_AS)
    observations={};resource_series=[];applied_supervisor=None
    from mo_iqcqp.experiment.proc_audit import sample_processes
    expected=dict(started=start,protocol=RUN_PROTOCOL,implementation_fingerprint=None,
                  algorithm_configuration=config_snapshot,queue_identity=queue_identity,
                  source=str(path),algorithm=algorithm,variant=variant,seed=seed,budget=budget,
                  input_limits=caps,variable_names=None,original_directions=None,
                  model_source=None,model_fingerprint=None,objective_dimension=None)
    request_path=Path(str(output)+'.request')
    request_descriptor=None
    cmd=[sys.executable,'-m','mo_iqcqp','_worker',str(path),'--budget',str(budget),
         '--seed',str(seed),'--algorithm',algorithm,'--output',str(output),
         '--started',str(start),'--variant',variant]
    for key,value in caps.items():cmd+=['--'+key.replace('_','-'),str(value)]
    environment=child_environment()
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):environment[key]='1'
    def terminate_owned(graceful=False):
        if p:
            if graceful and p.poll() is None:
                p.send_signal(signal.SIGINT)
                try:
                    p.communicate(timeout=.3)
                    return
                except subprocess.TimeoutExpired:pass
            # Popen signals only its unreaped direct child (poll/waitpid guarded).
            # Kernel parent-death guards terminate every managed backend child.
            # Never signal a numeric PID recovered from a dead parent's /proc tree.
            p.kill()
            p.communicate()
    def recover(status, error):
        # A graceful stop may already have published a richer final result.
        result=read_recovery(output,start,expected,path,caps)
        recovered='final' if result is not None else None
        if result is None:
            result=read_recovery(str(output)+'.checkpoint',start,expected,path,caps)
            if result is not None:recovered='checkpoint'
        if result is None:
            result=dict(expected,archive=[],metadata_status='MODEL_NOT_LOADED_OR_NOT_CHECKPOINTED')
        else:
            result['recovered_worker_status']=result['status']
            result['metadata_status']='MODEL_METADATA_AVAILABLE'
        # Retain an untrusted/invalid final artifact as evidence before publishing
        # the structured failure. Never silently discard bytes on failed recovery.
        if recovered!='final' and Path(output).exists():
            import shutil
            preserved=Path(str(output)+'.unverified-final')
            with preserved.open('xb') as dest,Path(output).open('rb') as source:
                shutil.copyfileobj(source,dest)
            result['unverified_final_artifact']=str(preserved)
        result.update(status=status,supervisor_failure=error,error=error,
                      recovery_source=recovered or 'none',actual_wall=time.monotonic()-start,
                      overrun=max(0,time.monotonic()-start-budget),
                      overrun_reason='supervisor stop; only previously validated saved samples retained',
                      statistics_complete=False)
        result['events_log']=inspect_event_log(str(output)+'.events.jsonl')
        return result
    try:
        os.sched_setaffinity(0,set(limits['affinity']))
        if 'sanitize' not in os.environ.get('MO_IQCQP_NATIVE',''):
            resource.setrlimit(resource.RLIMIT_AS,(limits['memory_mib']*1024**2,old_as[1]))
        applied_supervisor=resource.getrlimit(resource.RLIMIT_AS)[0]
        expected['implementation_fingerprint']=implementation_fingerprint()
        request=dict(schema='apels-worker-request-v1',started=start,
                     algorithm_configuration=config_snapshot,queue_identity=queue_identity)
        atomic_json_new(request_path,request)
        request_hash=file_sha256(request_path)
        request_descriptor=dict(path=str(request_path.resolve()),sha256=request_hash,
                                schema=request['schema'])
        cmd+=['--request-file',str(request_path.resolve()),'--request-sha256',request_hash]
        p=subprocess.Popen(cmd,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           text=True,env=environment)
        while True:
            owned=tree(p.pid)+[os.getpid()]
            tree_rss=rss(owned);peak=max(peak,tree_rss)
            sample_processes(owned,observations,time.monotonic()-start)
            resource_series.append(dict(at=time.monotonic()-start,rss_mib=tree_rss/1024**2))
            if peak>limits['tree_memory_mib']*1024**2:
                failure='RESOURCE_TREE_RSS_LIMIT';raise MemoryError(failure)
            free=available_memory()
            if free is not None and free<1024**3:
                failure='RESOURCE_HOST_MEMORY_PRESSURE';raise MemoryError(failure)
            now=time.monotonic()
            if now>start+budget+.5:
                lifecycle=Path(str(output)+'.lifecycle')
                try:life=json.loads(lifecycle.read_text())
                except (OSError,ValueError):life={}
                reporting=life.get('started')==start and life.get('stage') in ('after-close','serialization','done')
                if not reporting or now>start+budget+30.5:raise subprocess.TimeoutExpired(cmd,budget+.5)
            try:
                stdout,stderr=p.communicate(timeout=.05);break
            except subprocess.TimeoutExpired:pass
        if stdout and p.returncode:print(stdout,end='',file=sys.stderr)
        if stderr:print(stderr,file=sys.stderr,end='')
        if not Path(output).exists():raise RuntimeError(f'worker exit {p.returncode} without result')
        result=json.loads(Path(output).read_text())
        if result.get('started')!=start:raise RuntimeError('Worker failed; refusing stale result from a different invocation')
        if not intact(result):raise RuntimeError('RESULT_INTEGRITY_MISMATCH')
        if p.returncode and result.get('status')=='COMPLETED':
            raise RuntimeError(f'worker exit {p.returncode} after publishing completed result')
        if result.get('events_log'):
            observed=inspect_event_log(str(output)+'.events.jsonl')
            if observed is None or observed['complete']!=result['events_log']['complete'] or observed['sha256']!=result['events_log']['sha256'] or observed['records']!=result['events_log']['records']:
                result.update(status='ERROR',error='EVENT_LOG_INTEGRITY_MISMATCH',events_log=observed)
    except (subprocess.TimeoutExpired,KeyboardInterrupt,MemoryError) as exc:
        terminate_owned(graceful=isinstance(exc,KeyboardInterrupt))
        status='INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else failure or ('RESOURCE_HOST_MEMORY_PRESSURE' if isinstance(exc,MemoryError) else 'HARD_TIMEOUT')
        result=recover(status,type(exc).__name__+': '+str(exc))
    except Exception as exc:
        terminate_owned()
        result=recover('ERROR',type(exc).__name__+': '+str(exc))
    finally:
        resource.setrlimit(resource.RLIMIT_AS,old_as)
        os.sched_setaffinity(0,old)
    result.update(process_observations=list(observations.values()),resource_series=resource_series,
                  supervisor_applied_address_space_limit_mib=applied_supervisor/1024**2 if applied_supervisor is not None and applied_supervisor>=0 else None,
                  source=str(path),tree_peak_rss_mib=peak/1024**2,rss_sampling_seconds=.05,
                  supervisor_wall=time.monotonic()-start,resource_limits=limits,worker_request=request_descriptor,
                  input_limits=caps,supervisor_resource_observation=resource_observation())
    atomic_json(output,result)
    return result



def queue(config,output,on_result=None):
    """Preflight is untimed; each dispatched supervise() remains one cold run."""
    cfg=json.loads(Path(config).read_text())
    if not isinstance(cfg,dict) or set(cfg)-{'jobs','workers','core_budget','memory_mib','tree_memory_mib','runs'}:
        raise ValueError('Unknown queue configuration key')
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    queue_limits=plan(cfg)
    if not isinstance(cfg.get('runs'),list):raise ValueError('Queue runs must be a list')
    for task in cfg['runs']:
        precheck_start=time.monotonic()
        snapshot=effective_configuration(task.get('algorithm_config'))
        if snapshot['core_budget']>queue_limits['core_budget']:
            raise ValueError('Run core_budget exceeds queue core_budget')
        source_sha=file_sha256(task['path'])
        caps=input_limits({key:task[key] for key in INPUT_DEFAULTS if key in task})
        from mo_iqcqp.io.lp import ParseError
        try:objective_dimension=len(load_lp(task['path'],**caps).objectives)
        except ParseError:objective_dimension=None
        identity=queue_run_identity(task,snapshot,source_sha,implementation_fingerprint(),objective_dimension)
        digest=identity['digest']
        attempt=0
        while True:
            name=digest+('.attempt-'+str(attempt) if attempt else '')+'.json'
            dest=out/name
            if not dest.exists():
                if any(Path(str(dest)+suffix).exists() for suffix in
                       ('.events.jsonl','.checkpoint','.lifecycle','.tmp','.request','.request.tmp','.unverified-final')):
                    attempt+=1;continue
                break
            # Historical results without the new identity are preserved but never trusted.
            try:previous=json.loads(dest.read_text())
            except (OSError,ValueError):previous=None
            if _identity_matches(previous,identity,dest):
                dest=None;break
            attempt+=1
        if dest is None:continue
        precheck_wall=time.monotonic()-precheck_start
        dispatch={k:v for k,v in task.items() if k!='algorithm_config'}
        result=supervise(**dispatch,algorithm_config=snapshot,queue_identity=identity,output=dest)
        result['queue_precheck_wall']=precheck_wall
        result['queue_precheck_scope']='configuration/source/implementation fingerprint, LP parse, result integrity and sample revalidation, resume lookup; excluded from cold-run clock'
        atomic_json(dest,result)
        if result.get('status')=='COMPLETED' and not _identity_matches(result,identity,dest):
            raise RuntimeError('Completed queue result failed identity verification')
        if on_result:on_result(result)
        if result.get('status')!='COMPLETED':raise RuntimeError('QUEUE_STOPPED: '+str(result.get('status')))

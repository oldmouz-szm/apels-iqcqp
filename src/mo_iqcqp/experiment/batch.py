"""Serial, restartable batch dispatch; each run owns its bounded worker tree."""
import fcntl
import json
import math
import time
from pathlib import Path

from .resources import INPUT_DEFAULTS, input_limits, plan, preparation_memory_limit

RESOURCE_KEYS={'jobs','workers','core_budget','memory_mib','tree_memory_mib'}


def prepare(path, output, budget=60., seeds=(1,), pattern='*.lp',
            normalization='box', resources=None, caps=None, variant='Generic'):
    """Freeze per-model configurations without copying input files."""
    from mo_iqcqp.io import load_lp
    from mo_iqcqp.metrics.normalization import make_spec, coefficient_units
    from mo_iqcqp.metrics.r2 import FixedR2
    from .runner import atomic_json_new
    if not math.isfinite(budget) or budget<=0:raise ValueError('budget must be finite and positive')
    if not seeds or any(type(s) is not int or not 0<=s<2**64 for s in seeds):
        raise ValueError('seeds must be nonempty unsigned 64-bit integers')
    if len(set(seeds))!=len(seeds):raise ValueError('Duplicate seeds')
    if normalization not in ('box','coefficient'):raise ValueError('Unknown normalization')
    if variant not in ('Generic','Structure-enhanced'):raise ValueError('Unknown variant')
    target=Path(output)
    if target.exists():raise FileExistsError(target)
    limits=plan(resources or {});limits.pop('affinity')
    caps=input_limits(caps)
    root=Path(path).resolve(strict=True)
    files=sorted(p for p in root.rglob(pattern) if p.is_file()) if root.is_dir() else [root]
    if not files:raise ValueError('No matching input files')
    runs=[];errors=[]
    for source in files:
        try:
            with preparation_memory_limit(limits['memory_mib']):
                model=load_lp(source,**caps)
                explicit=coefficient_units(model) if normalization=='coefficient' else None
                spec=make_spec(model,explicit=explicit)
                FixedR2(spec,model)
                del model
            config=dict(limits,scheduler_mode='adaptive_r2',r2_spec=spec)
            for seed in seeds:
                runs.append(dict(path=str(source),budget=budget,seed=seed,variant=variant,
                                 algorithm_config=config,**caps))
        except (ValueError,OSError,ArithmeticError,MemoryError) as exc:
            errors.append(dict(source=str(source),status='PREPARATION_MEMORY_LIMIT' if isinstance(exc,MemoryError) else 'PREPARATION_ERROR',error=f'{type(exc).__name__}: {exc}'))
        finally:
            model=None  # Release a loaded model even if normalization exhausted its cap.
    manifest=dict(limits,runs=runs,continue_on_error=True,preparation_errors=errors)
    atomic_json_new(target,manifest)
    return dict(output=str(target.resolve()),runs=len(runs),preparation_errors=errors)


def queue(config,output,on_result=None):
    """Continue after a failed instance, preserve attempts and verify reuse."""
    from .runner import (atomic_json, canonical_json, effective_configuration, file_sha256,
                         implementation_for_config, queue_run_identity, _identity_matches, supervise)
    from mo_iqcqp.io import load_lp
    import hashlib
    import stat
    source=Path(config).resolve(strict=True)
    cfg=json.loads(source.read_text())
    allowed=RESOURCE_KEYS|{'runs','continue_on_error','preparation_errors'}
    if not isinstance(cfg,dict) or set(cfg)-allowed:raise ValueError('Unknown queue configuration key')
    if not isinstance(cfg.get('runs'),list):raise ValueError('Queue runs must be a list')
    if type(cfg.get('continue_on_error',True)) is not bool:raise ValueError('continue_on_error must be boolean')
    if not isinstance(cfg.get('preparation_errors',[]),list):raise ValueError('Invalid preparation_errors')
    limits=plan(cfg)
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    # Keep the inode stable: unlinking a held flock allows a second owner.
    with (out/'.queue.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise RuntimeError('Output directory is already in use by another queue') from exc
        digest=hashlib.sha256(canonical_json(cfg).encode()).hexdigest()
        summary_path=out/'queue-summary.json'
        if summary_path.exists():
            previous=json.loads(summary_path.read_text())
            if previous.get('manifest_sha256')!=digest:
                raise ValueError('Output directory belongs to a different queue manifest')
        summary=dict(schema='apels-queue-summary-v1',manifest_sha256=digest,
                     configuration=str(source),status='RUNNING',total=len(cfg['runs']),
                     preparation_errors=cfg.get('preparation_errors',[]),runs=[])
        started=time.monotonic()
        def save():
            summary['elapsed']=time.monotonic()-started
            summary['completed']=sum(r['status']=='COMPLETED' for r in summary['runs'])
            summary['failed']=sum(r['status']!='COMPLETED' for r in summary['runs'])+len(summary['preparation_errors'])
            atomic_json(summary_path,summary)
        save()
        for index,raw in enumerate(cfg['runs']):
            record=dict(index=index,source=raw.get('path') if isinstance(raw,dict) else None,
                        status='ERROR',reused=False,output=None)
            try:
                precheck=time.monotonic()
                if not isinstance(raw,dict):raise ValueError('Queue task must be an object')
                task=dict(raw)
                path=Path(task['path'])
                task['path']=str((source.parent/path).resolve(strict=True))
                record['source']=task['path']
                run_config=task.get('algorithm_config') or {}
                if isinstance(run_config,str):run_config=json.loads((source.parent/run_config).read_text())
                if not isinstance(run_config,dict):raise ValueError('Invalid run configuration')
                if 'configuration_schema' not in run_config:
                    run_config={**{k:cfg[k] for k in RESOURCE_KEYS if k in cfg},**run_config}
                snapshot=effective_configuration(run_config)
                for key in ('workers','core_budget','memory_mib','tree_memory_mib'):
                    if snapshot[key]>limits[key]:raise ValueError(f'Run {key} exceeds queue {key}')
                caps=input_limits({key:task[key] for key in INPUT_DEFAULTS if key in task})
                st=Path(task['path']).stat()
                if not stat.S_ISREG(st.st_mode):raise ValueError('QUEUE_INPUT_MUST_BE_REGULAR_FILE')
                if st.st_size>caps['max_bytes']:raise ValueError('QUEUE_INPUT_BYTE_LIMIT')
                sha=file_sha256(task['path'])
                with preparation_memory_limit(snapshot['memory_mib']):
                    model=load_lp(task['path'],**caps)
                    dimension=len(model.objectives)
                    del model
                identity=queue_run_identity(task,snapshot,sha,implementation_for_config(snapshot),dimension)
                attempt=0
                while True:
                    name=identity['digest']+('.attempt-'+str(attempt) if attempt else '')+'.json'
                    dest=out/name
                    if not dest.exists():
                        if any(out.glob(name+'.*')):attempt+=1;continue
                        break
                    try:previous=json.loads(dest.read_text())
                    except (OSError,ValueError):previous=None
                    if _identity_matches(previous,identity,dest):
                        result=previous;record['reused']=True;break
                    attempt+=1
                if not record['reused']:
                    elapsed=time.monotonic()-precheck
                    dispatch={k:v for k,v in task.items() if k!='algorithm_config'}
                    result=supervise(**dispatch,algorithm_config=snapshot,queue_identity=identity,output=dest)
                    result['queue_precheck_wall']=elapsed
                    result['queue_precheck_scope']='configuration/source/implementation, LP parse and verified resume; excluded from cold-run budget'
                    atomic_json(dest,result)
                    if result.get('status')=='COMPLETED' and not _identity_matches(result,identity,dest):
                        raise RuntimeError('Completed queue result failed identity verification')
                record.update(status=result['status'],output=str(dest.resolve()),
                              search_outcome=result.get('search_outcome'),
                              solution_count=len(result.get('archive',[])),error=result.get('error'))
                if on_result:on_result(result)
            except KeyboardInterrupt:
                record.update(status='INTERRUPTED',error='Queue interrupted')
            except Exception as exc:
                record.update(status='RESOURCE_PRECHECK_MEMORY_LIMIT' if isinstance(exc,MemoryError) else 'ERROR',error=f'{type(exc).__name__}: {exc}')
                if on_result:on_result(dict(source=record['source'],status=record['status'],archive=[],error=record['error']))
            summary['runs'].append(record)
            save()
            if record['status']=='INTERRUPTED':summary['status']='INTERRUPTED';break
            if record['status']!='COMPLETED' and not cfg.get('continue_on_error',True):
                summary['status']='STOPPED';break
        else:summary['status']='COMPLETED_WITH_ERRORS' if summary['failed'] else 'COMPLETED'
        save()
        return summary

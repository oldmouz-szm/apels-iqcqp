import argparse
import json
from .experiment.runner import run,queue,supervise
from .io import load_lp
from .experiment.resources import INPUT_DEFAULTS

def _compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),default=str)


def run_lines(result,metrics=None,metric_error=False):
    """Only requested run fields and original objective vectors reach stdout."""
    archive=result.get('archive') or []
    yield 'instance='+_compact(result.get('source'))
    wall=result.get('actual_wall')
    yield 'wall_seconds='+('NA' if wall is None else f'{wall:.6f}')
    yield f'solution_count={len(archive)}'
    if metric_error:
        yield 'HV=ERROR'
    elif metrics is None:
        yield 'HV=NOT_COMPUTED'
    else:
        yield 'HV='+_compact(metrics['hv']['value'])
    for index,point in enumerate(archive,1):
        yield f'solution[{index}] objectives='+_compact(point.get('original'))


def _show_run(result,metrics=None,metric_error=False):
    for line in run_lines(result,metrics,metric_error):print(line,flush=True)


def main():
    p=argparse.ArgumentParser(prog='mo-iqcqp');sub=p.add_subparsers(dest='command',required=True)
    for name in ['run','_worker']:
        r=sub.add_parser(name);r.add_argument('path');r.add_argument('--budget',type=float,default=10);r.add_argument('--seed',type=int,default=1);r.add_argument('--algorithm',choices=['apels'],default='apels');r.add_argument('--output',required=True)
        for key,default in INPUT_DEFAULTS.items():r.add_argument('--'+key.replace('_','-'),type=int,default=default)
        if name=='run':
            hv_options=r.add_mutually_exclusive_group()
            hv_options.add_argument('--hv-reference',help='comma-separated original-sense coordinates, e.g. 10,10; raw-unit HV after the solve')
            hv_options.add_argument('--metric-spec',help='fixed normalized HV specification; evaluated after the solve')
        if name=='_worker':
            r.add_argument('--started',type=float)
            r.add_argument('--algorithm-config-json')
            r.add_argument('--queue-identity-json')
            r.add_argument('--request-file')
            r.add_argument('--request-sha256')
        r.add_argument('--variant',choices=['Generic','Structure-enhanced'],default='Generic')
        r.add_argument('--algorithm-config')
    v=sub.add_parser('inspect');v.add_argument('path')
    q=sub.add_parser('queue');q.add_argument('config');q.add_argument('--output',required=True)
    m=sub.add_parser('metrics');m.add_argument('--run',required=True);m.add_argument('--spec',required=True)
    m.add_argument('--source');m.add_argument('--output',required=True)
    a=vars(p.parse_args());cmd=a.pop('command')
    if cmd in ('run','_worker'):
        if cmd=='_worker':
            from .experiment.lifecycle import arm_parent_death_guard
            arm_parent_death_guard()
            request_file=a.pop('request_file')
            request_hash=a.pop('request_sha256')
            if request_file is not None:
                from pathlib import Path
                import hashlib
                content=Path(request_file).read_bytes()
                if hashlib.sha256(content).hexdigest()!=request_hash:
                    p.exit(2,'WORKER_REQUEST_INTEGRITY_MISMATCH\n')
                payload=json.loads(content)
                if (set(payload)!={'schema','started','algorithm_configuration','queue_identity'} or
                    payload['schema']!='apels-worker-request-v1' or payload['started']!=a['started'] or a.get('algorithm_config_json') or
                    a.get('queue_identity_json') or a.get('algorithm_config')):
                    p.exit(2,'INVALID_WORKER_REQUEST\n')
                a['worker_request_sha256']=request_hash
                a['algorithm_config_json']=json.dumps(payload['algorithm_configuration'])
                a['queue_identity_json']=json.dumps(payload['queue_identity']) if payload['queue_identity'] is not None else None
            elif request_hash is not None:p.exit(2,'MISSING_WORKER_REQUEST\n')
        metric_spec=a.pop('metric_spec',None)
        hv_reference=a.pop('hv_reference',None)
        if hv_reference is not None:
            from .metrics import number
            hv_reference=[part.strip() for part in hv_reference.split(',')]
            if not 2 <= len(hv_reference) <= 4 or any(not part for part in hv_reference):
                p.error('--hv-reference needs 2 to 4 comma-separated coordinates')
            try:
                for part in hv_reference:number(part)
            except ValueError as exc:p.error(str(exc))
        if metric_spec:
            from pathlib import Path
            if not Path(metric_spec).is_file():p.error(f'Metric specification does not exist: {metric_spec}')
        try:result=(supervise if cmd=='run' else run)(**a)
        except FileExistsError as exc:p.exit(2,f'{p.prog}: {exc}\n')
        if cmd=='run':
            metrics=None;metric_error=False
            if (metric_spec or hv_reference) and result['status']=='COMPLETED':
                from .metrics import evaluate_saved_run, evaluate_saved_run_reference
                try:
                    metrics=(evaluate_saved_run(a['output'],metric_spec) if metric_spec else
                             evaluate_saved_run_reference(a['output'],hv_reference))
                except Exception as exc:
                    metric_error=True
                    import sys
                    print(f'mo-iqcqp: HV computation failed: {type(exc).__name__}: {exc}',file=sys.stderr)
            _show_run(result,metrics,metric_error)
            if result.get('error'):
                import sys
                print(f'mo-iqcqp: {result["error"]}',file=sys.stderr)
            if metric_error:raise SystemExit(2)
        if result['status']!='COMPLETED':
            import sys
            print('mo-iqcqp: status='+str(result['status']),file=sys.stderr)
            raise SystemExit(130 if result['status']=='INTERRUPTED' else
                             124 if result['status']=='HARD_TIMEOUT' else
                             3 if result['status'].startswith('RESOURCE_') else 2)
    elif cmd=='queue':
        try:queue(**a,on_result=_show_run)
        except RuntimeError as exc:p.exit(2,f'mo-iqcqp: {exc}\n')
    elif cmd=='metrics':
        from pathlib import Path
        import os
        import tempfile
        from .metrics import evaluate_saved_run
        result=evaluate_saved_run(a['run'],a['spec'],source_override=a['source'])
        target=Path(a['output']);target.parent.mkdir(parents=True,exist_ok=True)
        fd,temporary=tempfile.mkstemp(prefix='.metrics-',suffix='.tmp',dir=target.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump(result,stream,indent=2,ensure_ascii=False,allow_nan=False)
                stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.link(temporary,target)  # Atomic and refuses an existing result.
        finally:
            os.unlink(temporary)
        print('HV='+_compact(result['hv']['value']))
    else:
        m=load_lp(a['path']);print(json.dumps({'variables':len(m.variables),'objectives':len(m.objectives),'constraints':len(m.constraints),'fingerprint':m.fingerprint(),'source':m.source},indent=2))

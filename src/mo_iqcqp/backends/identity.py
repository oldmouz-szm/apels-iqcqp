"""Fingerprint the actual optional SCIP runtime in an isolated process."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def scip_runtime():
    import pyscipopt
    from pyscipopt import Model
    model=Model();model.hideOutput()
    root=Path(pyscipopt.__file__).resolve().parent
    files=set(root.rglob('*.py'))|set(root.rglob('*.so'))
    # Include the actual mapped SCIP shared library, including system installs.
    for line in Path('/proc/self/maps').read_text().splitlines():
        fields=line.split()
        if len(fields)>=6 and fields[-1].startswith('/') and 'libscip' in fields[-1]:
            files.add(Path(fields[-1]).resolve())
    payload=dict(schema='scip-runtime-v1',python_version=sys.version,
                 python_sha256=file_hash(Path(sys.executable).resolve()),
                 pyscipopt_version=pyscipopt.__version__,
                 scip_version=[model.getMajorVersion(),model.getMinorVersion(),model.getTechVersion()],
                 files=[dict(path=str(p),sha256=file_hash(p)) for p in sorted(files)])
    content=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
    return dict(sha256=hashlib.sha256(content).hexdigest(),payload=payload)

def probe_scip(deadline=float('inf')):
    from mo_iqcqp.experiment.lifecycle import child_environment
    root=Path(__file__).resolve().parents[3]
    python=os.environ.get('MO_IQCQP_SCIP_PYTHON') or sys.executable
    env=child_environment(dict(os.environ,PYTHONPATH=str(root/'src'),
                              PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1'))
    remaining=min(30.,deadline-time.monotonic())
    if remaining<=0:raise TimeoutError('SCIP fingerprint deadline')
    completed=subprocess.run([python,'-m','mo_iqcqp.backends.scip_worker','--fingerprint'],
                             env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=remaining)
    if completed.returncode:
        raise RuntimeError('SCIP_RUNTIME_UNAVAILABLE: '+completed.stderr[-2000:])
    result=json.loads(completed.stdout)
    if not isinstance(result,dict) or result.get('payload',{}).get('schema')!='scip-runtime-v1':
        raise RuntimeError('SCIP_RUNTIME_IDENTITY_INVALID')
    return result

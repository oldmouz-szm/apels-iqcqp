"""Integrity of saved artifacts and independent checks before queue reuse."""
import hashlib
import json
from fractions import Fraction
from pathlib import Path

SCHEMA = 'apels-result-content-v1'
STATUSES = {'COMPLETED','ERROR','INTERRUPTED','HARD_TIMEOUT',
            'RESOURCE_TREE_RSS_LIMIT','RESOURCE_HOST_MEMORY_PRESSURE','CHECKPOINT'}

def digest(data):
    payload = {k:v for k,v in data.items() if k != 'result_integrity'}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),
                         default=str,allow_nan=False).encode()).hexdigest()

def seal(data):
    if isinstance(data,dict) and data.get('status') in STATUSES:
        data['result_integrity'] = dict(schema=SCHEMA,sha256=digest(data))
    return data

def intact(data):
    if not isinstance(data, dict):
        return False
    try:
        return data.get('result_integrity') == dict(schema=SCHEMA,sha256=digest(data))
    except (ValueError,TypeError,OverflowError):
        return False

def validate_samples(result, source, caps):
    """Recompute stored coordinates; never trust a checksum as feasibility proof."""
    from mo_iqcqp.io import load_lp
    if not isinstance(result, dict):
        return False
    points = result.get('archive', [])
    if not isinstance(points,list):
        return False
    if not points:
        return True
    try:
        model = load_lp(source, **caps)
        if not isinstance(result.get('model_source'),dict) or result['model_source'].get('sha256') != model.source['sha256']:
            return False
        if result.get('variable_names') != [v.name for v in model.variables]:
            return False
        vectors = []
        for point in points:
            checked = model.validate(point['x'])
            if not checked['valid']:
                return False
            for key in ('original','internal'):
                values = point[key]
                if len(values) != len(model.objectives) or tuple(map(Fraction,values)) != tuple(checked[key]):
                    return False
            at = point.get('validated_by_elapsed')
            if at is not None and not 0 <= at < result['budget']:
                return False
            v = tuple(checked['internal'])
            for previous in vectors:
                # Weak dominance also rejects duplicate objective vectors.
                if all(a<=b for a,b in zip(previous,v)) or all(a<=b for a,b in zip(v,previous)):
                    return False
            vectors.append(v)
        return True
    except (ValueError,TypeError,KeyError,OSError,ArithmeticError):
        return False

def read_recovery(path, start, expected, source, caps):
    """Return a sealed same-invocation artifact with independently valid samples."""
    try:
        result = json.loads(Path(path).read_text())
        if not isinstance(result,dict) or result.get('started') != start or not intact(result):
            return None
        for key in ('implementation_fingerprint','algorithm_configuration','protocol','queue_identity'):
            if result.get(key) != expected.get(key):
                return None
        if not validate_samples(result,source,caps):
            return None
        return result
    except (OSError,ValueError,TypeError,MemoryError):
        return None

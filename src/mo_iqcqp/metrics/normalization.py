"""Exact conservative integer-box intervals. No inferred or fabricated variable bounds."""
from fractions import Fraction as F
import hashlib,json,math,random,time
from . import number,vector
from .online import check_deadline
SCHEMA='r2-asf-box-v2'
ALGORITHM='integer-box-independent-term-interval-v1'
def content_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def objective_intervals(model,deadline=float('inf'),clock=time.monotonic):
    boxes=[]
    for v in model.variables:
        check_deadline(deadline,clock)
        if v.kind not in ('B','I','GENERAL','INTEGER'):raise ValueError('Integer variables required')
        if v.lower is None or v.upper is None:boxes.append(None);continue
        lo,hi=math.ceil(number(v.lower)),math.floor(number(v.upper))
        if lo>hi:raise ValueError('Empty integer box')
        boxes.append((lo,hi))
    ans=[]
    for expr,direction in zip(model.objectives,model.directions):
        check_deadline(deadline,clock)
        lo=hi=0
        for key,coefficient in expr.terms.items():
            check_deadline(deadline,clock);a=coefficient if type(coefficient) is int else number(coefficient)
            if not key:tl=tu=1
            elif any(boxes[i] is None for i in key):raise ValueError('No finite objective interval; explicit finite origin and positive scale required')
            elif len(key)==1:tl,tu=boxes[key[0]]
            elif len(key)==2 and key[0]==key[1]:
                l,u=boxes[key[0]];tl=0 if l<=0<=u else min(l*l,u*u);tu=max(l*l,u*u)
            elif len(key)==2:
                l,u=boxes[key[0]];v,w=boxes[key[1]];ps=(l*v,l*w,u*v,u*w);tl,tu=min(ps),max(ps)
            else:raise ValueError('Only quadratic terms supported')
            ends=(a*tl,a*tu);lo+=min(ends);hi+=max(ends)
        if direction=='max':lo,hi=-hi,-lo
        elif direction!='min':raise ValueError('Invalid direction')
        ans.append((lo,hi))
    return ans

def default_weights(m,seed):
    rng=random.Random(seed+m*1009)
    ans=[[str(int(i==j)) for i in range(m)] for j in range(m)]
    for _ in range(32):
        raw=[rng.randrange(1,18) for _ in range(m)];total=sum(raw)
        ans.append([str(F(v,total)) for v in raw])
    return ans

def coefficient_units(model):
    """Optional fixed units for unbounded boxes; never claim objective bounds.

    An origin equal to the objective constant and an L1 coefficient scale make
    this deterministic and invariant to positive rescaling of an objective.
    These units guide search; they do not restrict the variable domains.
    """
    origins=[];scales=[];coordinates=[]
    for expr,direction in zip(model.objectives,model.directions):
        sign=1 if direction=='min' else -1
        origins.append(str(sign*expr.terms.get((),0)))
        scales.append(str(sum(abs(a) for key,a in expr.terms.items() if key) or 1))
        coordinates.append(dict(L=None,U=None,unit='internal objective coefficient units',
                                scale_kind='coefficient_l1',lower_bound_status='unproved',
                                proof='fixed coefficient units; no objective bound asserted'))
    return dict(origin_internal=origins,scale=scales,coordinates=coordinates)

def make_spec(model,seed=20260930,explicit=None,deadline=float('inf')):
    m=len(model.objectives)
    if m<2:raise ValueError('At least two objectives required')
    if explicit is None:
        intervals=objective_intervals(model,deadline)
        norm=dict(origin_internal=[str(l) for l,u in intervals],scale=[str(u-l if u>l else 1) for l,u in intervals])
        coords=[dict(L=str(l),U=str(u),unit='internal objective coefficient units',
                     scale_kind='box_range' if u>l else 'constant_positive_unit',
                     lower_bound_status='proved_box',proof=ALGORITHM) for l,u in intervals]
        algorithm=ALGORITHM
    else:
        if not isinstance(explicit,dict) or set(explicit)!={'origin_internal','scale','coordinates'}:raise ValueError('Explicit normalization needs coordinates/proof metadata')
        os=vector(explicit['origin_internal'],m,'origin');ss=vector(explicit['scale'],m,'scale')
        if any(v<=0 for v in ss):raise ValueError('Positive scales required')
        norm=dict(origin_internal=list(map(str,os)),scale=list(map(str,ss)))
        coords=explicit['coordinates'];algorithm='explicit-frozen-units-v1'
        if not isinstance(coords,list) or len(coords)!=m:raise ValueError('Coordinate provenance dimension')
    weights=default_weights(m,seed)
    fingerprint=model.fingerprint()
    provenance=dict(algorithm=algorithm,coordinates=coords,model_fingerprint=fingerprint,source_sha256=model.source['sha256'],
                    normalization_sha256=content_hash(norm),weights_sha256=content_hash(weights))
    return dict(schema=SCHEMA,model_fingerprint=fingerprint,source_sha256=model.source['sha256'],objective_directions=model.directions,
                normalization=norm,weights=weights,normalization_provenance=provenance)

def validate_provenance(spec,model,origin,scale,deadline=float('inf'),clock=time.monotonic,fingerprint=None):
    p=spec['normalization_provenance'];m=len(origin)
    keys={'algorithm','coordinates','model_fingerprint','source_sha256','normalization_sha256','weights_sha256'}
    if not isinstance(p,dict) or set(p)!=keys:raise ValueError('Invalid normalization provenance')
    if (p['model_fingerprint']!=(fingerprint or model.fingerprint()) or p['source_sha256']!=model.source['sha256'] or
        p['normalization_sha256']!=content_hash(spec['normalization']) or p['weights_sha256']!=content_hash(spec['weights'])):
        raise ValueError('Normalization provenance/content mismatch')
    coords=p['coordinates']
    if not isinstance(coords,list) or len(coords)!=m:raise ValueError('Coordinate provenance dimension')
    reliable=[]
    for j,c in enumerate(coords):
        check_deadline(deadline,clock)
        if not isinstance(c,dict) or set(c)!={'L','U','unit','scale_kind','lower_bound_status','proof'}:raise ValueError('Invalid coordinate metadata')
        if any(type(c[k]) is not str or not c[k].strip() for k in ('unit','scale_kind','proof')):raise ValueError('Unit/proof required')
        status=c['lower_bound_status']
        if status not in ('proved_box','declared_reliable','unproved'):raise ValueError('Invalid proof status')
        l=number(c['L']) if c['L'] is not None else None;u=number(c['U']) if c['U'] is not None else None
        if l is not None and u is not None and l>u:raise ValueError('Invalid objective bounds')
        if status!='unproved' and (l is None or origin[j]>l):raise ValueError('Reliable origin must not exceed declared lower bound')
        reliable.append(l if status!='unproved' else None)
    if p['algorithm']==ALGORITHM:
        expected=objective_intervals(model,deadline,clock)
        for j,((l,u),c) in enumerate(zip(expected,coords)):
            if (c['lower_bound_status']!='proved_box' or number(c['L'])!=l or number(c['U'])!=u or origin[j]!=l or scale[j]!=(u-l if u>l else 1)):
                raise ValueError('Box normalization derivation mismatch')
    elif p['algorithm']=='explicit-frozen-units-v1':
        if any(c['lower_bound_status']=='proved_box' for c in coords):raise ValueError('Explicit provenance cannot claim box proof')
    else:raise ValueError('Unknown normalization derivation')
    return tuple(reliable)

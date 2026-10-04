"""Frozen, exact finite-weight signed ASF/R2 reward for minimization archives."""
from fractions import Fraction
import time

from . import number, vector
from .online import check_deadline


class FixedR2:
    schema = 'r2-asf-v1'

    @staticmethod
    def gain(before, after):
        return before-after

    def __init__(self, spec, model, deadline=float('inf'), clock=time.monotonic):
        self.clock = clock
        check_deadline(deadline, clock)
        required = {'schema', 'model_fingerprint', 'source_sha256',
                    'objective_directions', 'normalization', 'weights'}
        from .normalization import SCHEMA,validate_provenance
        new=isinstance(spec,dict) and spec.get('schema')==SCHEMA
        if new:required=required|{'normalization_provenance'}
        if not isinstance(spec, dict) or set(spec) != required or spec['schema'] not in (self.schema,SCHEMA):
            raise ValueError('Invalid fixed R2 specification')
        fingerprint=model.fingerprint()
        if (spec['model_fingerprint'] != fingerprint or
                spec['source_sha256'] != model.source['sha256'] or
                spec['objective_directions'] != model.directions):
            raise ValueError('R2 specification source/model/direction mismatch')
        self.dimensions = len(model.objectives)
        if self.dimensions < 2:
            raise ValueError('R2 requires at least two objectives')
        norm = spec['normalization']
        if not isinstance(norm, dict) or set(norm) != {'origin_internal', 'scale'}:
            raise ValueError('Invalid R2 normalization')
        self.origin = vector(norm['origin_internal'], self.dimensions, 'origin')
        self.scale = vector(norm['scale'], self.dimensions, 'scale')
        if any(s <= 0 for s in self.scale):
            raise ValueError('R2 scales must be positive')
        raw = spec['weights']
        if not isinstance(raw, list) or not raw:
            raise ValueError('R2 needs finite nonempty weights')
        parsed=[]
        for w in raw:
            check_deadline(deadline, clock)
            if len(w)!=self.dimensions:raise ValueError('weight dimension mismatch')
            row=[]
            for j,v in enumerate(w):
                if j % 128 == 0:check_deadline(deadline, clock)
                row.append(number(v))
            parsed.append(tuple(row))
        self.weights=tuple(parsed)
        self._active=tuple(tuple((j,w) for j,w in enumerate(row) if w) for row in self.weights)
        if any(any(v < 0 for v in w) or sum(w) != 1 for w in self.weights):
            raise ValueError('R2 weights must be nonnegative and sum to one')
        self.reliable_lower=(validate_provenance(spec,model,self.origin,self.scale,deadline,clock,fingerprint) if new else (None,)*self.dimensions)
        self._seen = set()
        self._minima = None
        check_deadline(deadline, clock)

    def check_point(self, point, deadline=float('inf')):
        if len(point)!=self.dimensions:raise ValueError('R2 point dimension mismatch')
        for j,(x,l) in enumerate(zip(point,self.reliable_lower)):
            if j % 128 == 0:check_deadline(deadline,self.clock)
            if l is not None and number(x)<l:
                raise ValueError('RELIABLE_R2_LOWER_BOUND_BREACH')

    def _asf(self, point, weight, deadline=float('inf')):
        best=None
        for j,(x,o,s,w) in enumerate(zip(point,self.origin,self.scale,weight)):
            if j % 128 == 0:check_deadline(deadline,self.clock)
            value=w*(number(x)-o)/s
            best=value if best is None else max(best,value)
        return best

    def full_value(self, archive, deadline=float('inf')):
        """Independent complete-archive recomputation; useful for audits."""
        if not archive.points:
            return None
        minima = []
        for weight in self.weights:
            best = None
            for point in archive.points:
                check_deadline(deadline, self.clock)
                if len(point) != self.dimensions:
                    raise ValueError('R2 point dimension mismatch')
                self.check_point(point,deadline)
                value = self._asf(point, weight, deadline)
                best = value if best is None else min(best, value)
            minima.append(best)
        check_deadline(deadline, self.clock)
        return sum(minima, Fraction(0)) / len(minima)

    def value(self, archive, deadline=float('inf')):
        """Atomic cache; normalize each new point once, retain only live keys."""
        check_deadline(deadline,self.clock)
        if not archive.points:
            return None
        minima=list(self._minima) if self._minima is not None else [None]*len(self.weights)
        current=set()
        for point in archive.points:
            check_deadline(deadline,self.clock)
            current.add(point)
            if point in self._seen:continue
            self.check_point(point,deadline)
            y=[]
            for j,(x,o,s) in enumerate(zip(point,self.origin,self.scale)):
                if j % 128 == 0:check_deadline(deadline,self.clock)
                y.append((number(x)-o)/s)
            for i,active in enumerate(self._active):
                check_deadline(deadline,self.clock)
                # Missing zero-weight coordinates contribute exactly zero.
                # Keep this floor for arbitrary signed origins; do not use abs().
                scalar=Fraction(0) if len(active)<self.dimensions else None
                for k,(j,w) in enumerate(active):
                    if k % 128 == 0:check_deadline(deadline,self.clock)
                    v=w*y[j]
                    scalar=v if scalar is None or v>scalar else scalar
                minima[i]=scalar if minima[i] is None else min(minima[i],scalar)
        total=sum(minima,Fraction(0))/len(minima)
        check_deadline(deadline,self.clock)
        self._seen,self._minima=current,tuple(minima)
        return total

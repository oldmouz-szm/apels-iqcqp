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
        if (spec['model_fingerprint'] != model.fingerprint() or
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
        self.weights = tuple(vector(w, self.dimensions, 'weight') for w in raw)
        if any(any(v < 0 for v in w) or sum(w) != 1 for w in self.weights):
            raise ValueError('R2 weights must be nonnegative and sum to one')
        self.reliable_lower=(validate_provenance(spec,model,self.origin,self.scale,deadline,clock) if new else (None,)*self.dimensions)
        self._seen = set()
        self._minima = None
        check_deadline(deadline, clock)

    def check_point(self, point):
        if len(point)!=self.dimensions:raise ValueError('R2 point dimension mismatch')
        if any(l is not None and number(x)<l for x,l in zip(point,self.reliable_lower)):
            raise ValueError('RELIABLE_R2_LOWER_BOUND_BREACH')

    def _asf(self, point, weight):
        return max(w * (number(x)-o) / s
                   for x, o, s, w in zip(point, self.origin, self.scale, weight))

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
                self.check_point(point)
                value = self._asf(point, weight)
                best = value if best is None else min(best, value)
            minima.append(best)
        check_deadline(deadline, self.clock)
        return sum(minima, Fraction(0)) / len(minima)

    def value(self, archive, deadline=float('inf')):
        """Commit cache only after all dimensions, points and deadline checks pass."""
        if not archive.points:
            return None
        seen = set(self._seen)
        minima = list(self._minima) if self._minima is not None else [None]*len(self.weights)
        for point in archive.points:
            check_deadline(deadline, self.clock)
            if len(point) != self.dimensions:
                raise ValueError('R2 point dimension mismatch')
            self.check_point(point)
            if point in seen:
                continue
            for i, weight in enumerate(self.weights):
                check_deadline(deadline, self.clock)
                scalar = self._asf(point, weight)
                minima[i] = scalar if minima[i] is None else min(minima[i], scalar)
            seen.add(point)
        check_deadline(deadline, self.clock)
        self._seen, self._minima = seen, tuple(minima)
        return sum(minima, Fraction(0))/len(minima)

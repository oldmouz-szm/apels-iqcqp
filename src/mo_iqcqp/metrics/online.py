"""Exact full-archive 2D HV with cooperative deadline checks."""
from fractions import Fraction
import heapq
import time
from . import vector, original_to_internal, number

def check_deadline(deadline, clock):
    if clock() >= deadline:
        raise TimeoutError('online reward deadline')

class FixedHV:
    @staticmethod
    def gain(before, after):
        return after-before

    def __init__(self, spec, model, deadline=float('inf'), clock=time.monotonic):
        self.clock = clock
        check_deadline(deadline, clock)
        required = {'schema', 'model_fingerprint', 'source_sha256', 'objective_directions',
                    'normalization', 'hv_reference_original'}
        if not isinstance(spec, dict) or set(spec) != required or spec['schema'] != 'hv-v1':
            raise ValueError('Invalid fixed HV specification')
        if (spec['model_fingerprint'] != model.fingerprint() or
            spec['source_sha256'] != model.source['sha256'] or
            spec['objective_directions'] != model.directions):
            raise ValueError('HV specification source/model/direction mismatch')
        self.dimensions = len(model.objectives)
        if not 2 <= self.dimensions <= 4:
            raise ValueError('HV supports 2--4 objectives')
        norm = spec['normalization']
        if not isinstance(norm, dict) or set(norm) != {'origin_internal', 'scale'}:
            raise ValueError('Invalid HV normalization')
        self.origin = vector(norm['origin_internal'], self.dimensions, 'origin')
        self.scale = vector(norm['scale'], self.dimensions, 'scale')
        if any(s <= 0 for s in self.scale):
            raise ValueError('HV scales must be positive')
        ref = vector(spec['hv_reference_original'], self.dimensions, 'reference')
        self.reference = self.normalize(original_to_internal(ref, model.directions))
        check_deadline(deadline, clock)

    def normalize(self, point):
        return tuple((number(x)-o)/s for x,o,s in zip(point,self.origin,self.scale))

    def value(self, archive, deadline=float('inf')):
        if self.dimensions != 2:
            raise ValueError('Online exact HV supports exactly two objectives')
        heap = []
        for point in archive.points:
            check_deadline(deadline, self.clock)
            q = self.normalize(point)
            if len(point) != 2 or any(x >= r for x,r in zip(q,self.reference)):
                raise ValueError('HV reference must be strictly worse than every point')
            heapq.heappush(heap, q)
        area = Fraction(0)
        rx, previous_y = self.reference
        while heap:
            check_deadline(deadline, self.clock)
            x,y = heapq.heappop(heap)
            if y < previous_y:
                area += (rx-x)*(previous_y-y)
                previous_y = y
        check_deadline(deadline, self.clock)
        return area

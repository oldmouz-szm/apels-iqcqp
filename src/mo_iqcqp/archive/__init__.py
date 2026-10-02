"""Exact full archive and bounded, deadline-aware representative selection."""
import time

def check(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError('archive/work selection deadline')

def dominates(a, b, deadline=float('inf')):
    if len(a) != len(b):
        raise ValueError('Dominance dimension mismatch')
    strict = False
    for i, (x, y) in enumerate(zip(a, b)):
        if i % 128 == 0: check(deadline)
        if x > y: return False
        strict = strict or x < y
    return strict

class Archive:
    def __init__(self, capacity=512):
        self.points = {}; self.capacity = capacity; self.aux = []; self.work_cursor = 0

    def insert(self, checked, deadline=float('inf')):
        return self.insert_with_reason(checked, deadline) == 'ADMITTED'

    def insert_with_reason(self, checked, deadline=float('inf')):
        if time.monotonic() >= deadline: return 'DEADLINE'
        if not checked['valid']:
            if 'violation' in checked:
                self.aux.append(checked); self.aux.sort(key=lambda p:p['violation']); self.aux=self.aux[:32]
            return 'ORIGINAL_INFEASIBLE'
        key = tuple(checked['internal'])
        if key in self.points:
            self.aux.append(checked); self.aux=self.aux[-32:]; return 'SAME_OBJECTIVES'
        removed = {}
        try:
            for old, point in self.points.items():
                check(deadline)
                if dominates(old, key, deadline):
                    self.aux.append(checked); self.aux=self.aux[-32:]; return 'DOMINATED'
                if dominates(key, old, deadline): removed[old] = point
            check(deadline)
        except TimeoutError:
            return 'DEADLINE'
        for old in removed: del self.points[old]
        self.points[key] = checked
        if time.monotonic() >= deadline:
            del self.points[key]; self.points.update(removed); return 'DEADLINE'
        return 'ADMITTED'

    def work(self, deadline=float('inf')):
        check(deadline)
        p = list(self.points.values())
        if len(p) <= self.capacity: return p
        n = len(p[0]['internal'])
        lo=[]; scale=[]; chosen=[]; selected=set()
        # min/max retain the old stable, first-in-archive tie behavior.
        for j in range(n):
            check(deadline)
            minimum=maximum=0
            for i in range(1,len(p)):
                if i % 128 == 0: check(deadline)
                if p[i]['internal'][j] < p[minimum]['internal'][j]: minimum=i
                if p[i]['internal'][j] > p[maximum]['internal'][j]: maximum=i
            low=p[minimum]['internal'][j]
            lo.append(low); scale.append(max(1,p[maximum]['internal'][j]-low))
            for i in (minimum,maximum):
                if i not in selected: chosen.append(i); selected.add(i)
        if n > 4 and len(chosen) > self.capacity:
            offset=self.work_cursor % len(chosen)
            result=[p[i] for i in (chosen[offset:]+chosen[:offset])[:self.capacity]]
            check(deadline); self.work_cursor+=self.capacity
            return result
        def distance(i,j):
            total=0.
            for k,(a,b,s) in enumerate(zip(p[i]['internal'],p[j]['internal'],scale)):
                if k % 128 == 0: check(deadline)
                total+=float((a-b)/s)**2
            return total
        # Each pair is evaluated at most once; same metric and stable max ties.
        nearest={}
        if len(chosen)<self.capacity:
            for i in range(len(p)):
                check(deadline)
                if i not in selected: nearest[i]=min(distance(i,j) for j in chosen)
        while len(chosen)<self.capacity:
            check(deadline)
            best=max(nearest,key=nearest.get)
            chosen.append(best); selected.add(best); del nearest[best]
            if len(chosen)<self.capacity:
                for i in nearest:
                    check(deadline); nearest[i]=min(nearest[i],distance(i,best))
        check(deadline)
        return [p[i] for i in chosen[:self.capacity]]

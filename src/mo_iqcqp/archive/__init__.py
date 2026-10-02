"""Exact dominance; no tolerance/grid rounding in the full archive."""
import time

def dominates(a,b):return all(x<=y for x,y in zip(a,b)) and any(x<y for x,y in zip(a,b))

class Archive:
    def __init__(self, capacity=512):
        self.points={};self.capacity=capacity;self.aux=[];self.work_cursor=0

    def insert(self, checked, deadline=float('inf')):
        return self.insert_with_reason(checked, deadline)=='ADMITTED'

    def insert_with_reason(self, checked, deadline=float('inf')):
        if time.monotonic()>=deadline:return 'DEADLINE'
        if not checked['valid']:
            if 'violation' in checked:
                self.aux.append(checked);self.aux.sort(key=lambda p:p['violation']);self.aux=self.aux[:32]
            return 'ORIGINAL_INFEASIBLE'
        key=tuple(checked['internal'])
        if key in self.points:
            self.aux.append(checked);self.aux=self.aux[-32:];return 'SAME_OBJECTIVES'
        if any(dominates(k,key) for k in self.points):
            self.aux.append(checked);self.aux=self.aux[-32:];return 'DOMINATED'
        removed={k:v for k,v in self.points.items() if dominates(key,k)}
        if time.monotonic()>=deadline:return 'DEADLINE'
        for k in removed:del self.points[k]
        self.points[key]=checked
        if time.monotonic()>=deadline:
            del self.points[key];self.points.update(removed);return 'DEADLINE'
        return 'ADMITTED'

    def work(self):
        p=list(self.points.values())
        if len(p)<=self.capacity:return p
        # Farthest-point subset, preserves extrema before filling diversity.
        n=len(p[0]['internal']);lo=[min(x['internal'][j] for x in p) for j in range(n)];scale=[max(1,max(x['internal'][j] for x in p)-lo[j]) for j in range(n)]
        chosen=[]
        for j in range(n):
            for reverse in (False,True):
                point=sorted(p,key=lambda x:x['internal'][j],reverse=reverse)[0]
                if point not in chosen:chosen.append(point)
        if n>4 and len(chosen)>self.capacity:
            offset=self.work_cursor%len(chosen)
            self.work_cursor+=self.capacity
            return (chosen[offset:]+chosen[:offset])[:self.capacity]
        while len(chosen)<self.capacity:
            point=max((x for x in p if x not in chosen),key=lambda x:min(sum(float((a-b)/s)**2 for a,b,s in zip(x['internal'],y['internal'],scale)) for y in chosen))
            chosen.append(point)
        return chosen[:self.capacity]

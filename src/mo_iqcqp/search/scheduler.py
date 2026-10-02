"""Fixed measured-time shares with gap/grid epsilon scheduling."""
import math
import random
import time
from fractions import Fraction as F

class Scheduler:
    def __init__(self,n,seed=1,config=None):
        self.n=n;self.rng=random.Random(seed);self.turn=0;self.direction_turn=0;self.epsilon_turn=0;self.unit_cursor=0;self.feasibility_turn=0;self.feasibility_unit_cursor=0;self.version=0;self.last_scale=None
        self.elapsed={'direction':0.,'epsilon':0.,'pls':0.,'feasibility':0.}
        self.eligible=();self.baseline=dict(self.elapsed)
        self.shares={'direction':.3,'epsilon':.5,'pls':.2}
        self.attempts={}
        config=config or {};self.bootstrap_strategy=config.get('bootstrap_strategy','legacy_v1');self.rho=float(config.get('rho',.001))
        if 'shares' in config:
            shares=config['shares']
            if set(shares)!=set(self.shares) or any(not math.isfinite(v) or v<0 for v in shares.values()) or not 0<sum(shares.values())<float('inf'):raise ValueError('Invalid time shares')
            self.shares=(dict(shares) if config.get('configuration_schema') in ('apels-effective-v1','apels-effective-v2')
                         else {k:float(v)/sum(shares.values()) for k,v in shares.items()})
    def next(self,archive,reserved=None,deadline=float('inf')):
        points=archive.work(deadline);n=self.n;t=self.turn;self.turn+=1
        # Epsilon needs two distinct full-archive witnesses, even at work_capacity=1.
        if len(points)==1 and len(archive.points)>1:
            points.append(next(p for p in archive.points.values() if p['internal']!=points[0]['internal']))
        eligible=self._eligible(points)
        if eligible!=self.eligible:
            # No debt accrues while a class cannot run. Keep lifetime totals for reporting.
            self.eligible=eligible;self.baseline=dict(self.elapsed)
        if not eligible:return None
        reserved=reserved or {}
        kind,effective=self._select(eligible,reserved)
        z=[];scale=[]
        for j in range(n):
            if time.monotonic()>=deadline:raise TimeoutError('task construction deadline')
            low=min(p['internal'][j] for p in points) if points else 0
            z.append(low);scale.append(max(1,max(p['internal'][j] for p in points)-low) if points else 1)
        if (z,scale)!=self.last_scale:self.version+=1;self.last_scale=(z,scale)
        eps=[None]*n;seed=None;gap_key=None
        if kind in ('direction','feasibility'):
            if kind=='direction':
                direction_turn=self.direction_turn;self.direction_turn+=1
                if direction_turn<2*n or direction_turn%2==0:
                    unit=self.unit_cursor%n;self.unit_cursor+=1
                    w=[float(unit==j) for j in range(n)]
                else:
                    w=[self.rng.random()+.01 for _ in range(n)]
            else:
                if self.bootstrap_strategy=='legacy_v1':
                    w=[float(t%n==j) for j in range(n)] if t<2*n or t%2==0 else [self.rng.random()+.01 for j in range(n)]
                else:
                    ft=self.feasibility_turn;self.feasibility_turn+=1
                    if ft<2*n or ft%2==0:
                        unit=self.feasibility_unit_cursor%n;self.feasibility_unit_cursor+=1
                        w=[float(unit==j) for j in range(n)]
                    else:w=[self.rng.random()+.01 for j in range(n)]
            weights=[w[j]/float(scale[j]) for j in range(n)]
            if points:seed=self.rng.choice(points)['x']
            elif archive.aux and t%3:seed=min(archive.aux,key=lambda a:a.get('violation',0))['x']
        elif kind=='epsilon':
            if n==2:
                p=sorted(points,key=lambda x:x['internal'][0]);gaps=[]
                for a,b in zip(p,p[1:]):
                    u,v=a['internal'],b['internal'];key=(tuple(u),tuple(v))
                    if u[0]<v[0] and u[1]>v[1]:gaps.append((float((v[0]-u[0])*(u[1]-v[1])/(scale[0]*scale[1]))/(1+self.attempts.get(key,0)),key,a,b))
                if not gaps:
                    raise ValueError('Nondominated biobjective archive has no valid gap')
                else:
                    _,gap_key,a,b=max(gaps,key=lambda x:x[0]);count=self.attempts.get(gap_key,0);self.attempts[gap_key]=count+1
                    primary=count%2;other=1-primary
                    frac=[F(1,2),F(3,10),F(7,10)][count//2%3]
                    eps[other]=a['internal'][other]+(b['internal'][other]-a['internal'][other])*frac
                    seed=(b if primary==0 else a)['x'];weights=[(1 if j==primary else self.rho)/float(scale[j]) for j in range(n)]
            else:
                grid={}
                for a in points:
                    cell=tuple(math.floor(float((a['internal'][j]-z[j])/scale[j])*20) for j in range(n));grid.setdefault(cell,[]).append(a)
                sparse=min(grid,key=lambda c:(len(grid[c]),self.rng.random()));a=self.rng.choice(grid[sparse])
                legacy_grid=n<=4 and self.bootstrap_strategy=='legacy_v1'
                rotation=t if legacy_grid else self.epsilon_turn
                if not legacy_grid:self.epsilon_turn+=1
                primary=rotation%n;q=(primary+1+rotation//n%(n-1))%n;eta=[F(1,100),F(3,100),F(1,10)][t%3]
                eps=[None if j==primary else a['internal'][j]+(-eta if j==q else eta)*scale[j] for j in range(n)]
                seed=min(points,key=lambda a:sum(max(0,float(a['internal'][j]-eps[j]))/float(scale[j]) for j in range(n) if eps[j] is not None))['x']
                weights=[(1 if j==primary else self.rho)/float(scale[j]) for j in range(n)]
        else:
            weights=[1/float(s) for s in scale];seed=self.rng.choice(points)['x']
        reason='empty_archive' if not archive.points else 'second_witness' if kind=='feasibility' else None
        if reason and hasattr(self,'last_decision') and self.last_decision is not None:
            self.last_decision['forced_reason']=reason
        result={'kind':kind,'effective_shares':effective,'weights':weights,'eps':eps,'seed':seed,'z':z,'scale':scale,'scale_version':self.version,'gap':repr(gap_key) if gap_key else None}
        if self.bootstrap_strategy!='legacy_v1' or reason=='second_witness':result['bootstrap_reason']=reason
        if time.monotonic()>=deadline:raise TimeoutError('task construction deadline')
        return result

    def _eligible(self, points):
        if not points:return ('feasibility',)
        eligible=tuple(k for k,v in self.shares.items()
                       if v>0 and (k!='epsilon' or len(points)>=2))
        # Timed witness acquisition is not an enabled epsilon task or learning arm.
        return eligible if eligible else ('feasibility',)

    def _select(self, eligible, reserved):
        kind=min(eligible,key=lambda k:(self.elapsed[k]-self.baseline[k]+reserved.get(k,0))/(self.shares.get(k,1)))
        total=sum(self.shares.get(k,1) for k in eligible)
        effective={k:self.shares.get(k,1)/total for k in eligible}
        return kind,effective

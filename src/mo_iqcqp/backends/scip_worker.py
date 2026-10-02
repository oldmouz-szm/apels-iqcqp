"""One persistent process per SCIP worker; rebuilds each frozen task model."""
import hashlib
import json
import math
from fractions import Fraction
import sys
import time

from mo_iqcqp.io import load_lp


def finite(value):
    converted=float(Fraction(value)) if isinstance(value,str) else float(value)
    if not math.isfinite(converted):raise ValueError('NONFINITE_SCIP_VALUE')
    return converted


class Worker:
    def __init__(self):
        self.source=None;self.seed=1;self.task=None;self.warm=None;self.candidates=[]
        self.tasks=0;self.nodes=0

    def solve(self,seconds):
        from pyscipopt import Model,quicksum
        start=time.monotonic()
        if self.source is None or self.task is None:raise ValueError('MODEL_OR_TASK_REQUIRED')
        model=Model('apels-scip-task');model.hideOutput()
        model.setRealParam('limits/time',max(.001,seconds))
        model.setIntParam('parallel/maxnthreads',1)
        model.setIntParam('randomization/randomseedshift',self.seed%(2**31-1))
        infinity=model.infinity()
        variables=[]
        for i,v in enumerate(self.source.variables):
            lower=-infinity if v.lower is None else finite(v.lower)
            upper=infinity if v.upper is None else finite(v.upper)
            variables.append(model.addVar(name='x'+str(i),vtype='B' if v.kind=='B' else 'I',lb=lower,ub=upper))
        def expr(e):
            terms=[]
            for key,c in e.terms.items():
                value=finite(c)
                for j in key:value=value*variables[j]
                terms.append(value)
            return quicksum(terms)
        objectives=[expr(e.scaled(1 if d=='min' else -1)) for e,d in zip(self.source.objectives,self.source.directions)]
        for i,c in enumerate(self.source.constraints):
            lhs=expr(c.expr);rhs=finite(c.rhs)
            relation=lhs<=rhs if c.sense=='<=' else lhs>=rhs if c.sense=='>=' else lhs==rhs
            model.addCons(relation,name='original'+str(i))
        for j,epsilon in enumerate(self.task['eps']):
            if epsilon is not None:model.addCons(objectives[j]<=finite(epsilon),name='epsilon'+str(j))
        tau=model.addVar(name='task_value',vtype='C',lb=-infinity)
        scalar=quicksum(finite(w)*e for w,e in zip(self.task['weights'],objectives))
        model.addCons(scalar<=tau,name='weighted_objective')
        model.setObjective(tau,'minimize')
        warm_start=dict(status='not_requested',reason=None)
        if self.warm is not None:
            if len(self.warm)!=len(variables):raise ValueError('WARM_DIMENSION')
            candidate=model.createSol()
            for var,value in zip(variables,self.warm):model.setSolVal(candidate,var,finite(value))
            # Evaluate the actual SCIP expression, including mixed objective signs.
            scalar_value=finite(model.getSolVal(candidate,scalar))
            model.setSolVal(candidate,tau,scalar_value)
            feasible=model.checkSol(candidate,printreason=False,completely=True)
            if feasible:
                stored=model.addSol(candidate,free=True)
                warm_start=dict(status='accepted' if stored else 'not_stored',
                                reason=None if stored else 'not_better_than_stored_solution',
                                task_value=scalar_value,subproblem_feasible=True)
            else:
                model.freeSol(candidate)
                warm_start=dict(status='rejected',reason='SCIP_SUBPROBLEM_INFEASIBLE',
                                task_value=scalar_value,subproblem_feasible=False)
        model.optimize()
        self.tasks+=1;self.nodes+=model.getNNodes()
        solution=model.getBestSol()
        if solution is not None:
            x=[model.getSolVal(solution,var) for var in variables]
            if all(abs(v-round(v))<=1e-6 for v in x):self.candidates.append([int(round(v)) for v in x])
        self.candidates=self.candidates[-48:]
        return dict(status='SLICE_DONE',steps=self.nodes,slice_steps=model.getNNodes(),
                    stop_reason=str(model.getStatus()),candidate_count=len(self.candidates),
                    slice_elapsed_ms=(time.monotonic()-start)*1000,warm_start=warm_start)

    def command(self,item):
        command=item['command']
        if command=='LOAD':
            source=load_lp(item['path'])
            if source.source['sha256']!=item['sha256']:raise ValueError('MODEL_SOURCE_CHANGED')
            self.source=source;return {'status':'LOADED'}
        if command=='SEED':self.seed=int(item['seed']);return {'status':'SEEDED'}
        if command=='TASK':self.task=item;self.candidates=[];return {'status':'TASK_SET'}
        if command=='WARM':self.warm=item['x'];return {'status':'WARM_STARTED'}
        if command=='SLICE':return self.solve(finite(item['seconds']))
        if command=='COLLECT':
            out=self.candidates;self.candidates=[];return {'status':'CANDIDATES','x':out}
        if command=='STATS':return {'status':'STATS','tasks':self.tasks,'steps':self.nodes}
        if command=='CLOSE':return {'status':'CLOSED'}
        raise ValueError('UNKNOWN_COMMAND')


def main():
    from mo_iqcqp.experiment.lifecycle import arm_parent_death_guard
    arm_parent_death_guard()
    worker=Worker()
    for line in sys.stdin:
        command=None
        try:
            command=json.loads(line)
            result=worker.command(command)
        except Exception as error:
            result={'status':'ERROR','error':type(error).__name__+': '+str(error)}
        print(json.dumps(result,separators=(',',':'),allow_nan=False),flush=True)
        if isinstance(command,dict) and command.get('command')=='CLOSE':break

if __name__=='__main__':main()

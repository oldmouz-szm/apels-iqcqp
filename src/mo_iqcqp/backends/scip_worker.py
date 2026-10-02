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

    def solve(self,seconds,deadline=float('inf'),stop_on_feasible=False):
        start=time.monotonic()
        end=min(deadline,start+seconds)
        from pyscipopt import Model,quicksum,Eventhdlr,SCIP_EVENTTYPE
        def check():
            if time.monotonic()>=end:raise TimeoutError('SCIP task deadline')
        return self._solve_bounded(start,end,check,stop_on_feasible,Model,quicksum,Eventhdlr,SCIP_EVENTTYPE)

    def _solve_bounded(self,start,end,check,stop_on_feasible,Model,quicksum,Eventhdlr,SCIP_EVENTTYPE):
        try:
            return self._solve(start,end,check,stop_on_feasible,Model,quicksum,Eventhdlr,SCIP_EVENTTYPE)
        except TimeoutError:
            return dict(status='SLICE_DONE',steps=self.nodes,slice_steps=0,
                        stop_reason='TASK_DEADLINE_DURING_SETUP',candidate_count=len(self.candidates),
                        slice_elapsed_ms=(time.monotonic()-start)*1000,
                        search_state_continued=False,model_rebuilt=True)

    def _solve(self,start,end,check,stop_on_feasible,Model,quicksum,Eventhdlr,SCIP_EVENTTYPE):
        check()
        if self.source is None or self.task is None:raise ValueError('MODEL_OR_TASK_REQUIRED')
        model=Model('apels-scip-task');model.hideOutput()
        model.setRealParam('limits/time',max(.001,end-time.monotonic()))
        model.setIntParam('parallel/maxnthreads',1)
        model.setIntParam('randomization/randomseedshift',self.seed%(2**31-1))
        infinity=model.infinity()
        variables=[]
        for i,v in enumerate(self.source.variables):
            check()
            lower=-infinity if v.lower is None else finite(v.lower)
            upper=infinity if v.upper is None else finite(v.upper)
            variables.append(model.addVar(name='x'+str(i),vtype='B' if v.kind=='B' else 'I',lb=lower,ub=upper))
        def expr(e):
            terms=[]
            for key,c in e.terms.items():
                check()
                value=finite(c)
                for j in key:value=value*variables[j]
                terms.append(value)
            return quicksum(terms)
        objectives=[expr(e.scaled(1 if d=='min' else -1)) for e,d in zip(self.source.objectives,self.source.directions)]
        for i,c in enumerate(self.source.constraints):
            check()
            lhs=expr(c.expr);rhs=finite(c.rhs)
            relation=lhs<=rhs if c.sense=='<=' else lhs>=rhs if c.sense=='>=' else lhs==rhs
            model.addCons(relation,name='original'+str(i))
        for j,epsilon in enumerate(self.task['eps']):
            check()
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
        check()
        worker=self
        from pyscipopt import SCIP_STAGE
        class FirstCandidate(Eventhdlr):
            def eventinit(self):self.model.catchEvent(SCIP_EVENTTYPE.BESTSOLFOUND,self)
            def eventexit(self):self.model.dropEvent(SCIP_EVENTTYPE.BESTSOLFOUND,self)
            def eventexec(self,event):
                try:
                    sol=self.model.getBestSol()
                    if sol is None:return
                    values=[self.model.getSolVal(sol,v) for v in variables]
                    if all(abs(v-round(v))<=1e-6 for v in values):
                        key=tuple(int(round(v)) for v in values)
                        if (key!=getattr(worker,'last_bootstrap_export',None) and
                            self.model.getStage() in (SCIP_STAGE.PRESOLVING,SCIP_STAGE.SOLVING)):
                            self.model.interruptSolve()
                except Exception as exc:
                    worker.callback_error=str(exc)
        self.callback_error=None
        if stop_on_feasible:
            if getattr(self,'last_bootstrap_export',None) is None:model.setIntParam('limits/solutions',1)
            model.includeEventhdlr(FirstCandidate(),'first_candidate','return a new tentative bootstrap candidate')
        model.setRealParam('limits/time',max(.000001,end-time.monotonic()))
        model.optimize()
        if self.callback_error:raise RuntimeError('SCIP_EVENT_CALLBACK: '+self.callback_error)
        self.tasks+=1;self.nodes+=model.getNNodes()
        solution=model.getBestSol()
        if solution is not None:
            x=[model.getSolVal(solution,var) for var in variables]
            if all(abs(v-round(v))<=1e-6 for v in x):
                candidate=[int(round(v)) for v in x]
                self.candidates.append(candidate)
                self.warm=candidate
                if stop_on_feasible:self.last_bootstrap_export=tuple(candidate)
        self.candidates=self.candidates[-48:]
        return dict(status='SLICE_DONE',steps=self.nodes,slice_steps=model.getNNodes(),
                    stop_reason=str(model.getStatus()),candidate_count=len(self.candidates),
                    slice_elapsed_ms=(time.monotonic()-start)*1000,warm_start=warm_start,
                    search_state_continued=False,model_rebuilt=True,bootstrap_candidate_stop=stop_on_feasible)


    def neighbors(self,seconds,limit,offset,deadline=float('inf')):
        if self.source is None or self.task is None or self.warm is None:
            raise ValueError('MODEL_TASK_AND_WARM_REQUIRED')
        if type(limit) is not int or limit<0 or type(offset) is not int or offset<0:
            raise ValueError('INVALID_NEIGHBOR_LIMIT')
        start=time.monotonic();end=min(deadline,start+seconds);base=list(self.warm)
        self.candidates=[]
        for k in range(min(limit,2*len(base))):
            if time.monotonic()>=end:break
            op=(k+offset)%(2*len(base));i=op//2
            value=base[i]+(-1 if op%2 else 1);var=self.source.variables[i]
            if (var.lower is not None and value<var.lower) or (var.upper is not None and value>var.upper):continue
            candidate=list(base);candidate[i]=value;self.candidates.append(candidate)
        return dict(status='NEIGHBORS_DONE',count=len(self.candidates),
                    search_operator='integer_unit_neighborhood',
                    elapsed=time.monotonic()-start)

    def command(self,item):
        command=item['command']
        if command=='LOAD':
            from mo_iqcqp.experiment.resources import input_limits
            source=load_lp(item['path'],deadline=item.get('deadline',float('inf')),
                           **input_limits(item.get('input_limits')))
            if source.source['sha256']!=item['sha256']:raise ValueError('MODEL_SOURCE_CHANGED')
            self.source=source;return {'status':'LOADED'}
        if command=='SEED':self.seed=int(item['seed']);return {'status':'SEEDED'}
        if command=='TASK':
            if self.source is None or len(item['weights'])!=len(self.source.objectives) or len(item['eps'])!=len(self.source.objectives):
                raise ValueError('TASK_DIMENSION')
            for v in item['weights']:finite(v)
            for v in item['eps']:
                if v is not None:finite(v)
            self.task=item;self.candidates=[];return {'status':'TASK_SET'}
        if command=='WARM':
            if self.source is None or len(item['x'])!=len(self.source.variables):raise ValueError('WARM_DIMENSION')
            if any(Fraction(str(v)).denominator!=1 for v in item['x']):raise ValueError('WARM_NONINTEGER')
            self.warm=list(item['x']);return {'status':'WARM_STARTED'}
        if command=='SLICE':return self.solve(finite(item['seconds']),item.get('deadline',float('inf')),item.get('stop_on_feasible',False))
        if command=='NEIGHBORS':return self.neighbors(finite(item['seconds']),item['limit'],item['offset'],item.get('deadline',float('inf')))
        if command=='COLLECT':
            out=self.candidates;self.candidates=[];return {'status':'CANDIDATES','x':out}
        if command=='STATS':return {'status':'STATS','tasks':self.tasks,'steps':self.nodes}
        if command=='CLOSE':return {'status':'CLOSED'}
        raise ValueError('UNKNOWN_COMMAND')


def main():
    from mo_iqcqp.experiment.lifecycle import arm_parent_death_guard
    arm_parent_death_guard()
    if sys.argv[1:]==['--fingerprint']:
        from .identity import scip_runtime
        print(json.dumps(scip_runtime(),sort_keys=True,allow_nan=False))
        return
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

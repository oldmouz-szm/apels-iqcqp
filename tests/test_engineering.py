"""Temporary fixtures only: no benchmark data or generated results in source."""
import fcntl
import itertools
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from fractions import Fraction as F
import random
import resource

from mo_iqcqp.backends.native import NativeSession,num
from mo_iqcqp.experiment.batch import prepare,queue
from mo_iqcqp.experiment.results import validate_samples
from mo_iqcqp.experiment.resources import INPUT_DEFAULTS,preparation_memory_limit
from mo_iqcqp.experiment.runner import supervise
from mo_iqcqp.io import load_lp
from mo_iqcqp.metrics.normalization import make_spec,objective_intervals
from mo_iqcqp.model import Model,Variable,Expr

class Engineering(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='apels-test-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def lp(self,text,name='model.lp'):
        path=self.root/name;path.write_text(text);return path
    def solve(self,path,budget=1.2,config=None):
        model=load_lp(path)
        cfg=dict(scheduler_mode='adaptive_r2',r2_spec=make_spec(model))
        cfg.update(config or {})
        return supervise(path,budget=budget,algorithm_config=cfg,output=self.root/(path.stem+'.json'))
    def test_degenerate_models(self):
        for name,text,count,stop in [
            ('constant','Minimize\n f: 0\n g: x\nSubject To\n c: x >= 0\nBinary\n x\nEnd\n',1,None),
            ('constant_repair','Minimize\n f: 0\n g: 0\nSubject To\n c: x >= 1\nBinary\n x\nEnd\n',1,None),
            ('infeasible','Minimize\n f: x\n g: - x\nSubject To\n c: 0 >= 1\nBinary\n x\nEnd\n',0,'CONSTANT_CONSTRAINT_INFEASIBLE'),
            ('no_variables','Minimize\n f: 2\n g: 3\nEnd\n',1,'CONSTANT_MODEL')]:
            with self.subTest(name=name):
                result=self.solve(self.lp(text,name+'.lp'))
                self.assertEqual(result['status'],'COMPLETED',result.get('error'))
                self.assertEqual(len(result['archive']),count)
                if stop:self.assertEqual(result['stop_reason'],stop)
    def test_integer_negative_linear(self):
        path=self.lp('Minimize\n f: - x\n g: 0\nBounds\n 0 <= x <= 5\nGeneral\n x\nEnd\n')
        result=self.solve(path)
        self.assertEqual(result['status'],'COMPLETED',result.get('error'))
        self.assertEqual(result['archive'][0]['x'],[5])
    def test_many_objective_assignments(self):
        for m in (2,3,5,10,50,200):
            with self.subTest(m=m):
                coefficients=[[1,2,3,4],[-1,-2,-3,-4]]
                coefficients += [[((j+2)*(i+3))%11-5 for i in range(4)] for j in range(m-2)]
                lines=['Minimize']
                for j,row in enumerate(coefficients):
                    lines.append(' f'+str(j)+': '+' '.join(('+' if a>=0 else '-')+' '+str(abs(a))+' x'+str(i) for i,a in enumerate(row)))
                lines+=['Binary',' x0 x1 x2 x3','End']
                path=self.lp('\n'.join(lines)+'\n',f'm{m}.lp');result=self.solve(path,budget=1.5)
                self.assertEqual(result['status'],'COMPLETED',result.get('error'))
                self.assertTrue(result['archive'])
                vectors=[]
                for point in result['archive']:
                    x=point['x'];self.assertTrue(all(type(v) is int and v in (0,1) for v in x))
                    exact=[sum(a*v for a,v in zip(row,x)) for row in coefficients]
                    self.assertEqual(point['original'],exact);vectors.append(exact)
                    self.assertLess(point['validated_by_elapsed'],result['budget'])
                for a,b in itertools.permutations(vectors,2):self.assertFalse(all(x<=y for x,y in zip(a,b)))
    def test_queue_failure_resume_and_lock(self):
        path=self.lp('Minimize\n f: x\n g: - x\nBinary\n x\nEnd\n')
        manifest=self.root/'queue.json';prepare(path,manifest,budget=.5)
        cfg=json.loads(manifest.read_text());cfg['runs'].insert(0,dict(path='missing.lp'))
        manifest.write_text(json.dumps(cfg));out=self.root/'results'
        first=queue(manifest,out)
        self.assertEqual(first['status'],'COMPLETED_WITH_ERRORS');self.assertEqual(first['completed'],1)
        second=queue(manifest,out)
        self.assertTrue(second['runs'][1]['reused'])
        self.assertEqual(second['runs'][1]['output'],first['runs'][1]['output'])
        with (out/'.queue.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'already in use'):queue(manifest,out)
        cfg['runs'][1]['budget']=.6;manifest.write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'different queue'):queue(manifest,out)
    def test_fractional_bounds_and_exact_transport(self):
        path=self.lp('Minimize\n f: x\n g: - x\nBounds\n .5 <= x <= 1.5\nGeneral\n x\nEnd\n')
        model=load_lp(path)
        with NativeSession(time.monotonic()+5) as native:
            native.load_model(model);native.set_task([1,0],warm=[1]);native.run_slice(.03)
            self.assertTrue(all(x==[1] for x in native.collect_candidates()))
        self.assertEqual(num(2**53+1),str(2**53+1))
        with self.assertRaisesRegex(ValueError,'finite binary64'):num(10**5000)
        large=2**53+1
        path=self.lp(f'Minimize\n f: x\n g: - x\nBounds\n x = {large}\nGeneral\n x\nEnd\n','large.lp')
        with NativeSession(time.monotonic()+5) as native:
            native.load_model(load_lp(path));native.set_task([1,0]);native.run_slice(.01)
            self.assertEqual(native.collect_candidates(),[[large]])
    def test_combined_task_warm_has_one_reset(self):
        path=self.lp('Minimize\n f: x\n g: - x\nBinary\n x\nEnd\n')
        with NativeSession(time.monotonic()+5) as native:
            native.load_model(load_lp(path));native.set_task([1,0],warm=[1]);stats=native.run_slice(.01,steps=1)
            self.assertEqual(stats['reset_count'],1)
            native.set_task([0,1],warm=[0]);stats=native.run_slice(.01,steps=1)
            self.assertEqual(stats['reset_count'],2)
    def test_interrupt_keeps_valid_checkpoint(self):
        path=self.lp('Minimize\n f: x\n g: - x\nBinary\n x\nEnd\n')
        config=self.root/'config.json';config.write_text(json.dumps(dict(r2_spec=make_spec(load_lp(path)))))
        output=self.root/'interrupted.json'
        with tempfile.TemporaryFile() as log:
            child=subprocess.Popen([sys.executable,'-m','mo_iqcqp','run',str(path),'--budget','30',
                                    '--algorithm-config',str(config),'--output',str(output)],stdout=log,stderr=log)
            try:
                end=time.monotonic()+8
                while not Path(str(output)+'.checkpoint').exists() and time.monotonic()<end and child.poll() is None:time.sleep(.02)
                self.assertIsNone(child.poll());self.assertTrue(Path(str(output)+'.checkpoint').exists())
                child.send_signal(signal.SIGINT);self.assertEqual(child.wait(timeout=8),130)
                result=json.loads(output.read_text());self.assertEqual(result['status'],'INTERRUPTED')
                self.assertTrue(result['archive']);self.assertTrue(validate_samples(result,path,INPUT_DEFAULTS))
            finally:
                if child.poll() is None:child.kill();child.wait()
    def test_preparation_keeps_errors_and_explicit_units(self):
        path=self.lp('Minimize\n f: x\n g: - x\nGeneral\n x\nEnd\n')
        self.lp('unsupported input','bad.lp')
        manifest=self.root/'queue.json'
        report=prepare(self.root,manifest,normalization='coefficient',seeds=[1,2])
        self.assertEqual(report['runs'],2);self.assertEqual(len(report['preparation_errors']),1)
        cfg=json.loads(manifest.read_text());spec=cfg['runs'][0]['algorithm_config']['r2_spec']
        self.assertEqual(spec['normalization_provenance']['coordinates'][0]['lower_bound_status'],'unproved')
        with self.assertRaises(FileExistsError):prepare(path,manifest)
    def test_exact_interval_integer_fast_path(self):
        rng=random.Random(41)
        for case in range(50):
            variables=[Variable(str(i),'I',-2,3) for i in range(3)]
            objectives=[]
            for j in range(3):
                expr=Expr();expr.add((),rng.randrange(-10,11))
                for i in range(3):
                    expr.add((i,),F(rng.randrange(-20,21),rng.choice([1,1,2,5])))
                    for k in range(i,3):expr.add((i,k),F(rng.randrange(-20,21),rng.choice([1,1,3])))
                objectives.append(expr)
            model=Model(variables,objectives,['min','max','min'],[])
            intervals=objective_intervals(model)
            expected=[]
            for expr,sense in zip(objectives,model.directions):
                lo=hi=F(0)
                for key,a in expr.terms.items():
                    domain=sorted(set(key))
                    values=[]
                    for x in itertools.product(range(-2,4),repeat=len(domain)):
                        assignment=dict(zip(domain,x));product=F(a)
                        for i in key:product*=assignment[i]
                        values.append(product)
                    lo+=min(values);hi+=max(values)
                expected.append((lo,hi) if sense=='min' else (-hi,-lo))
            self.assertEqual(intervals,expected)
    def test_exact_parser_coefficients(self):
        path=self.lp('Minimize\n f: 7 + 3 x + [ 2 x ^ 2 - 3 x * y ] / 2\n g: 1e-3 x - .25 y\nBinary\n x y\nEnd\n')
        model=load_lp(path)
        for x,y in itertools.product((0,1),repeat=2):
            self.assertEqual(model.objectives[0].evaluate([x,y]),7+3*x+x*x-F(3,2)*x*y)
            self.assertEqual(model.objectives[1].evaluate([x,y]),F(1,1000)*x-F(1,4)*y)
    def test_preparation_limit_restored_on_failure(self):
        previous=resource.getrlimit(resource.RLIMIT_AS)
        with self.assertRaises(MemoryError):
            with preparation_memory_limit(256):
                self.assertLessEqual(resource.getrlimit(resource.RLIMIT_AS)[0],256*1024**2)
                raise MemoryError('synthetic allocation failure')
        self.assertEqual(resource.getrlimit(resource.RLIMIT_AS),previous)

if __name__=='__main__':unittest.main()

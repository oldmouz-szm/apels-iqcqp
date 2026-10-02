"""Exact sparse mathematical model, independent of the native search state."""
from dataclasses import dataclass, field
from fractions import Fraction as F
import hashlib
import json
import time

SCHEMA = 'canonical-monomials-v1'

@dataclass
class Expr:
    terms: dict = field(default_factory=dict)  # () constant; (i,) linear; (i,j) quadratic

    def add(self, key, value):
        key = tuple(sorted(key))
        value = self.terms.get(key, 0) + value
        if isinstance(value,F) and value.denominator==1:value=value.numerator
        if value: self.terms[key] = value
        else: self.terms.pop(key, None)

    def evaluate(self, x, deadline=float('inf')):
        value = 0
        for count, (key, coeff) in enumerate(self.terms.items()):
            if count % 1024 == 0 and time.monotonic() >= deadline: raise TimeoutError('evaluation deadline')
            product = coeff
            for i in key: product *= x[i]
            value += product
        return value

    def scaled(self, scale):
        return Expr({k:v*scale for k,v in self.terms.items() if v*scale})

    def serial(self): return [[list(k), str(v)] for k,v in sorted(self.terms.items())]

@dataclass
class Variable:
    name: str
    kind: str = 'CONTINUOUS'
    lower: object = 0
    upper: object = None

@dataclass
class Constraint:
    name: str
    expr: Expr
    sense: str
    rhs: object
    origin: str = 'original'

@dataclass
class Model:
    variables: list
    objectives: list
    directions: list
    constraints: list
    source: dict = field(default_factory=dict)
    structure: dict = field(default_factory=lambda: {'status':'UNKNOWN','variant':'Generic'})

    def fingerprint(self):
        payload = [SCHEMA, [(v.name,v.kind,str(v.lower),str(v.upper)) for v in self.variables],
                   [e.serial() for e in self.objectives], self.directions,
                   [(c.name,c.expr.serial(),c.sense,str(c.rhs),c.origin) for c in self.constraints]]
        return hashlib.sha256(json.dumps(payload,separators=(',',':')).encode()).hexdigest()

    def validate(self, x, names=None, deadline=float('inf')):
        if names is not None and list(names) != [v.name for v in self.variables]: return {'valid':False,'reason':'VARIABLE_MAPPING'}
        if len(x)!=len(self.variables): return {'valid':False,'reason':'DIMENSION'}
        values=[]
        for v,y in zip(self.variables,x):
            # Native JSON emits exact integral assignments as Python ints. Keep
            # the Fraction conversion for every other representation, including
            # floats and strings, so their existing exact decimal semantics stay.
            if type(y) is not int:
                try: y=F(str(y))
                except (ValueError,ZeroDivisionError): return {'valid':False,'reason':'NONFINITE'}
                if y.denominator!=1:return {'valid':False,'reason':'NONINTEGER'}
            if (v.lower is not None and y<v.lower) or (v.upper is not None and y>v.upper):return {'valid':False,'reason':'BOUNDS'}
            values.append(int(y))
        violation=F(0)
        for c in self.constraints:
            value=c.expr.evaluate(values,deadline)-c.rhs
            violation+=abs(value) if c.sense=='=' else max(0,value if c.sense=='<=' else -value)
        original=[e.evaluate(values,deadline) for e in self.objectives]
        internal=[v if d=='min' else -v for v,d in zip(original,self.directions)]
        return {'valid':violation==0,'reason':'VALID' if violation==0 else 'ORIGINAL_INFEASIBLE', 'violation':violation,'original':original,'internal':internal,'x':values}

def from_matrix(matrix):
    """Explicit x^T Q x conversion, including nonsymmetric Q."""
    e=Expr()
    for i,row in enumerate(matrix):
        for j,value in enumerate(row):e.add((i,j),F(str(value)))
    return e

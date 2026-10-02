"""Prove assignment structure from coefficients; never infer it from directories."""
import math
from fractions import Fraction as F
from . import Expr

def identify(model):
    binary={i for i,v in enumerate(model.variables) if v.kind=='B'};n=math.isqrt(len(binary))
    if n<2 or n*n!=len(binary):return None
    eq=[]
    for c in model.constraints:
        if c.sense=='=' and c.rhs==1 and len(c.expr.terms)==n and all(len(k)==1 and k[0] in binary and a==1 for k,a in c.expr.terms.items()):eq.append({k[0] for k in c.expr.terms})
    if len(eq)!=2*n:return None
    rows=[eq[0]]
    for group in eq[1:]:
        if all(not group&r for r in rows):rows.append(group)
    if len(rows)!=n or set.union(*rows)!=binary:return None
    cols=[g for g in eq if g not in rows]
    if len(cols)!=n or set.union(*cols)!=binary or any(len(a&b)!=1 for a in rows for b in cols):return None
    if any(cols[i]&cols[j] for i in range(n) for j in range(i)):return None
    matrix=[[next(iter(r&c)) for c in cols] for r in rows];lifts={}
    for c in model.constraints:
        if c.sense!='=' or any(len(k)>1 for k in c.expr.terms):continue
        general=[k[0] for k in c.expr.terms if k and k[0] not in binary]
        if len(general)!=1:continue
        j=general[0];coefficient=c.expr.terms[(j,)];e=Expr()
        e.add((),F(c.rhs)/coefficient)
        for k,a in c.expr.terms.items():
            if k!=(j,):e.add(k,-F(a)/coefficient)
        if j in lifts and lifts[j].serial()!=e.serial():return None
        lifts[j]=e
    if set(lifts)!=set(range(len(model.variables)))-binary:return None
    return {'kind':'assignment_with_linear_lifts','matrix':matrix,'lifts':lifts,'proof':'two exact binary partitions, pairwise singleton intersections; other variables have defining linear equalities','tour_semantics':'UNKNOWN'}

def from_permutation(model,structure,permutation):
    x=[0]*len(model.variables)
    for row,col in enumerate(permutation):x[structure['matrix'][row][col]]=1
    for j,e in structure['lifts'].items():
        y=e.evaluate(x)
        if y!=int(y):raise ValueError('Noninteger lift')
        x[j]=int(y)
    return x

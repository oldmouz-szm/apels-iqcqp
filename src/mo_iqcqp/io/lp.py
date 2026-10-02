"""A rejecting, tokenized parser for the audited dialect, NOT general LP/MPS.

Lines are streamed. Each expression is tokenized then reduced into sparse monomials.
Explicit size/term guards fail visibly before an impractical model is expanded.
"""
import hashlib
import re
import time
import math
from fractions import Fraction as F
from pathlib import Path
from mo_iqcqp.model import Model, Variable, Expr, Constraint
from mo_iqcqp.experiment.resources import input_limits

CONVERTER='strict-lp-v1'
class ParseError(ValueError): pass
TOKEN=re.compile(r'\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z_0-9.]*)|(<=|>=|[+\-*/^\[\]:=]))')
NAME=re.compile(r'[A-Za-z_][A-Za-z_0-9.]*\Z')
NUMBER=re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')

def tokenize(line, deadline=float('inf')):
    pos=0
    count=0
    while pos<len(line):
        count+=1
        if count%256==0 and time.monotonic()>=deadline:raise TimeoutError('parsing deadline')
        while pos<len(line) and line[pos].isspace():
            pos+=1
            if pos%256==0 and time.monotonic()>=deadline:raise TimeoutError('parsing deadline')
        if pos>=len(line):break
        m=TOKEN.match(line,pos)
        if not m:raise ParseError(f'Unknown token near {line[pos:pos+40]!r}')
        yield next(t for t in m.groups() if t is not None);pos=m.end()

def expression(tokens, register, deadline=float('inf'), max_expression_terms=500_000):
    e=Expr(); block=None; i=0; need_sign=False
    while i<len(tokens):
        if i%256==0 and time.monotonic()>=deadline:raise TimeoutError('parsing deadline')
        t=tokens[i]
        if t==']':
            if block is None:raise ParseError('Unmatched ]')
            i+=1; divisor=F(1)
            if i<len(tokens) and tokens[i]=='/':
                if i+1>=len(tokens) or not NUMBER.fullmatch(tokens[i+1]):raise ParseError('Bad bracket divisor')
                divisor=F(tokens[i+1]);i+=2
                if divisor==0:raise ParseError('Zero divisor')
            for key,val in block.terms.items():
                e.add(key,val/divisor)
                if len(e.terms)>max_expression_terms:raise ParseError('RESOURCE_LIMIT: expression terms')
            block=None;need_sign=True;continue
        sign=1
        if t in ('+','-'):
            sign=1 if t=='+' else -1;i+=1
            if i==len(tokens):raise ParseError('Trailing sign')
            t=tokens[i]
        elif need_sign and t!='[':raise ParseError('Missing sign between terms')
        if t=='[':
            if block is not None or sign!=1:raise ParseError('Nested/signed brackets not supported')
            block=Expr();i+=1;need_sign=False;continue
        coeff=F(sign)
        if NUMBER.fullmatch(t):
            coeff*=F(t);i+=1
            t=tokens[i] if i<len(tokens) else ''
        key=()
        if NAME.fullmatch(t):
            key=(register(t),);i+=1
            if i<len(tokens) and tokens[i]=='^':
                if i+1>=len(tokens) or tokens[i+1]!='2':raise ParseError('Only square powers supported')
                key=key+key;i+=2
            elif i<len(tokens) and tokens[i]=='*':
                if i+1>=len(tokens) or not NAME.fullmatch(tokens[i+1]):raise ParseError('Bad quadratic product')
                key=tuple(sorted((key[0],register(tokens[i+1]))));i+=2
        elif coeff==sign and not NUMBER.fullmatch(tokens[i-1] if i else ''):
            raise ParseError('Expected monomial')
        target=block if block is not None else e
        target.add(key,coeff);need_sign=True
        if len(target.terms)>max_expression_terms:raise ParseError('RESOURCE_LIMIT: expression terms')
    if block is not None:raise ParseError('Unclosed bracket')
    if len(e.terms)>max_expression_terms:raise ParseError('RESOURCE_LIMIT: expression terms')
    return e

def load_lp(path, deadline=float('inf'), max_bytes=40_000_000, max_terms=500_000,
            max_variables=100_000, max_constraints=100_000, max_expression_terms=500_000):
    input_limits(dict(max_bytes=max_bytes,max_terms=max_terms,max_variables=max_variables,
                      max_constraints=max_constraints,max_expression_terms=max_expression_terms))
    path=Path(path)
    if path.stat().st_size>max_bytes:raise ParseError('RESOURCE_LIMIT: source exceeds configured max_bytes; no dense fallback')
    variables=[]; mapping={}; objectives=[]; directions=[]; constraints=[]; section=None; mode=None
    tokens=[]; name=None; objective_mode=None; sha=hashlib.sha256(); term_count=0; ended=False; seen=set(); start_line=0;objective_labels=[]
    def register(name):
        if name not in mapping:
            if len(variables)>=max_variables:raise ParseError('RESOURCE_LIMIT: variable count')
            mapping[name]=len(variables);variables.append(Variable(name))
        return mapping[name]
    def append_tokens(source):
        for token in tokenize(source,deadline):
            tokens.append(token)
            if len(tokens)>max_expression_terms*7:
                raise ParseError('RESOURCE_LIMIT: expression tokens')
    def flush():
        nonlocal tokens,term_count,name,objective_mode
        if name is None:
            if tokens:raise ParseError('Expression without label')
            return
        if section=='objective':objectives.append(expression(tokens,register,deadline,max_expression_terms));directions.append(objective_mode or mode);term_count+=len(objectives[-1].terms)
        elif section=='constraint':
            if len(constraints)>=max_constraints:raise ParseError('RESOURCE_LIMIT: constraint count')
            relations=[i for i,t in enumerate(tokens) if t in ('<=','>=','=')]
            if len(relations)!=1:raise ParseError('Constraint requires one relation')
            j=relations[0];rhs=expression(tokens[j+1:],register,deadline,max_expression_terms)
            if any(k for k in rhs.terms):raise ParseError('Nonconstant RHS unsupported')
            expr=expression(tokens[:j],register,deadline,max_expression_terms)
            constraints.append(Constraint(name,expr,tokens[j],rhs.terms.get((),0)));term_count+=len(expr.terms)
        if term_count>max_terms:raise ParseError('RESOURCE_LIMIT: sparse term count')
        tokens=[];name=None;objective_mode=None
    with path.open('rb') as f:
        for line_no,b in enumerate(f,1):
            if time.monotonic()>=deadline:raise TimeoutError('parsing deadline')
            sha.update(b);s=b.decode('utf-8').strip()
            if not s or s.startswith('\\'):continue
            if s.split()[0].lower() in ('sos','sos1','sos2','indicator','semi-continuous','semi-integer'):raise ParseError('Unsupported variable/constraint section')
            if ended:raise ParseError('Content after End')
            if s in ('Minimize multi-objectives','Maximize multi-objectives','Minimize','Maximize'):
                if mode is not None:raise ParseError('Repeated objective header')
                mode='min' if s.startswith('Min') else 'max';section='objective';continue
            if s in ('Subject To','Bounds','Binary','Binaries','General','Generals','End'):
                flush()
                section={'Subject To':'constraint','Bounds':'bounds','Binary':'binary','Binaries':'binary','General':'integer','Generals':'integer','End':'end'}[s]
                if section in seen:raise ParseError('Duplicate section')
                seen.add(section);ended=section=='end';continue
            if section=='objective' and ':' in s:
                flush();label,rest=s.split(':',1);name=label;start_line=line_no
                if label in objective_labels:raise ParseError('Duplicate objective label')
                objective_labels.append(label)
                if label.startswith('OBJ'):
                    metadata=rest.strip()
                    prefix='Priority=1 Weight=1.0 AbsTol=0.0 RelTol=0.0'
                    if not re.fullmatch(r'OBJ\d+',label) or metadata not in (prefix,prefix+' Sense=min',prefix+' Sense=max'):
                        raise ParseError('Unknown multiobjective metadata')
                    if metadata.endswith('Sense=min'):objective_mode='min'
                    elif metadata.endswith('Sense=max'):objective_mode='max'
                else:append_tokens(rest)
            elif section=='constraint' and ':' in s:
                flush();name,rest=s.split(':',1);append_tokens(rest);start_line=line_no
            elif section in ('objective','constraint'):
                append_tokens(s)
            elif section in ('binary','integer'):
                for t in tokenize(s,deadline):
                    if not NAME.fullmatch(t):raise ParseError('Bad variable declaration')
                    v=variables[register(t)]
                    if v.kind!='CONTINUOUS':raise ParseError('Repeated type declaration')
                    v.kind='B' if section=='binary' else 'I'
                    if v.kind=='B':v.lower=max(0,v.lower) if v.lower is not None else 0;v.upper=min(1,v.upper) if v.upper is not None else 1
            elif section=='bounds':
                tt=list(tokenize(s,deadline))
                # Bound grammar uses exactly one identifier and constant sides.
                if len(tt)==2 and tt[1]=='free':
                    v=variables[register(tt[0])];v.lower=v.upper=None;continue
                ids=[j for j,t in enumerate(tt) if NAME.fullmatch(t)]
                if len(ids)!=1:raise ParseError('Unsupported bound syntax')
                j=ids[0];v=variables[register(tt[j])]
                def num(seq):
                    try:return F(''.join(seq))
                    except Exception as e:raise ParseError('Nonfinite/nonconstant bound') from e
                if j>0:
                    if tt[j-1]!='<=':raise ParseError('Expected lower <= var')
                    v.lower=num(tt[:j-1])
                if j+1<len(tt):
                    op=tt[j+1];val=num(tt[j+2:])
                    if op=='<=':v.upper=val
                    elif op=='>=':v.lower=val
                    elif op=='=':v.lower=v.upper=val
                    else:raise ParseError('Bad bound relation')
                if j==0 and j+1==len(tt):raise ParseError('Missing bound')
            else:raise ParseError(f'Unknown section/content at line {line_no}: {s[:100]}')
    if not ended or not objectives or mode is None:raise ParseError('Incomplete LP')
    if len({c.name for c in constraints})!=len(constraints):raise ParseError('Duplicate constraint labels')
    for v in variables:
        if v.kind=='B':
            v.lower=max(0,v.lower) if v.lower is not None else 0
            v.upper=min(1,v.upper) if v.upper is not None else 1
        if v.kind=='CONTINUOUS':raise ParseError(f'Continuous/undeclared variable {v.name}')
        if v.lower is not None and v.upper is not None and v.lower>v.upper:raise ParseError('Inconsistent bounds')
        if v.lower is not None and v.upper is not None and math.ceil(v.lower)>math.floor(v.upper):raise ParseError('EMPTY_INTEGER_DOMAIN')
    model=Model(variables,objectives,directions,constraints,{'path':str(path),'sha256':sha.hexdigest(),'converter':CONVERTER,'numeric':'exact-rational','equivalence':'SERIALIZED_LP_ONLY','objective_labels':objective_labels})
    return model

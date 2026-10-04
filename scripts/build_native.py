"""Reproducible audited patch generation; never writes to the original installation."""
from pathlib import Path
import hashlib, json, os, re, subprocess, sys, tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mo_iqcqp.backends.build import source_hash,sha256
SRC=ROOT/'third_party/ls-iqcqp'; BUILD=ROOT/'build'; PATCHED=BUILD/'ls'

BUILD.mkdir(exist_ok=True);SRC.mkdir(parents=True,exist_ok=True);PATCHED.mkdir(exist_ok=True)
os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
if not (SRC/'LICENSE').exists():
    raise FileNotFoundError('Bundled LS-IQCQP sources/license are required')

def strip_comments(s):
    return re.sub(r'("(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27)|//[^\n]*|/\*[\s\S]*?\*/',lambda m:m[1] or '',s)

def balanced(s,pos,left='(',right=')'):
    depth=1;i=pos+1
    while depth:
        if s[i]==left:depth+=1
        if s[i]==right:depth-=1
        i+=1
    return i

def instrument(s):
    # At every loop-condition evaluation, including long repair/neighbourhood loops.
    edits=[]
    for m in re.finditer(r'\b(for|while)\s*\(',s):
        start=m.end()-1;end=balanced(s,start);inside=s[start+1:end-1]
        if m[1]=='while':replacement='(mo_probe(), ('+inside+'))'
        elif ';' in inside:
            bits=inside.split(';')
            if len(bits)!=3:raise RuntimeError('Unexpected for header')
            bits[1]='(mo_probe(), ('+(bits[1].strip() or 'true')+'))';replacement=';'.join(bits)
        else:
            # Range loops: inject into body. All upstream range bodies are braced.
            body=end
            while s[body].isspace():body+=1
            if s[body]!='{':continue
            edits.append((body+1,body+1,' mo_probe(); '));continue
        edits.append((start+1,end-1,replacement))
    for a,b,r in reversed(edits):s=s[:a]+r+s[b:]
    return s

stepdefs=[]
branches=[('ls_no_cons.cpp','local_search_without_cons','without_cons'),('ls_bin.cpp','local_search_bin_new','bin_new'),('ls_bin.cpp','local_search_bin','bin'),('ls_balance.cpp','local_search_mix_balance','mix_balance')]
for filename,func,short in branches:
    s=strip_comments((SRC/filename).read_text());start=s.index('void qp_solver::'+func+'()');start=s.index('{',start);end=balanced(s,start,'{','}')
    body=s[start+1:end-1];loop=re.search(r'for\s*\(_steps\s*=\s*0;',body);lb=body.index('{',loop.start());le=balanced(body,lb,'{','}')
    prefix=body[:loop.start()]
    prefix=re.sub(r'std::srand\([^;]+;|initialize(?:_without_cons)?\(\);|sta_cons\(\);|restart_by_new_solution\(\);|cout\s*<<[^;]*;', '', prefix)
    core=body[lb+1:le-1]
    core=re.sub(r'if\s*\(_steps % 1000 == 0 && \(TimeElapsed\(\) > _cut_off\)\)\s*(?:\{[\s\S]*?break;\s*\}|break;)', '',core)
    stepdefs.append('void qp_solver::mo_step_'+short+'(){\n'+prefix+core+'\n++_steps;\n}')

for p in SRC.iterdir():
    if p.suffix not in ('.h','.cpp'):continue
    old=p.read_text();s=strip_comments(old)
    if p.suffix=='.cpp':
        s=re.sub(r'\b(?:std\s*::\s*)?cout\b','std::cerr',s)
        s=re.sub(r'\b(?:std::)?srand\(', 'mo_seed(',s)
        s=re.sub(r'\brand\(\)', 'mo_rand()',s)
        s=re.sub(r'\bexit\([^;]+\);', 'throw std::runtime_error("upstream invariant exit");',s)
        for method in ['execute_critical_move','execute_critical_move_no_cons','execute_critical_move_mix','execute_critical_move_mix_more']:
            marker=re.search(r'void qp_solver::'+method+r'\([^)]*\)\s*\{',s)
            if marker:
                end=balanced(s,marker.end()-1,'{','}')
                s=s[:end-1]+'\nif(mo_candidate) mo_candidate();\n'+s[end-1:]
        if p.name!='call.cpp':s=instrument(s)
    if p.name=='sol.h':
        s=s.replace('class qp_solver {\n    public:', '''class qp_solver {
    public:
        std::mt19937_64 mo_rng{1};
        std::function<void()> mo_candidate;
        std::chrono::steady_clock::time_point mo_deadline=std::chrono::steady_clock::time_point::max();
        unsigned long long mo_probes=0;
        void mo_probe(){ if ((++mo_probes & 63)==0 && std::chrono::steady_clock::now()>=mo_deadline) throw std::runtime_error("DEADLINE"); }
        int mo_rand(){return static_cast<int>(mo_rng() & 0x7fffffff);}
        void mo_seed(unsigned long long seed){mo_rng.seed(seed);}
        void mo_step_without_cons(); void mo_step_bin_new(); void mo_step_bin(); void mo_step_mix_balance();
        ~qp_solver(){for(auto &v:_vars) delete v.recent_value;}
''')
        s=s.replace('_best_object_value = INT32_MAX','_best_object_value = std::numeric_limits<Float>::infinity()')
    if p.name=='util.h':s='#include <random>\n#include <limits>\n#include <stdexcept>\n#include <functional>\n'+s
    (PATCHED/p.name).write_text(s)
steps='#include "sol.h"\nnamespace solver {\n'+ '\n'.join(stepdefs)+'\n}\n'
(PATCHED/'steps.cpp').write_text(instrument(steps))
sources=[str(PATCHED/f) for f in ['ls_read.cpp','ls_no_cons.cpp','ls_bin.cpp','component.cpp','ls_mix_not_dis.cpp','ls_balance.cpp','steps.cpp']]
cmd=['g++','-std=c++17','-O2','-g','-fno-omit-frame-pointer','-I'+str(PATCHED),*sources,str(ROOT/'native/worker.cpp'),'-lgsl','-lgslcblas','-lm','-o',str(BUILD/'ls_worker')]
if '--sanitize' in sys.argv:cmd[2:2]=['-fsanitize=address,undefined'];cmd[-1]=str(BUILD/'ls_worker_sanitize')
print(' '.join(cmd),flush=True)
target=Path(cmd[-1]);fd,temporary=tempfile.mkstemp(prefix=target.name+'-',dir=BUILD)
os.close(fd);cmd[-1]=temporary
try:
    subprocess.run(cmd,check=True)
    os.replace(temporary,target)
finally:
    Path(temporary).unlink(missing_ok=True)
target.with_suffix('.manifest.json').write_text(json.dumps(dict(
    source_sha256=source_hash(ROOT),binary_sha256=sha256(target),
    compiler=subprocess.check_output(['g++','--version'],text=True).splitlines()[0]),indent=2)+'\n')

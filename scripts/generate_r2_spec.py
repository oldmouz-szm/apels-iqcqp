"""Generate a new frozen R2 spec; existing paths are never overwritten."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from mo_iqcqp.io import load_lp
from mo_iqcqp.metrics.normalization import make_spec
from mo_iqcqp.metrics.r2 import FixedR2
p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');p.add_argument('--seed',type=int,default=20260930);p.add_argument('--explicit',help='JSON with finite coordinates, positive scales, and coordinate proof metadata');a=p.parse_args()
m=load_lp(a.input);sp=make_spec(m,a.seed,json.loads(Path(a.explicit).read_text()) if a.explicit else None);FixedR2(sp,m)
with Path(a.output).open('x') as f:json.dump(sp,f,indent=2,allow_nan=False);f.write('\n')
print('Frozen',sp['schema'],a.output)

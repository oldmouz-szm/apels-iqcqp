"""Compile regression checks into a temporary directory; no fixtures are kept."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'src'))
from mo_iqcqp.backends.build import verify
sanitized='--sanitize' in sys.argv
verify(root,root/('build/ls_worker_sanitize' if sanitized else 'build/ls_worker'))
with tempfile.TemporaryDirectory(prefix='apels-native-tests-') as temp:
    executable=str(Path(temp)/'regression')
    sources=[root/'build/ls'/name for name in (
        'ls_read.cpp','ls_no_cons.cpp','ls_bin.cpp','component.cpp','ls_mix_not_dis.cpp','ls_balance.cpp','steps.cpp')]
    cmd=['g++','-std=c++17','-O1','-g','-fno-omit-frame-pointer','-I'+str(root/'build/ls')]
    if sanitized:cmd+=['-fsanitize=address,undefined']
    cmd+=list(map(str,sources))+[str(root/'tests/native_regression.cpp'),'-lgsl','-lgslcblas','-lm','-o',executable]
    subprocess.run(cmd,check=True)
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',UBSAN_OPTIONS='halt_on_error=1')
    subprocess.run([executable],check=True,env=env)

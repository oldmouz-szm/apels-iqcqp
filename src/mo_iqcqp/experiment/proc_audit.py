"""Read only /proc observations for an explicitly owned process tree."""
import os
from pathlib import Path

def sample_processes(pids, observations, elapsed):
    for pid in pids:
        try:
            status=Path(f'/proc/{pid}/status').read_text()
            fields=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
            as_line=next(line for line in Path(f'/proc/{pid}/limits').read_text().splitlines()
                         if line.startswith('Max address space'))
            threads={p.name: sorted(os.sched_getaffinity(int(p.name)))
                     for p in Path(f'/proc/{pid}/task').iterdir()}
            signature=repr((pid,as_line,threads))
            if signature not in observations:
                observations[signature]=dict(pid=pid,name=fields['Name'].strip(),
                    ppid=int(fields['PPid']),software_threads=int(fields['Threads']),
                    affinity=sorted(os.sched_getaffinity(pid)),thread_affinities=threads,
                    address_space_limit_line=as_line,first_at=elapsed,last_at=elapsed,samples=0)
            observations[signature]['last_at']=elapsed
            observations[signature]['samples']+=1
        except (OSError,ValueError,StopIteration):
            pass # A process may exit between the bounded /proc reads.

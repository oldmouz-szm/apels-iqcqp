"""Run-local limits; never changes host/WSL configuration."""
import math
import os
import resource
from pathlib import Path

# Defaults protect the laptop. The upper bounds are implementation/protocol
# limits, not a claim that a machine has enough memory to use them.
INPUT_DEFAULTS = dict(max_bytes=40_000_000, max_terms=500_000,
                      max_variables=100_000, max_constraints=100_000,
                      max_expression_terms=500_000)
INPUT_HARD = dict(max_bytes=2**63-1, max_terms=20_000_000,
                  max_variables=100_000, max_constraints=100_000,
                  max_expression_terms=1_000_000)


def input_limits(values=None):
    values = values or {}
    if not isinstance(values, dict) or set(values)-set(INPUT_DEFAULTS):
        raise ValueError('Unknown input limit')
    result = dict(INPUT_DEFAULTS)
    result.update(values)
    for key, value in result.items():
        if type(value) is not int or not 1 <= value <= INPUT_HARD[key]:
            raise ValueError(f'{key} must be an integer in [1, {INPUT_HARD[key]}]')
    if result['max_expression_terms'] > result['max_terms']:
        raise ValueError('max_expression_terms cannot exceed max_terms')
    return result


def cgroup_file(name):
    """Resolve this process's cgroup; root files may not exist on WSL."""
    try:
        relative=next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines()
                      if line.startswith('0::'))
        current=Path('/sys/fs/cgroup')/relative.lstrip('/')
        root=Path('/sys/fs/cgroup')
        while current.is_relative_to(root):
            candidate=current/name
            if candidate.exists():return str(candidate)
            if current==root:break
            current=current.parent
    except (OSError,StopIteration):pass
    return '/sys/fs/cgroup/'+name


def resource_observation():
    """Observed entitlement and headroom; unknown is represented by None."""
    def read(path):
        try: return Path(path).read_text().strip()
        except OSError: return None
    cpu = read(cgroup_file('cpu.max'))
    cpu_cores = None
    if cpu:
        parts = cpu.split()
        if len(parts) == 2 and parts[0] != 'max':
            try: cpu_cores = int(parts[0])/int(parts[1])
            except (ValueError, ZeroDivisionError): pass
    maximum = read(cgroup_file('memory.max'))
    current = read(cgroup_file('memory.current'))
    try: memory_max = int(maximum) if maximum != 'max' else None
    except (ValueError, TypeError): memory_max = None
    try: memory_current = int(current)
    except (ValueError, TypeError): memory_current = None
    host_available = None
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                host_available=int(line.split()[1])*1024
                break
    except OSError: pass
    as_soft,as_hard=resource.getrlimit(resource.RLIMIT_AS)
    return dict(affinity_cpus=sorted(os.sched_getaffinity(0)),
                inherited_rlimit_as_soft_bytes=None if as_soft<0 else as_soft,
                inherited_rlimit_as_hard_bytes=None if as_hard<0 else as_hard,
                cgroup_cpu_quota_cores=cpu_cores,
                cgroup_memory_max_bytes=memory_max,
                cgroup_memory_current_bytes=memory_current,
                cgroup_cpuset=read(cgroup_file('cpuset.cpus.effective')),
                host_mem_available_bytes=host_available,
                effective_mem_available_bytes=available_memory())


def configuration(config=None):
    import json
    if isinstance(config, dict):return dict(config)
    return json.loads(Path(config).read_text()) if config else {}


def plan(config, allowed=None, observation=None):
    allowed = sorted(os.sched_getaffinity(0) if allowed is None else allowed)
    values = {k: config.get(k, 1) for k in ('jobs','workers','core_budget')}
    if any(type(v) is not int or v < 1 for v in values.values()):
        raise ValueError('jobs, workers and core_budget must be positive integers')
    if values['jobs'] != 1:
        raise ValueError('Only serial jobs=1 is supported')
    if values['workers'] > values['core_budget'] or values['core_budget'] > len(allowed):
        raise ValueError('workers <= core_budget <= allowed affinity CPUs required')
    observed = observation if observation is not None else resource_observation()
    quota = observed.get('cgroup_cpu_quota_cores')
    if quota is not None and values['core_budget'] > quota + 1e-9:
        raise ValueError('core_budget exceeds cgroup CPU quota')
    values['affinity'] = allowed[:values['core_budget']]
    for k, default in (('memory_mib',512),('tree_memory_mib',640)):
        value = config.get(k, default)
        if type(value) is not int or not 64 <= value <= (2**63-1)//(1024**2):
            raise ValueError(f'{k} must be an integer in [64, RLIMIT_AS maximum]')
        values[k] = value
    # RLIMIT_AS is virtual address space, while the tree cap is resident RSS;
    # neither quantity must be numerically larger than the other.
    cgroup_max = observed.get('cgroup_memory_max_bytes')
    if cgroup_max is not None and values['tree_memory_mib']*1024**2 > cgroup_max:
        raise ValueError('tree_memory_mib exceeds cgroup memory.max')
    inherited_hard=observed.get('inherited_rlimit_as_hard_bytes')
    if inherited_hard is not None and values['memory_mib']*1024**2>inherited_hard:
        raise ValueError('memory_mib exceeds inherited RLIMIT_AS hard cap')
    headroom = None
    if cgroup_max is not None and observed.get('cgroup_memory_current_bytes') is not None:
        headroom=max(0,cgroup_max-observed['cgroup_memory_current_bytes'])
    available = [v for v in (headroom,observed.get('host_mem_available_bytes')) if v is not None]
    if available and values['tree_memory_mib']*1024**2>min(available):
        raise ValueError('tree_memory_mib exceeds observed available memory')
    return values


def tree(pid):
    try:
        children = list(map(int, Path(f'/proc/{pid}/task/{pid}/children').read_text().split()))
    except (OSError, ValueError):
        return [pid]
    return [pid] + [p for c in children for p in tree(c)]


def rss(pids):
    total = 0
    for pid in pids:
        try:
            for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                if line.startswith('VmRSS:'):
                    total += int(line.split()[1])*1024
        except OSError:
            pass
    return total


def available_memory():
    host = None
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                host = int(line.split()[1])*1024
                break
    except OSError: pass
    def read_int(path):
        try: return int(Path(path).read_text().strip())
        except (OSError, ValueError): return None
    maximum = read_int(cgroup_file('memory.max'))
    current = read_int(cgroup_file('memory.current'))
    headroom = max(0, maximum-current) if maximum is not None and current is not None else None
    known = [v for v in (host, headroom) if v is not None]
    return min(known) if known else None

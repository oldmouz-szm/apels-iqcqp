"""Linux parent-death guards for owned coordinator/backend processes only."""
import ctypes
import os
import signal

PARENT_ENV = 'MO_IQCQP_EXPECTED_PARENT'

def child_environment(environment=None):
    env = dict(os.environ if environment is None else environment)
    env[PARENT_ENV] = str(os.getpid())
    return env

def arm_parent_death_guard():
    expected = os.environ.get(PARENT_ENV)
    if expected is None:
        return
    parent = int(expected)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, 'Cannot arm owned-process parent-death guard')
    # Covers parent death between spawning us and arming the kernel guard.
    if os.getppid() != parent:
        os._exit(125)

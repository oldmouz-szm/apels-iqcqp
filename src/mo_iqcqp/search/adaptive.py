"""Sliding-window service-rate UCB heuristic; no stochastic-bandit guarantee."""
from collections import deque
from fractions import Fraction
import hashlib
import math
import random
import time
from .tasks import TaskBuilder

ARMS = ('direction', 'epsilon', 'pls')
REWARD_VERSION = 'full-archive-fixed-hv-exact2d-v1'
R2_REWARD_VERSION = 'full-archive-fixed-asf-r2-exact-v1'

class AdaptiveScheduler(TaskBuilder):
    def __init__(self, n, seed=1, config=None):
        config = config or {}
        version = config.get('adaptive', {}).get('reward_version', REWARD_VERSION)
        if version == REWARD_VERSION and n != 2:
            raise ValueError('Apels-IQCQP HV scheduling supports exactly two objectives with exact HV')
        if n < 2 or version not in (REWARD_VERSION, R2_REWARD_VERSION):
            raise ValueError('Reward version/objective dimension mismatch')
        self.reward_version = version
        if 'shares' in config:
            raise ValueError('Adaptive does not accept fixed shares')
        enabled=config.get('enabled_arms',ARMS)
        if (not isinstance(enabled,(list,tuple)) or not enabled or
            any(type(arm) is not str or arm not in ARMS for arm in enabled) or len(set(enabled))!=len(enabled) or
            tuple(enabled)!=tuple(arm for arm in ARMS if arm in enabled)):
            raise ValueError('Invalid enabled_arms')
        super().__init__(n, seed, {'rho': config.get('rho', .001),'bootstrap_strategy':config.get('bootstrap_strategy','legacy_v1')})
        self.enabled_arms=tuple(enabled)
        self.window = deque(maxlen=30)
        digest = hashlib.sha256(('adaptive-selection-v1:' + str(seed)).encode()).digest()
        self.selection_rng = random.Random(int.from_bytes(digest, 'big'))
        self.last_decision = None

    def _eligible(self, points):
        if not points:return ('feasibility',)
        eligible=tuple(k for k in self.enabled_arms if k!='epsilon' or len(points)>=2)
        # Epsilon needs two distinct objective vectors. Feasibility remains
        # available as a timed bootstrap task until that witness exists.
        return eligible if eligible else ('feasibility',)

    def observe(self, kind, gain, service, deadline=float("inf"), clock=time.monotonic):
        if kind not in ARMS:
            return
        gain = Fraction(gain)
        if gain < 0:
            raise ValueError('Negative exact reward gain')
        if not math.isfinite(service) or service <= 0:
            raise ValueError('Service must be finite and positive')
        observation=(kind, gain, Fraction(str(service)))
        if clock()>=deadline:return False
        self.window.append(observation)
        return True

    def _select(self, eligible, pending):
        if eligible == ('feasibility',):
            self.last_decision = dict(eligible=list(eligible), forced_reason='empty_archive', pending=dict(pending))
            return 'feasibility', {}
        stats = {}
        for k in eligible:
            observations = [(g, s) for arm, g, s in self.window if arm == k]
            gain = sum((g for g, s in observations), Fraction(0))
            service = sum((s for g, s in observations), Fraction(0))
            p = pending.get(k, 0)
            if type(p) is not int or p < 0:
                raise ValueError('Pending counts must be nonnegative integers')
            stats[k] = dict(n=len(observations), p=p, gain=gain, service=service,
                            efficiency=gain/service if service else Fraction(0))
        maximum = max(v['efficiency'] for v in stats.values())
        total = sum(v['n'] + v['p'] for v in stats.values())
        forced = []
        for k, v in stats.items():
            v['Q'] = float(v['efficiency']/maximum) if maximum else 0.
            count = v['n'] + v['p']
            if not count:
                forced.append(k)
                v.update(exploration=None, score=None)
            else:
                exploration = math.sqrt(2*math.log1p(total)/count)
                v.update(exploration=exploration, score=v['Q']+exploration)
        if forced:
            candidates = forced
        else:
            best = max(v['score'] for v in stats.values())
            candidates = [k for k, v in stats.items()
                          if math.isclose(v['score'], best, rel_tol=1e-12, abs_tol=1e-12)]
        chosen = self.selection_rng.choice(candidates)
        self.last_decision = dict(eligible=list(eligible), pending=dict(pending), N=total,
            forced_reason='no_window_observation_or_pending' if forced else None,
            forced_candidates=forced, tie_candidates=candidates,
            arms={k: {name: str(value) if isinstance(value, Fraction) else value
                       for name, value in v.items()} for k, v in stats.items()})
        return chosen, {}

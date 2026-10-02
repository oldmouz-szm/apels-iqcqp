# apels-iqcqp adaptive scheduling

Adaptive-HV supports exactly two objectives. Feasibility search runs while the validated archive is empty and is not a bandit arm. Direction and PLS are eligible after one objective vector; epsilon requires two distinct vectors from the full archive, even if the scheduler work capacity is one.

For each instance, a supplied `hv-v1` specification freezes objective directions, normalization origin and scale, and a strictly worse reference. The specification must match the input SHA-256 and mathematical model fingerprint. Search tasks keep their own frozen z/scale, which do not change reward coordinates. A completed task receives the exact Fraction reward

    gain = HV(full archive after validated batch; fixed spec)
         - HV(full archive before validated batch; fixed spec).

The deadline-aware 2D sweep checks every full-archive point and each heap step. Repeated or dominated points earn no extra credit. A negative exact gain or out-of-range reference is an error. The coordinator admits only assignments verified against the original model before the global deadline.

Over the latest W=30 valid completed arm observations, arm k has service efficiency

    E[k] = sum(gain[i] for arm[i] == k) / sum(service[i] for arm[i] == k).

Service includes active worker wall time plus coordinator batch validation, admission and reward wall. Scheduling, logging and checkpoint work also consume the global budget and are recorded separately. Valid zero-gain completions count. Global-deadline cancellations and incomplete rewards are censored; infrastructure failures stop the run.

For currently eligible arms let n[k] be completed window observations, p[k] be in-flight counts, M=max(E), and N=sum(n+p). Set Q[k]=E[k]/M if M>0, else zero. Any arm with n[k]+p[k]=0 gets forced exploration first. Otherwise choose the maximum of

    Q[k] + sqrt(2*log(1+N)/(n[k]+p[k])).

Scores within 1e-12 relative or absolute tolerance tie. Ties use an independent SHA-256-derived RNG, leaving the task-construction RNG unchanged. A pending task is counted for exploration but has no invented gain. Every decision and task result is recorded in finite JSON telemetry. Identical event, pending and service sequences replay identical decisions; wall-clock searches need not produce identical points.

This is a sliding-window, gain-per-service, pending-adjusted UCB-style heuristic. It is neither FRRMAB nor covered by standard UCB1 regret bounds. This page defines the legacy exact-HV Adaptive mode, which still supports only two objectives. Adaptive-R2 supports finite `m>=2`; their extension and signed ASF/R2 reward are specified in [MANY_OBJECTIVE_SPEC.md](MANY_OBJECTIVE_SPEC.md).

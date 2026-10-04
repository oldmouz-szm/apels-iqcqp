# Apels-IQCQP batch safety contract — v12

Each run owns its process tree, resources, frozen request and result artifacts. Exact original-model validation and fixed reward coordinates remain mandatory.

## Process ownership

On Linux, each managed child receives its creator PID and arms PR_SET_PDEATHSIG(SIGKILL). The coordinator and native LS worker check the parent again immediately after arming; a parent that died during startup causes exit 125. Thus the supervisor/coordinator/backend chain also cleans up after abrupt parent exit before the first /proc sample. This is run-local, not a system configuration change. Cleanup signals the unreaped direct child through Popen, whose poll/waitpid handling avoids signaling a recycled PID. It never signals a stale numerical PID recovered from a dead parent's tree. Kernel guards cover these managed processes, not arbitrary externally launched processes. A dead zombie is not an active solver; adoption/reaping belongs to the OS parent.

## Request transport and identity

The supervisor writes an exclusive `<output>.request` JSON sidecar and passes only its pathname and SHA256 through argv. The child verifies schema, invocation start, content digest and mutual exclusion with legacy inline arguments before use. The verified request file is the only preexisting output artifact accepted by the child. The sidecar freezes effective algorithm/resource configuration and queue identity, including actual objective dimension, input/source/native hashes and reward specification. Large finite-dimensional configurations no longer hit Linux's per-argument size cap. Parsing, allocation and hashing still cost real time/memory and can exhaust resources.

Run protocol: `end-to-end-cold-ls-adaptive-v12`. Queue schema: `queue-run-ls-adaptive-v12`. Results from other protocol versions are never reused.

## Saved content, resume and recovery

Final results and checkpoints carry a SHA256 over canonical JSON content excluding the digest field. This detects accidental changes; it is not an authenticated signature against an actor who can rewrite everything. Queue reuse also reloads the original source and checks every saved assignment, exact original/internal objective vector, validation time and pairwise nondominance, plus event-stream digest and full identity. Invalid JSON, corrupt content or invalid points cause a new attempt; original bytes remain untouched. Resume checks are outside the next cold-run clock and their cost is recorded.

On interruption/error/resource stop, recovery first checks the same invocation's sealed final result, then its sealed checkpoint. Saved samples are independently revalidated; no new sample is admitted and no censored reward is learned during recovery. A valid final result can be richer than the last checkpoint and must not be overwritten by it. Full configuration, model mapping and source/native identity are retained. Without a valid saved model, missing metadata is explicit null and status is MODEL_NOT_LOADED_OR_NOT_CHECKPOINTED. An invalid existing final artifact is preserved as `.unverified-final` before publishing the structured failure. Recovery statistics are marked incomplete. Recovery/revalidation and serialization add wall time; this does not extend the search deadline. SIGKILL of the supervisor cannot itself publish a final JSON; its surviving artifacts force a fresh attempt on resume.

## Epsilon-only adaptive configuration

With enabled_arms=["epsilon"], a single archive vector triggers bounded feasibility tasks for a second witness, not fake arm feedback. Finding a second vector is not guaranteed. The global deadline still applies.

## Exit codes and queue behavior

Single-run exit codes: COMPLETED=0, ERROR=2, HARD_TIMEOUT=124, INTERRUPTED=130, RESOURCE_*=3. Abnormal statuses are printed on stderr while existing successful stdout fields remain compatible. COMPLETED with no feasible sample still means the search completed, not a proof of infeasibility. Queue continues after instance failures by default; `continue_on_error: false` enables fail-fast behavior. It writes `queue-summary.json` incrementally, including preparation errors, failed prechecks, reused runs and result paths. A queue with failures exits 2; interruption exits 130 and stops further dispatch. Each failed attempt is retried at most once per explicit queue invocation. Relative input/configuration paths resolve against the manifest directory. An advisory output-directory lock prevents simultaneous queues, and the manifest hash rejects accidental reuse of another queue's output directory.

## Limits and preparation

`prepare` references external LP files without copying them. It freezes per-instance normalization and accepts the same explicit input caps as `run`. It records model preparation errors while retaining valid tasks. The optional coefficient normalization uses fixed, unproved search units and never invents variable bounds. Preparation and queue preflight are outside the cold-run budget, but their parsing/normalization enforce a temporary per-process address-space cap. Preflight memory exhaustion is recorded as `RESOURCE_PRECHECK_MEMORY_LIMIT` without blocking later inputs; worker allocation exhaustion returns `RESOURCE_ADDRESS_SPACE_LIMIT`.

A validated initial assignment is checkpointed before native model loading. Search, parsing and native requests share the global run deadline. Recovery sample revalidation stops at the reporting deadline (budget plus 30.5 seconds); samples that cannot be verified within it are not retained. JSON serialization, opaque library calls and filesystem operations are not hard-real-time. Inspect `supervisor_wall` and status in addition to budget.

Large archives require full exact validation, serialization and quadratic pairwise nondominance checks on resume. RSS limits are sampled; each process also has RLIMIT_AS. Resource settings are explicit and must fit observed machine headroom. Serial instance dispatch (`jobs=1`) prevents oversubscription, with multiple native workers allowed within each run's declared core budget.

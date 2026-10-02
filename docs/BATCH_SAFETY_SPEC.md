# Batch safety contract — v10 (2026-10-02)

The seven earlier batch-safety guarantees remain in force. LS move rules, exact original-model validation, signed ASF, fixed R2 normalization and UCB/window semantics are retained. Current operator completion and epsilon rotation changes are specified in ENGINEERING_SPEC.md.

## Process ownership

On Linux, each managed child receives its creator PID and arms PR_SET_PDEATHSIG(SIGKILL). The coordinator, native LS worker and SCIP worker check the parent again immediately after arming; a parent that died during startup causes exit 125. Thus the supervisor/coordinator/backend chain also cleans up after abrupt parent exit before the first /proc sample. This is run-local, not a system configuration change. Cleanup signals the unreaped direct child through Popen, whose poll/waitpid handling avoids signaling a recycled PID. It never signals a stale numerical PID recovered from a dead parent's tree. Kernel guards cover these managed processes, not arbitrary externally launched processes. A dead zombie is not an active solver; adoption/reaping belongs to the OS parent.

## Request transport and identity

The supervisor writes an exclusive `<output>.request` JSON sidecar and passes only its pathname and SHA256 through argv. The child verifies schema, invocation start, content digest and mutual exclusion with legacy inline arguments before use. The verified request file is the only preexisting output artifact accepted by the child. The sidecar freezes effective algorithm/resource configuration and queue identity, including actual objective dimension, input/source/native hashes and reward specification. Large finite-dimensional configurations no longer hit Linux's per-argument size cap. Parsing, allocation and hashing still cost real time/memory and can exhaust resources.

Run protocol: `end-to-end-cold-engineering-v10`. Queue schema: `queue-run-engineering-v10`. Existing v7/v8/v9 results remain historical and are never reused as v10.

## Saved content, resume and recovery

Final results and checkpoints carry a SHA256 over canonical JSON content excluding the digest field. This detects accidental changes; it is not an authenticated signature against an actor who can rewrite everything. Queue reuse also reloads the original source and checks every saved assignment, exact original/internal objective vector, validation time and pairwise nondominance, plus event-stream digest and full identity. Invalid JSON, corrupt content or invalid points cause a new attempt; original bytes remain untouched. Resume checks are outside the next cold-run clock and their cost is recorded.

On interruption/error/resource stop, recovery first checks the same invocation's sealed final result, then its sealed checkpoint. Saved samples are independently revalidated; no new sample is admitted and no censored reward is learned during recovery. A valid final result can be richer than the last checkpoint and must not be overwritten by it. Full configuration, model mapping and source/native identity are retained. Without a valid saved model, missing metadata is explicit null and status is MODEL_NOT_LOADED_OR_NOT_CHECKPOINTED. An invalid existing final artifact is preserved as `.unverified-final` before publishing the structured failure. Recovery statistics are marked incomplete. Recovery/revalidation and serialization add wall time; this does not extend the search deadline. SIGKILL of the supervisor cannot itself publish a final JSON; its surviving artifacts force a fresh attempt on resume.

## Fixed epsilon-only

With one distinct archive vector and no eligible enabled arm, Fixed performs bounded feasibility tasks labeled `second_witness`. They are not direction-arm service or learning feedback. At two distinct vectors epsilon becomes eligible. A second vector may not exist or may not be found; execution remains bounded by the real deadline, with no infeasibility claim. Normal legacy 2/3/4D task/RNG replay stays covered; the old epsilon-only early-stop expectation is intentionally replaced by this corrected behavior.

## Exit codes and queue behavior

Single-run exit codes: COMPLETED=0, ERROR=2, HARD_TIMEOUT=124, INTERRUPTED=130, RESOURCE_*=3. Abnormal statuses are printed on stderr while existing successful stdout fields remain compatible. COMPLETED with no feasible sample still means the search completed, not a proof of infeasibility. Queue stops on a non-COMPLETED run and exits nonzero (currently 2), preserving the status in JSON. Resuming requires another explicit invocation; there is no unbounded retry loop.

## SCIP warm start

The auxiliary task_value variable is initialized by evaluating the actual scalar expression at the original-variable assignment. SCIP checks the complete candidate, then records accepted/not_stored/rejected and the subproblem feasibility. An original-feasible point can violate temporary epsilon constraints and is explicitly rejected as a SCIP start; original archive admission remains separately exact. Exceptions are no longer silently swallowed. SCIP uses numerical checks for its own search only, never as the original-model exact validator.

## Acceptance and limits

The development checkout retains the acceptance logs and independent audit artifacts. Tests cover coordinator death before the first sample, supervisor SIGKILL cascade, Ctrl-C with a newer final result, checkpoint recovery, blocked parsing, resource-stop/exit codes, request corruption, malformed or tampered saved samples, 200D queue transport, Fixed second witness and real SCIP warm starts. Short cold functional runs validated fresh source/config/result artifacts and 1/2/4-worker execution in the development checkout.

These are engineering gates, not evidence of quality superiority or parallel speedup. Large archives still require full exact validation, result serialization and O(|A|^2 m) resume nondominance checking. RSS observations are sampled, not a strict instantaneous tree cap; per-process RLIMIT_AS remains enforced. No new 300-second performance comparison is included. Begin subsequent large experiments with a separately frozen small real-input pilot, then expand only after its independent audit succeeds.

Current deadline granularity, SCIP operator closure/runtime identity and exact R2 optimizations are specified in ENGINEERING_SPEC.md.

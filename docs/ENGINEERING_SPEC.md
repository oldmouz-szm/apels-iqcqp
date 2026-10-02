# apels-iqcqp engineering contract (v11)

## Supported scope

The parser, canonical model, exact validator, archive, task vectors and native protocol use the actual finite objective count m >= 2. Adaptive-R2 has no algorithmic 2/3/4-objective ceiling. Legacy Adaptive-HV is deliberately 2D; requested offline exact HV supports 2–4D. No high-dimensional exact HV is required for successful solving. Finite memory, input bytes/terms, native count representation, floating search range and time remain explicit limits.

Models have integer variables, quadratic objectives and quadratic constraints, with the documented LP conventions. Returned assignments and all original objectives are checked with exact rational arithmetic. Unsuccessful search means UNKNOWN, never an infeasibility proof. The complete archive contains verified nondominated samples; it does not certify a complete front or optimum.

## Operator closure and compatibility

LS-IQCQP implements Generic direction and epsilon scalar subproblems and the same bounded Generic PLS neighborhood: at most 64 plus/minus-one integer coordinate moves from a warm point, respecting declared variable bounds, with modular offset. There are at most 2n distinct moves; a short neighborhood never repeats moves to fill the quota. Exact original-model validation and dominance admission remain in the coordinator. This is finite neighborhood sampling, not an exhaustive Pareto local search procedure.

For m >= 3, current default epsilon construction uses its own task counter to rotate primary and tightened coordinates regardless of the scheduler's arm cadence. The explicit legacy_v1 path retains the old 3/4D global-turn construction. The 2D gap rule and legacy task/RNG replay remain supported. Feasibility for an empty archive or missing second witness is not a learning arm.

Structure-enhanced assignment moves require an exactly recognized assignment/lift structure and use LS-IQCQP.

## Anytime and resource boundaries

Model validation, expression serialization/scaling, dominance admission, representative selection and ASF coordinate loops cooperatively check the shared deadline. Archive edits and reward-cache updates either commit completely before their deadline or retain the prior valid state. A late native response never causes post-deadline admission or feedback.

The default persistent_unit_v2 bootstrap exports a tentative first candidate early from LS-IQCQP. Exact admission remains the coordinator’s decision. Explicit persistent_unit_v1 and legacy_v1 remain available. Model loading receives the configured input caps and absolute deadline.

Execution is not hard real-time; opaque native calls, filesystem operations and serialization can add stop/reporting latency. The process watchdog and parent-death guards bound managed execution and cleanup. Search budget, actual_wall, supervisor_wall, overrun/status and resource observations must be read together. A catastrophic kill or resource failure may recover an earlier sealed checkpoint and marks statistics incomplete; no claim that all in-memory points survive arbitrary hardware/process failure is made.

A queue rejects a FIFO or other nonregular source before untimed preflight hashing; byte caps apply there. jobs remains 1. Multiple workers share the declared core budget, and their service intervals can overlap.

## Exact R2 and bounded state

The signed definition remains max_j(w_j (f_j-o_j)/s_j). A new point is normalized once, and zero weights are handled sparsely while preserving their exact zero contribution. In particular, singleton weights still have max(y_j,0) for arbitrary signed normalization; reliable lower-bound normalization makes y_j nonnegative.

Cache minima are monotone because archive removals occur only under exact dominance. The seen-key set contains only the current full archive, not every historically admitted vector. Updates remain atomic, and independent full-archive rational recomputation is available for audit.

For m singleton weights plus 32 dense weights, new-point ASF evaluation uses O(m) active products instead of dense O(m squared) evaluation. The frozen external weight matrix is still dense and has O(m squared) storage/initialization cost. Arbitrary finite dimension does not mean dimension-independent resources.

Representative selection maintains each candidate's current minimum distance, reducing repeated farthest-point work from O(A K squared m) to O(A K m). The distance, stable ties, extreme-point rules and capacity remain the same. Full-archive points are never discarded to satisfy work capacity.

## Identity, errors and output

Run protocol is end-to-end-cold-ls-adaptive-v11 and queue schema is queue-run-ls-adaptive-v11. The source/native hashes, actual m, frozen configuration and reward content remain part of identity.

Final results/checkpoints retain content digests; queue reuse independently validates assignments, objective order/signs, nondominance and event-stream integrity. Single-run abnormal exit codes remain ERROR=2, HARD_TIMEOUT=124, INTERRUPTED=130 and RESOURCE_*=3. No missing metric is represented as a false zero.

The supported installation is a built source checkout, optionally installed editable. A wheel containing only the Python package does not contain the C++ sources/binary and is not a standalone solver distribution.

## Validation

The independent local engineering delivery contains reproducible regression tests, frozen functional validation and raw audit records. Benchmark inputs and historical experiment results are outside the clean solver repository. Exactness and interface tests do not establish performance superiority, parallel speedup or exhaustive verification of every finite dimension.


Only adaptive scheduling and LS-IQCQP are shipped. Obsolete configurations fail validation. The v11 identity prevents reuse of earlier results.

## Release acceptance

The LS-only v11 cleanup was rebuilt and passed 43 applicable regression tests plus 12 fresh cold functional runs at 2, 3, 4, 5, 8, 10, 20, 50, 100 and 200 objectives, including one, two and four workers. These results are functional evidence, not a performance comparison. Local audit data and test inputs are excluded from this source-only repository.

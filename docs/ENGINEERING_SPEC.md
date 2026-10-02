# apels-iqcqp engineering contract (v10)

## Supported scope

The parser, canonical model, exact validator, archive, task vectors and native protocol use the actual finite objective count m >= 2. Fixed and Adaptive-R2 have no algorithmic 2/3/4-objective ceiling. Legacy Adaptive-HV is deliberately 2D; requested offline exact HV supports 2–4D. No high-dimensional exact HV is required for successful solving. Finite memory, input bytes/terms, native count representation, floating search range and time remain explicit limits.

Models have integer variables, quadratic objectives and quadratic constraints, with the documented LP conventions. Returned assignments and all original objectives are checked with exact rational arithmetic. Unsuccessful search means UNKNOWN, never an infeasibility proof. The complete archive contains verified nondominated samples; it does not certify a complete front or optimum.

## Operator closure and compatibility

Both backends implement Generic direction and epsilon scalar subproblems and the same bounded Generic PLS neighborhood: at most 64 plus/minus-one integer coordinate moves from a warm point, respecting declared variable bounds, with modular offset. There are at most 2n distinct moves; a short neighborhood never repeats moves to fill the quota. Exact original-model validation and dominance admission remain in the coordinator. This is finite neighborhood sampling, not an exhaustive Pareto local search procedure.

SCIP PLS now uses an actual neighborhood command, not a scalar optimize call. Its direction/epsilon tasks rebuild a SCIP model; only the worker process and warm incumbent persist. Telemetry explicitly says search_state_continued=false/model_rebuilt=true. Persistent SCIP branch-and-bound state across slices is not claimed.

For m >= 3, current default epsilon construction uses its own task counter to rotate primary and tightened coordinates regardless of the scheduler's arm cadence. The explicit legacy_v1 path retains the old 3/4D global-turn construction. The 2D gap rule and legacy task/RNG replay remain supported. Feasibility for an empty archive or missing second witness is not a learning arm.

Structure-enhanced assignment moves require an exact recognized assignment/lift structure and are supported by LS-IQCQP. SCIP requests for this variant fail explicitly before an initial point is admitted. This optional variant is not advertised as a generic SCIP capability.

## Anytime and resource boundaries

Model validation, expression serialization/scaling, dominance admission, representative selection and ASF coordinate loops cooperatively check the shared deadline. Archive edits and reward-cache updates either commit completely before their deadline or retain the prior valid state. A late native response never causes post-deadline admission or feedback.

The default persistent_unit_v2 bootstrap exports a tentative first candidate early in LS and SCIP. SCIP uses a solution limit plus stage-safe best-solution events; exact admission is still the coordinator's decision. Explicit persistent_unit_v1 and legacy_v1 remain available. SCIP model setup consumes its slice allowance, and model loading receives the configured input caps and absolute deadline.

SCIP is not a hard real-time library; opaque native calls, filesystem operations and serialization can add stop/reporting latency. The process watchdog and parent-death guards bound managed execution and cleanup. Search budget, actual_wall, supervisor_wall, overrun/status and resource observations must be read together. A catastrophic kill or resource failure may recover an earlier sealed checkpoint and marks statistics incomplete; no claim that all in-memory points survive arbitrary hardware/process failure is made.

A queue rejects a FIFO or other nonregular source before untimed preflight hashing; byte caps apply there. jobs remains 1. Multiple workers share the declared core budget, and their service intervals can overlap.

## Exact R2 and bounded state

The signed definition remains max_j(w_j (f_j-o_j)/s_j). A new point is normalized once, and zero weights are handled sparsely while preserving their exact zero contribution. In particular, singleton weights still have max(y_j,0) for arbitrary signed normalization; reliable lower-bound normalization makes y_j nonnegative.

Cache minima are monotone because archive removals occur only under exact dominance. The seen-key set contains only the current full archive, not every historically admitted vector. Updates remain atomic, and independent full-archive rational recomputation is available for audit. Fixed validates the same supplied reward specification without calculating online gains.

For m singleton weights plus 32 dense weights, new-point ASF evaluation uses O(m) active products instead of dense O(m squared) evaluation. The frozen external weight matrix is still dense and has O(m squared) storage/initialization cost. Arbitrary finite dimension does not mean dimension-independent resources.

Representative selection maintains each candidate's current minimum distance, reducing repeated farthest-point work from O(A K squared m) to O(A K m). The distance, stable ties, extreme-point rules and capacity remain the same. Full-archive points are never discarded to satisfy work capacity.

## Identity, errors and output

Run protocol is end-to-end-cold-engineering-v10 and queue schema is queue-run-engineering-v10. The source/native hashes, actual m, frozen configuration and reward content remain part of identity. SCIP additionally records the actual interpreter, PySCIPOpt modules, mapped SCIP shared library and versions in an isolated-process runtime fingerprint. Changing that runtime invalidates queue reuse.

Final results/checkpoints retain content digests; queue reuse independently validates assignments, objective order/signs, nondominance and event-stream integrity. Single-run abnormal exit codes remain ERROR=2, HARD_TIMEOUT=124, INTERRUPTED=130 and RESOURCE_*=3. No missing metric is represented as a false zero.

The supported installation is a built source checkout, optionally installed editable. A wheel containing only the Python package does not contain the C++ sources/binary and is not a standalone solver distribution.

## Validation

The independent local engineering delivery contains reproducible regression tests, frozen functional validation and raw audit records. Benchmark inputs and historical experiment results are outside the clean solver repository. Exactness and interface tests do not establish performance superiority, parallel speedup or exhaustive verification of every finite dimension.

# Accepted LP input

The parser reads one multiobjective LP file with a shared variable and constraint section. It handles Minimize or Maximize objective blocks, including canonical quadratic terms, linear terms and constants. `[quadratic expression] / 2` is divided by two exactly once; ordinary constraint brackets have no implicit half. Unknown syntax is rejected.

The solver requires at least two objectives and has no fixed maximum objective count. An exact metadata suffix `Sense=min` or `Sense=max` can override the common Minimize/Maximize header for an individual `OBJn`, enabling mixed directions. Other metadata remains rejected. The parser can still read a one-objective LP for inspection, but the solver refuses to search it.

Binary declarations imply bounds [0,1]. General integers retain the explicit bounds or LP default lower bound 0; the parser does not invent a finite upper bound. Decimal coefficients are represented as exact rational numbers. The coordinator re-evaluates every candidate against the original variables, bounds, constraints and objectives before adding it to the archive.

Input limits cover source bytes, total sparse terms, variables, constraints and terms per expression. Exceeding a limit is a resource rejection, not a proof of infeasibility. Generic LP support does not by itself prove equivalence to any source format from which an LP was converted.

Objective count consumes source bytes, sparse-term memory, protocol parsing, exact validation and reward time. C++ LOAD allocates vectors using the parsed count and rejects malformed counts; C++ `int` representation and actual resource limits remain finite. An R2 specification must match source SHA-256, model fingerprint and all objective directions, and supply one finite origin and positive scale per objective plus a nonempty finite list of normalized nonnegative rational weights.


## Bootstrap compatibility
LS-IQCQP default bootstrap_strategy=persistent_unit_v2 permits early tentative first-feasible export from the immutable unit-direction task; feasibility_slice_seconds=10 remains the maximum unsuccessful allowance. persistent_unit_v1 and legacy_v1 are explicit compatibility choices. No new input model class is added. Structure-enhanced initial assignments use the same exact original feasibility and reliable R2 lower-bound checks as ordinary candidates, including when native loading later reaches the deadline.

## v12 saved-run and request contract

The output group includes a frozen `.request` sidecar; only new output paths are accepted. Final/checkpoint content digests and exact original-model sample checks protect queue reuse. Model metadata unavailable before parsing is explicitly null on supervisor failure. Run/queue identity is v12, so older outputs cannot be silently reused. See BATCH_SAFETY_SPEC.md for status codes, interruption recovery and resource limits. Objective number remains any finite m>=2 subject to actual resources; the file transport removes the argv length limitation.

LS-IQCQP Generic supports direction, epsilon and finite integer-neighborhood PLS. Structure-enhanced requires an exactly recognized structure. Queue preflight accepts only regular files within the configured byte cap. See ENGINEERING_SPEC.md for deadline and resource semantics.

# Apels-IQCQP

Apels-IQCQP is an approximate multiobjective integer quadratic solver. The **A** in Apels means Adaptive. A Python coordinator schedules persistent C++ LS-IQCQP workers, validates saved assignments with exact rational arithmetic, and maintains a nondominated sample archive.

The default R2 scheduler supports any finite objective count **m >= 2**, subject to configured time, memory and input limits. Inputs may have binary/general integer variables, mixed minimization/maximization objectives and quadratic constraints. See [the input contract](docs/INPUT_CONTRACT.md) for the supported LP dialect.

## Build on Linux / WSL

Requires Python 3.11+, g++ (C++17), and GSL headers/libraries. On Ubuntu the system packages are `python3-venv`, `g++`, and `libgsl-dev`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python scripts/build_native.py
```

Build products stay in `build/`. The solver rejects a stale or modified native binary; rebuild after changing native sources or the build script. The supported installation is a built source checkout, optionally installed editable. Python runtime dependencies are standard library only. The bundled LS-IQCQP source retains its [MIT license](third_party/ls-iqcqp/LICENSE).

## Batch runs

`prepare` scans a directory recursively, freezes one R2 specification per model, and writes a manifest for all requested seeds. It references absolute source paths without copying any input. Preparation errors are recorded and valid models remain runnable. Use `--pattern` to select a subset.

For example, the following permits large sparse models and allocates one worker/core, up to 2 GiB virtual memory per process and 3 GiB resident memory for the process tree:

```bash
.venv/bin/apels-iqcqp prepare /mnt/d/path/to/benchmark \
  --pattern '*.lp' --budget 60 --seeds 1,2,3 \
  --max-terms 1500000 --max-expression-terms 1000000 \
  --memory-mib 2048 --tree-memory-mib 3072 \
  --output /path/outside/repo/batch.json
.venv/bin/apels-iqcqp queue /path/outside/repo/batch.json \
  --output /path/outside/repo/results
```

Choose resource values that fit the host. Untimed preparation and queue preflight parsing also enforce the per-process memory limit and report failures per input. Multiple native workers can be requested with `--workers 2 --core-budget 2`; instances run serially (`jobs=1`) to keep resource ownership explicit. All input-limit flags are shared by `prepare`, `inspect`, `run` and `scripts/generate_r2_spec.py`.

Default normalization (`--normalization box`) derives exact objective intervals from finite integer variable boxes. For models whose objective variables lack finite bounds, either provide explicit units via `scripts/generate_r2_spec.py --explicit`, or select **`--normalization coefficient`** when preparing the batch. This uses the objective constant as origin and the L1 norm of nonconstant coefficients as a positive fixed scale. It is a search heuristic, does not assert objective bounds and does not alter variable domains. The choice is recorded in every result.

By default a failed instance does not stop later runs. `queue-summary.json` records each status, result path, sample count, error and reuse decision. Exit 2 indicates at least one failure or preparation error. Set `continue_on_error: false` in the manifest for fail-fast operation. Ctrl-C stops the queue with exit 130.

Repeat the same `queue` command to resume: matching successful runs are independently verified and reused, failed or damaged attempts receive new filenames. Existing results are preserved. An output directory accepts only one manifest and one active queue. To change the budget/configuration, use a new output directory.

## Single runs and results

`prepare` also accepts a single LP file. Alternatively, generate a fixed specification, place it in an algorithm configuration under `r2_spec`, and invoke:

```bash
.venv/bin/apels-iqcqp run /path/to/model.lp --budget 60 --seed 1 \
  --algorithm-config /path/to/config.json --output /path/to/new-result.json
```

Each output path must be new. Results contain original objective vectors, assignments, the frozen configuration, timing and resource observations. A cheap initial assignment is validated and checkpointed before native startup. Search accepts samples only before the global deadline; cleanup, recovery and serialization can add reporting time. `COMPLETED` with `NO_FEASIBLE_SAMPLES_FOUND` is an explicit empty result, not an optimality or general infeasibility claim. High-dimensional HV is not needed to solve; optional offline exact HV supports 2-4 objectives.

See [batch safety](docs/BATCH_SAFETY_SPEC.md), [engineering details](docs/ENGINEERING_SPEC.md), and [R2 normalization](docs/MANY_OBJECTIVE_SPEC.md). Run/queue identity is v12 and versioned fingerprints prevent reuse of results produced by a different implementation.

## Verification

Regression tests construct temporary inputs and remove their artifacts:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/test_native.py
.venv/bin/python scripts/build_native.py --sanitize
.venv/bin/python scripts/test_native.py --sanitize
```

The repository contains solver source, current documentation, build tooling and regression test source only. Benchmark files, generated examples, logs, historical reports, results, virtual environments and binaries are not committed. Store experiments outside the checkout.

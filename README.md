# apels-iqcqp: multiobjective integer quadratic solver

A Python coordinator runs persistent C++ LS-IQCQP workers, validates every saved assignment against the original integer quadratic model with exact rational arithmetic, and maintains the complete nondominated sample archive. Fixed scheduling and Adaptive-R2 accept any finite objective count m ≥ 2 within actual resource limits. Legacy Adaptive-HV is supported for two objectives. The optional SCIP backend needs a separately installed PySCIPOpt environment.

The output contains validated heuristic samples. It does not certify a full Pareto front, global optimum, or infeasibility. Supported inputs are the documented LP integer quadratic model class; continuous variables and arbitrary nonlinear expressions are outside this release.

## Build

On Ubuntu/Linux, install Python 3.11+, g++ with C++17, and GSL headers/libraries. The bundled LS-IQCQP source retains its upstream MIT license in third_party/ls-iqcqp/LICENSE.

~~~bash
python3 -m venv .venv
.venv/bin/python scripts/build_native.py
.venv/bin/python -m pip install -e .
~~~

The build generates build/ls_worker and native/upstream.patch in this checkout. Python run-time dependencies are from the standard library. The optional SCIP worker uses a PySCIPOpt interpreter selected with MO_IQCQP_SCIP_PYTHON.

## Run

Provide your own LP input; see the input contract in docs/INPUT_CONTRACT.md. Every output path must be new. For Fixed:

~~~bash
.venv/bin/apels-iqcqp run /path/to/model.lp \
  --budget 60 --seed 1 --output /path/to/new-result.json
~~~

For Adaptive-R2, first freeze a model-specific R2 specification. The default uses exact finite integer-box bounds; models without such bounds require explicit finite origin, positive scale, and coordinate metadata as described in docs/MANY_OBJECTIVE_SPEC.md. Supply the generated JSON in an algorithm configuration with scheduler_mode set to adaptive_r2 and r2_spec set to that JSON object.

~~~bash
.venv/bin/python scripts/generate_r2_spec.py \
  /path/to/model.lp /path/to/new-r2-spec.json
.venv/bin/python -c 'import json,sys; json.dump({"scheduler_mode":"adaptive_r2","r2_spec":json.load(open(sys.argv[1]))},open(sys.argv[2],"x"),indent=2)' \
  /path/to/new-r2-spec.json /path/to/new-r2-config.json
.venv/bin/apels-iqcqp run /path/to/model.lp \
  --budget 60 --seed 1 --algorithm-config /path/to/new-r2-config.json \
  --output /path/to/new-r2-result.json
~~~

Run .venv/bin/apels-iqcqp --help for CLI commands. The JSON result includes every original objective value, assignment, frozen configuration, worker request and resource records. High-dimensional HV is not computed unless a supported offline metric is explicitly requested. Current run and queue protocol is v10; saved content, interruption behavior and status codes are in docs/BATCH_SAFETY_SPEC.md.

This repository contains the solver only. No benchmark instances, example LPs, historical results, virtual environment or compiled binary are committed.

The v10 engineering contract is in [docs/ENGINEERING_SPEC.md](docs/ENGINEERING_SPEC.md). Generic PLS is implemented for both backends; the optional Structure-enhanced variant requires LS-IQCQP. Runtime identity includes the actual SCIP installation when that backend is selected. The documented installation is a built source checkout with an optional editable Python install.

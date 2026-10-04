# LS-IQCQP: Local Search for Integer Quadratic Programming

## Integration in Apels-IQCQP

These sources retain the upstream license and include local correctness fixes for integer bound clipping, equality scoring, binary inequality preservation, repeated pair coefficients and degenerate search states. Apels-IQCQP uses `scripts/build_native.py` at the repository root to generate deadline-aware persistent step methods, callbacks and process-local randomness. The adapter supplies complete objective/constraint indices; it does not use the standalone LP reader below. Regression sources are in the root `tests/` directory.

## Upstream standalone description

LS-IQCQP is a local search algorithm implementation for solving General Integer Quadratic Programming. The algorithm supports multiple search strategies including greedy strategy and tabu search strategy.

## Compilation

### Dependencies

Ensure the following libraries are installed on your system:
- GSL (GNU Scientific Library)

### Compilation

Using Makefile (recommended):

```bash
make
```

## Usage

After building, the executable is in `build/LS-IQCQP`.

### Basic Syntax

```bash
./build/LS-IQCQP <cutoff> <tabu_flag> <filename>
```

### Parameters

- `cutoff`: Time limit in seconds
- `tabu_flag`: Tabu search strategy flag (`0` = disable, `1` = enable)
- `filename`: Input problem file path

### Examples

```bash
# Disable tabu search strategy with 300 seconds time limit
./build/LS-IQCQP 300 0 problem.lp

# Enable tabu search strategy with 300 seconds time limit
./build/LS-IQCQP 300 1 problem.lp
```

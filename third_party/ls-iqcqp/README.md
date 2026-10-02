# LS-IQCQP: Local Search for Integer Quadratic Programming

## Description

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

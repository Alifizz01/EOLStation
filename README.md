# eol-sequencer

A small end-of-line production test sequencer in Python, modelled on the concepts of
NI TestStand. It runs a YAML test sequence against simulated boards through a simulated
SCPI DAQ/DMM, writes a report per unit and a station log, and summarises a batch with
yield, retest rate, a failure Pareto and Cpk.

I built it to learn how a production test station is structured: sequence groups, step
types, limits, retries, retest flow and the statistics a test engineer looks at after a
shift.

## What it does

- `sequences/board_eol.yaml`: example sequence with Setup / Main / Cleanup groups,
  station ID, fixture ID and a serial number pattern.
- `eol/sequence.py`: parser and validator (unknown types, calls, keys, missing or
  inverted limits, duplicate names and YAML errors are rejected with the step named),
  and limit evaluation.
- `eol/station.py`: PyVISA driver for the DAQ, the step calls, and the per-unit
  execution: retries per step with attempt count, stop-on-fail or continue per step,
  Cleanup always runs, an exception in a step becomes an Error result instead of
  stopping the station, simulated operator prompts and simulated barcode scan checked
  against `serial_pattern`.
- `eol/dut.py`: simulated board with three rails (5V0, 3V3, 1V8), idle current,
  three components and a firmware version. Unit-to-unit spread is about 0.5 % on the
  rails. Defect injection: rail out of tolerance, missing component, intermittent
  fixture contact (reads 0 V at random, usually fixed by a re-seat), wrong firmware,
  high current.
- `eol/daqsim.py`: SCPI DAQ simulator vendored unchanged from my
  [daq-testbench](https://github.com/Alifizz01/daq-testbench). The DUT "drives" a DAQ
  input via `SOUR:VOLT`, the station reads it back with `MEAS:VOLT?`, so measurements
  carry the simulator's offset, gain error and noise. Current is read as a voltage
  across a 0.1 ohm shunt on the 0.1 V range.
- `eol/__main__.py`: batch runner. A failed unit is re-seated and retested once.
- Outputs in `out/`: `units/<serial>_run<n>.json` and `.md`, `station_log.csv` (one row
  per step per run), `batch_summary.md` and `.json`.

## Run

```
pip install -r requirements.txt
pytest -q
python -m eol sequences/board_eol.yaml --units 200 --seed 7 --out out
python -m eol sequences/board_eol.yaml --units 50 --rates '{"missing_component": 0.2}'
```

## Mapping to TestStand concepts

| TestStand | Here |
|---|---|
| Sequence file, Setup / Main / Cleanup step groups | YAML file with `setup`, `main`, `cleanup` lists |
| Numeric Limit Test, Pass/Fail Test, String Value Test, Action | `numeric`, `passfail`, `string`, `action` step types |
| Comparison types (GELE, GTLT, GE, LT, EQ, ...) | same names in `comp`, with `low`, `high`, `units` |
| Step loop / retry on fail | `retries: N`, attempts recorded per step |
| Step failure causes sequence failure / skip | `on_fail: stop` (default) skips the remaining Main steps; `continue` keeps going |
| Code modules / adapters | Python functions in `CALLS` |
| Process model (serial number entry, UUT loop, report, database logging) | `run_batch`: serial scan, per-unit run, retest, report files, CSV station log |
| Station globals | station and fixture ID in the sequence file |
| Operator interface | not built; prompts are auto-acknowledged and logged |

## Sample batch summary (200 units, seed 7, default defect rates)

| Metric | Value |
|---|---|
| Units | 200 |
| First-pass yield | 88.5% |
| Final yield (after retest) | 91.0% |
| Retest rate | 11.5% |
| Step retries used (run 1) | 36 |

Failure Pareto (first run, first failing step): Rail 5V0 5, Idle current 4,
Connector present 3, Firmware version 3, Rail 1V8 3, Rail 3V3 3, ETH PHY present 1,
PMIC present 1.

| Step | n | Mean | Std | Low | High | Units | Cpk |
|---|---|---|---|---|---|---|---|
| Rail 5V0 | 195 | 4.999 | 0.0253 | 4.85 | 5.15 | V | 1.965 |
| Rail 3V3 | 192 | 3.299 | 0.0174 | 3.2 | 3.4 | V | 1.9 |
| Rail 1V8 | 189 | 1.801 | 0.00927 | 1.746 | 1.854 | V | 1.899 |
| Idle current | 185 | 180.6 | 5.7 | 140 | 230 | mA | 2.372 |

Definitions: first-pass yield = units passing on run 1 / units. Final yield = units
whose last run passed / units. Retest rate = units run more than once / units. Cpk =
min(USL - mean, mean - LSL) / (3 * s), sample standard deviation, one-sided when only
one limit exists. Cpk here uses passing measurements only, because 0 V contact faults
and defective units would otherwise dominate the spread; that makes it optimistic.
The retry count shows intermittent contacts being absorbed by step retries, which is
also how retries can hide a fixture problem on a real line.

## Tests

52 pytest tests: every comparison type at and around its limits, non-numeric and NaN
values, string and pass/fail strictness, 13 sequence parsing error cases, retry
recovery and exhaustion, stop-on-fail vs continue with Cleanup still running, step
exceptions, operator prompts and serial validation, yield / Pareto / Cpk on hand
computed data, and end-to-end batches (all good units give 100 % yield; five injected
defects give exactly 35/40 first pass and 36/40 final). CI runs them on Python 3.11 and
3.12 and uploads a sample batch as an artifact.

## Limitations

- Simulated DUT and simulated instruments only. Nothing has run against real hardware
  or on a production line.
- This is not NI TestStand and does not read TestStand sequence files. It borrows the
  concepts to show I understand them.
- Limits and defect rates are illustrative, not from any real product.
- No operator UI, no database, no MES integration, no parallel or batch fixtures, no
  measurement uncertainty or guard banding, no Gage R&R.
- Not affiliated with Axis Communications or National Instruments.

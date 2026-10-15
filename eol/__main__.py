"""Batch runner: python -m eol sequences/board_eol.yaml --units 200 --out out"""
import argparse
import json
import os
import shutil

from . import dut as dutmod
from . import report, sequence, stats
from .station import CALLS, Daq, run_unit

DEFAULT_RATES = {"rail_out_of_tol": 0.03, "missing_component": 0.02,
                 "intermittent_contact": 0.06, "wrong_firmware": 0.01, "high_current": 0.02}


def run_batch(seq, units, out, seed=1, max_runs=2, resource=None):
    """Test every unit; a failed unit is re-seated and retested up to max_runs total."""
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    log = os.path.join(out, "station_log.csv")
    results = []
    daq = Daq(resource, seed=seed)
    try:
        for u in units:
            for run in range(1, max_runs + 1):
                if run > 1:
                    u.reseat()
                r = run_unit(seq, u, daq, run=run)
                results.append(r)
                report.write_unit(r, out)
                report.append_log(r, log)
                if r.passed:
                    break
    finally:
        daq.close()
    sm = {"sequence": seq.name, "station": seq.station, "fixture": seq.fixture, "seed": seed,
          "yield": stats.yields(results), "pareto": stats.pareto(results),
          "capability": stats.numeric_capability(results),
          "retries_used": sum(s.attempts - 1 for r in results if r.run == 1 for s in r.steps
                              if s.attempts > 1)}
    with open(os.path.join(out, "batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump(sm, f, indent=2)
    with open(os.path.join(out, "batch_summary.md"), "w", encoding="utf-8") as f:
        f.write(report.summary_markdown(sm))
    return sm, results


def main(argv=None):
    p = argparse.ArgumentParser(prog="eol")
    p.add_argument("sequence")
    p.add_argument("--units", type=int, default=100)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", default="out")
    p.add_argument("--rates", help="JSON dict of defect rates, e.g. '{\"missing_component\": 0.1}'")
    a = p.parse_args(argv)
    seq = sequence.load(a.sequence, calls=CALLS)
    rates = json.loads(a.rates) if a.rates else DEFAULT_RATES
    units = dutmod.make_batch(a.units, rates, seed=a.seed)
    sm, _ = run_batch(seq, units, a.out, seed=a.seed)
    print(report.summary_markdown(sm))


if __name__ == "__main__":
    main()

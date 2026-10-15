"""Batch statistics: yield, retest rate, failure Pareto, Cpk."""
import statistics
from collections import Counter


def yields(results):
    """results: UnitResults over all runs. First pass = run 1 passed; final = last run passed."""
    by_unit = {}
    for r in results:
        by_unit.setdefault(r.serial, []).append(r)
    n = len(by_unit)
    if n == 0:
        return {"units": 0, "first_pass_yield": 0.0, "final_yield": 0.0, "retest_rate": 0.0}
    first = sum(1 for runs in by_unit.values() if min(runs, key=lambda r: r.run).passed)
    final = sum(1 for runs in by_unit.values() if max(runs, key=lambda r: r.run).passed)
    retested = sum(1 for runs in by_unit.values() if len(runs) > 1)
    return {"units": n, "first_pass_yield": first / n, "final_yield": final / n,
            "retest_rate": retested / n}


def pareto(results, run=1):
    """Failure count per first failing step for the given run, most frequent first."""
    c = Counter(r.first_failure for r in results if r.run == run and not r.passed)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def cpk(values, low=None, high=None):
    """Cpk using sample std dev. One-sided if only one limit. None if undefined."""
    if len(values) < 2 or (low is None and high is None):
        return None
    mu, sd = statistics.fmean(values), statistics.stdev(values)
    if sd == 0:
        return None
    sides = []
    if high is not None:
        sides.append((high - mu) / (3 * sd))
    if low is not None:
        sides.append((mu - low) / (3 * sd))
    return min(sides)


def numeric_capability(results, run=1):
    """Cpk per numeric step over passing measurements of the given run.

    Defective units (0 V contacts, missing parts) would swamp the spread, so only Passed
    values are used. That describes the good population and is optimistic by construction.
    """
    vals, lims = {}, {}
    for r in results:
        if r.run != run:
            continue
        for s in r.steps:
            if s.type == "numeric" and s.status == "Passed":
                vals.setdefault(s.name, []).append(s.value)
                lims[s.name] = (s.low, s.high, s.units)
    out = {}
    for name, v in vals.items():
        lo, hi, units = lims[name]
        c = cpk(v, lo, hi)
        out[name] = {"n": len(v), "mean": statistics.fmean(v),
                     "std": statistics.stdev(v) if len(v) > 1 else 0.0,
                     "low": lo, "high": hi, "units": units,
                     "cpk": None if c is None else round(c, 3)}
    return out

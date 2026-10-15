"""Per-unit reports (JSON + Markdown), station log CSV, batch summary."""
import csv
import json
import os
from dataclasses import asdict

LOG_FIELDS = ["start", "station", "fixture", "serial", "run", "group", "step", "type", "value",
              "low", "high", "units", "comp", "status", "attempts", "message"]


def _fmt(v):
    return f"{v:.4g}" if isinstance(v, float) else ("" if v is None else str(v))


def unit_markdown(r):
    verdict = "PASS" if r.passed else "FAIL"
    lines = [f"# Test report {r.serial} (run {r.run})", "",
             f"- Result: **{verdict}**",
             f"- Sequence: {r.sequence}", f"- Station / fixture: {r.station} / {r.fixture}",
             f"- Start: {r.start}, duration {r.duration_s} s", "",
             "| Group | Step | Value | Low | High | Units | Status | Attempts |",
             "|---|---|---|---|---|---|---|---|"]
    for s in r.steps:
        lines.append(f"| {s.group} | {s.name} | {_fmt(s.value)} | {_fmt(s.low)} | {_fmt(s.high)} "
                     f"| {s.units} | {s.status} | {s.attempts} |")
    return "\n".join(lines) + "\n"


def write_unit(r, out):
    d = os.path.join(out, "units")
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, f"{r.serial}_run{r.run}")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(asdict(r) | {"passed": r.passed}, f, indent=2)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(unit_markdown(r))


def append_log(r, path):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, LOG_FIELDS)
        if new:
            w.writeheader()
        for s in r.steps:
            w.writerow({"start": r.start, "station": r.station, "fixture": r.fixture,
                        "serial": r.serial, "run": r.run, "group": s.group, "step": s.name,
                        "type": s.type, "value": _fmt(s.value), "low": _fmt(s.low),
                        "high": _fmt(s.high), "units": s.units, "comp": s.comp,
                        "status": s.status, "attempts": s.attempts, "message": s.message})


def summary_markdown(sm):
    y = sm["yield"]
    lines = [f"# Batch summary: {sm['sequence']}", "",
             f"Station {sm['station']}, fixture {sm['fixture']}, seed {sm['seed']}", "",
             "| Metric | Value |", "|---|---|",
             f"| Units | {y['units']} |",
             f"| First-pass yield | {y['first_pass_yield']:.1%} |",
             f"| Final yield (after retest) | {y['final_yield']:.1%} |",
             f"| Retest rate | {y['retest_rate']:.1%} |",
             f"| Step retries used (run 1) | {sm['retries_used']} |", "",
             "## Failure Pareto (first run, first failing step)", "",
             "| Step | Failures |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sm["pareto"]]
    lines += ["", "## Numeric step capability (first run, passing measurements only)", "",
              "| Step | n | Mean | Std | Low | High | Units | Cpk |",
              "|---|---|---|---|---|---|---|---|"]
    for k, c in sm["capability"].items():
        lines.append(f"| {k} | {c['n']} | {c['mean']:.4g} | {c['std']:.3g} | {_fmt(c['low'])} "
                     f"| {_fmt(c['high'])} | {c['units']} | {c['cpk']} |")
    return "\n".join(lines) + "\n"

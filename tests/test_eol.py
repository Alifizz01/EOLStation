import json
import math

import pytest

from eol import dut as dutmod
from eol import sequence, stats
from eol.__main__ import run_batch
from eol.sequence import SequenceError, Step, evaluate
from eol.station import CALLS, Daq, StepResult, UnitResult, run_unit

SEQ = "sequences/board_eol.yaml"


def num(comp="GELE", low=1.0, high=2.0):
    return Step("s", "numeric", "x", low=low, high=high, comp=comp)


# ---------- limit evaluation ----------
@pytest.mark.parametrize("comp,value,ok", [
    ("GELE", 1.0, True), ("GELE", 2.0, True), ("GELE", 0.999, False), ("GELE", 2.001, False),
    ("GTLT", 1.0, False), ("GTLT", 2.0, False), ("GTLT", 1.5, True),
    ("GELT", 1.0, True), ("GELT", 2.0, False), ("GTLE", 1.0, False), ("GTLE", 2.0, True),
    ("GE", 1.0, True), ("GE", 0.9, False), ("GT", 1.0, False),
    ("LE", 2.0, True), ("LE", 2.1, False), ("LT", 2.0, False),
    ("EQ", 1.0, True), ("EQ", 1.0000001, False), ("NE", 1.0, False), ("NE", 3.0, True),
])
def test_numeric_comparisons(comp, value, ok):
    assert evaluate(num(comp), value) is ok


@pytest.mark.parametrize("bad", [None, float("nan"), "1.5", True])
def test_numeric_rejects_non_numbers(bad):
    assert evaluate(num(), bad) is False


def test_passfail_string_action():
    assert evaluate(Step("p", "passfail", "x"), True)
    assert not evaluate(Step("p", "passfail", "x"), 1)  # truthy is not enough
    assert evaluate(Step("s", "string", "x", expected="2.4.1"), "2.4.1")
    assert not evaluate(Step("s", "string", "x", expected="2.4.1"), "2.4.1 ")
    assert evaluate(Step("a", "action", "x"), None)


# ---------- sequence parsing ----------
BASE = "name: t\nstation: S\nfixture: F\nmain:\n"


def test_example_sequence_loads():
    s = sequence.load(SEQ, calls=CALLS)
    assert (len(s.setup), len(s.main), len(s.cleanup)) == (2, 8, 2)
    assert s.main[0].retries == 2 and s.main[0].units == "V"


@pytest.mark.parametrize("body,msg", [
    ("  - {name: a, type: bogus, call: power_on}\n", "unknown type"),
    ("  - {name: a, type: action, call: nope}\n", "unknown call"),
    ("  - {name: a, type: numeric, call: power_on, low: 2, high: 1}\n", "low 2 > high 1"),
    ("  - {name: a, type: numeric, call: power_on, comp: GE}\n", "needs numeric 'low'"),
    ("  - {name: a, type: numeric, call: power_on, comp: XX, low: 1}\n", "unknown comparison"),
    ("  - {name: a, type: string, call: read_firmware}\n", "needs 'expected'"),
    ("  - {name: a, type: action, call: power_on, retries: -1}\n", "retries"),
    ("  - {name: a, type: action, call: power_on, on_fail: maybe}\n", "on_fail"),
    ("  - {name: a, type: action, call: power_on, colour: red}\n", "unknown keys"),
    ("  - {type: action, call: power_on}\n", "missing 'name'"),
    ("  - {name: a, type: action, call: power_on}\n  - {name: a, type: action, call: power_off}\n",
     "duplicate"),
    ("  []\n", "no steps"),
    ("  - {name: a, type: action, call: power_on\n", "YAML error"),
])
def test_parse_errors(body, msg):
    with pytest.raises(SequenceError, match=msg):
        sequence.load(BASE + body, calls=CALLS)


def test_missing_top_level():
    with pytest.raises(SequenceError, match="fixture"):
        sequence.load("name: t\nstation: S\nmain: []\n")


# ---------- runner: retries, stop-on-fail, cleanup ----------
@pytest.fixture(scope="module")
def daq():
    d = Daq(seed=3)
    yield d
    d.close()


def seq_of(main, extra=""):
    return sequence.load(
        BASE.replace("main:\n", f"serial_pattern: 'SN\\d{{6}}'\n{extra}main:\n") + main
        + "cleanup:\n  - {name: off, type: action, call: power_off}\n", calls=CALLS)


RAIL = "  - {name: r, type: numeric, call: measure_rail, args: {rail: 3V3}, low: 3.2, high: 3.4, retries: %d}\n"


def test_retry_recovers_intermittent(daq, monkeypatch):
    u = dutmod.Dut("SN000001", 1, {})
    u.powered = True
    seq = seq_of(RAIL % 3)
    reads = iter([False, False, True])
    monkeypatch.setattr(u, "contact_ok", lambda: next(reads))
    r = run_unit(seq, u, daq)
    s = r.steps[0]
    assert (s.status, s.attempts) == ("Passed", 3) and r.passed


def test_retries_exhausted_fail(daq):
    u = dutmod.Dut("SN000001", 1, {"intermittent_contact": 1.0})
    u.powered = True
    r = run_unit(seq_of(RAIL % 2), u, daq)
    assert (r.steps[0].status, r.steps[0].attempts) == ("Failed", 3)
    assert r.steps[0].value == pytest.approx(0.0, abs=1e-3)


def test_stop_on_fail_skips_rest_but_runs_cleanup(daq):
    u = dutmod.Dut("SN000002", 2, {"missing_component": "U3_PMIC"})
    main = ("  - {name: p, type: passfail, call: component_present, args: {ref: U3_PMIC}}\n"
            "  - {name: fw, type: string, call: read_firmware, expected: '2.4.1'}\n")
    r = run_unit(seq_of(main), u, daq)
    assert [s.status for s in r.steps] == ["Failed", "Skipped", "Done"]
    assert r.first_failure == "p"


def test_continue_on_fail_runs_rest(daq):
    u = dutmod.Dut("SN000002", 2, {"missing_component": "U3_PMIC"})
    main = ("  - {name: p, type: passfail, call: component_present, args: {ref: U3_PMIC}, on_fail: continue}\n"
            "  - {name: fw, type: string, call: read_firmware, expected: '2.4.1'}\n")
    r = run_unit(seq_of(main), u, daq)
    assert [s.status for s in r.steps] == ["Failed", "Passed", "Done"] and not r.passed


def test_step_exception_is_error_and_cleanup_runs(daq):
    u = dutmod.Dut("SN000003", 3, {})
    main = "  - {name: r, type: numeric, call: measure_rail, args: {rail: 12V}, low: 0, high: 1}\n"
    r = run_unit(seq_of(main), u, daq)
    assert r.steps[0].status == "Error" and "KeyError" in r.steps[0].message
    assert r.steps[-1].status == "Done"


def test_operator_prompts_and_serial_check(daq):
    seq = sequence.load(SEQ, calls=CALLS)
    prompts = []
    r = run_unit(seq, dutmod.Dut("SN000004", 4, {}), daq, prompts=prompts)
    assert r.passed and len(prompts) == 2
    with pytest.raises(ValueError, match="does not match"):
        run_unit(seq, dutmod.Dut("XX12", 4, {}), daq)


# ---------- statistics on known data ----------
def unit(serial, run, failed_step=None):
    st = [StepResult("a", "main", "action", "Done"),
          StepResult(failed_step or "b", "main", "passfail", "Failed" if failed_step else "Passed")]
    return UnitResult(serial, run, "S", "F", "t", "now", steps=st)


def test_yield_and_pareto_known():
    res = [unit("1", 1), unit("2", 1, "rail"), unit("2", 2),
           unit("3", 1, "rail"), unit("3", 2, "rail"), unit("4", 1, "fw")]
    y = stats.yields(res)
    assert y == {"units": 4, "first_pass_yield": 0.25, "final_yield": 0.5, "retest_rate": 0.5}
    assert stats.pareto(res) == [("rail", 2), ("fw", 1)]
    assert stats.pareto(res, run=2) == [("rail", 1)]


def test_cpk_known():
    v = [9.0, 10.0, 11.0]  # mean 10, sample sd 1
    assert stats.cpk(v, 7.0, 16.0) == pytest.approx(1.0)  # (10-7)/3 limits it
    assert stats.cpk(v, high=13.0) == pytest.approx(1.0)
    assert stats.cpk(v, low=4.0) == pytest.approx(2.0)
    assert stats.cpk([5.0, 5.0], 0, 10) is None
    assert stats.cpk([5.0], 0, 10) is None


# ---------- end to end ----------
def test_batch_all_good_units(tmp_path):
    seq = sequence.load(SEQ, calls=CALLS)
    sm, _ = run_batch(seq, dutmod.make_batch(30, {}, seed=5), str(tmp_path), seed=5)
    assert sm["yield"]["first_pass_yield"] == 1.0 and sm["pareto"] == []
    assert all(c["cpk"] > 1.33 for c in sm["capability"].values())


def test_batch_injected_defects_expected_yield(tmp_path):
    seq = sequence.load(SEQ, calls=CALLS)
    units = dutmod.make_batch(40, {}, seed=9)
    units[0].defects["missing_component"] = "U7_ETH_PHY"
    units[1].defects["rail_out_of_tol"] = ("3V3", 1.08)
    units[2].defects["wrong_firmware"] = "2.3.9"
    units[3].defects["high_current"] = 1.4
    units[4].defects["intermittent_contact"] = 1.0  # always open until re-seated
    units[4].reseat = lambda fix_prob=0.8: units[4].defects.pop("intermittent_contact")
    sm, results = run_batch(seq, units, str(tmp_path), seed=9)
    y = sm["yield"]
    assert y["first_pass_yield"] == pytest.approx(35 / 40)
    assert y["final_yield"] == pytest.approx(36 / 40)  # only the contact fault recovers
    assert y["retest_rate"] == pytest.approx(5 / 40)
    assert dict(sm["pareto"]) == {"ETH PHY present": 1, "Rail 3V3": 1, "Firmware version": 1,
                                  "Idle current": 1, "Rail 5V0": 1}
    saved = json.loads((tmp_path / "batch_summary.json").read_text())
    assert saved["yield"] == y
    log = (tmp_path / "station_log.csv").read_text().splitlines()
    assert len(log) == 1 + 12 * len(results)
    assert (tmp_path / "units" / "SN000001_run2.md").exists()


def test_make_batch_rejects_unknown_defect():
    with pytest.raises(ValueError):
        dutmod.make_batch(1, {"gremlins": 0.5})

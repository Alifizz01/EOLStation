"""Test station: instruments, step calls, sequence execution (the process model)."""
import re
import threading
import time
from dataclasses import dataclass, field

import pyvisa

from . import daqsim
from .sequence import evaluate

SHUNT_OHM = 0.1
RAIL_CH = {"5V0": 1, "3V3": 2, "1V8": 3}
SHUNT_CH = 4


class Daq:
    """PyVISA driver for the SCPI DAQ. Starts the vendored simulator if no resource is given."""

    def __init__(self, resource=None, seed=1):
        self.srv = None
        if resource is None:
            self.srv = daqsim.serve(daqsim.DaqModel(seed=seed), port=0)
            threading.Thread(target=self.srv.serve_forever, daemon=True).start()
            resource = f"TCPIP::127.0.0.1::{self.srv.server_address[1]}::SOCKET"
        self.inst = pyvisa.ResourceManager("@py").open_resource(
            resource, read_termination="\n", write_termination="\n", timeout=5000)
        self.inst.write("*RST")
        self.idn = self.inst.query("*IDN?")

    def apply(self, ch, volts):
        """Simulator only: SOUR:VOLT stands in for the DUT driving the input."""
        self.inst.write(f"SOUR:VOLT {volts},(@{ch})")

    def measure(self, ch, rng=10.0):
        self.inst.write(f"CONF:VOLT:RANG {rng},(@{ch})")
        return float(self.inst.query(f"MEAS:VOLT? (@{ch})"))

    def close(self):
        self.inst.close()
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()


# Step calls: f(ctx, **args) -> value
def _power(ctx, on):
    ctx.dut.powered = on
    return None


def _rail(ctx, rail):
    ch = RAIL_CH[rail]
    ctx.daq.apply(ch, ctx.dut.rail(rail))
    return ctx.daq.measure(ch)


def _current(ctx):
    ctx.daq.apply(SHUNT_CH, ctx.dut.supply_current_ma() / 1000 * SHUNT_OHM)
    return ctx.daq.measure(SHUNT_CH, rng=0.1) / SHUNT_OHM * 1000


CALLS = {
    "power_on": lambda ctx: _power(ctx, True),
    "power_off": lambda ctx: _power(ctx, False),
    "prompt": lambda ctx, message: ctx.operator(message),
    "measure_rail": _rail,
    "supply_current_ma": _current,
    "component_present": lambda ctx, ref: ctx.dut.component_present(ref),
    "read_firmware": lambda ctx: ctx.dut.firmware(),
}


@dataclass
class StepResult:
    name: str
    group: str
    type: str
    status: str  # Passed | Failed | Error | Done | Skipped
    value: object = None
    attempts: int = 0
    low: float | None = None
    high: float | None = None
    units: str = ""
    comp: str = ""
    message: str = ""


@dataclass
class UnitResult:
    serial: str
    run: int
    station: str
    fixture: str
    sequence: str
    start: str
    duration_s: float = 0.0
    steps: list = field(default_factory=list)

    @property
    def passed(self):
        return all(s.status in ("Passed", "Done") for s in self.steps)

    @property
    def first_failure(self):
        return next((s.name for s in self.steps if s.status in ("Failed", "Error")), None)


@dataclass
class Ctx:
    dut: object
    daq: Daq
    operator: object


def auto_operator(log):
    """Simulated operator: records the prompt and acknowledges it."""
    def op(message):
        log.append(message)
        return True
    return op


def scan_serial(seq, raw):
    """Simulated barcode scan: validate against the sequence's serial pattern."""
    s = raw.strip()
    if not re.fullmatch(seq.serial_pattern, s):
        raise ValueError(f"serial '{s}' does not match {seq.serial_pattern}")
    return s


def run_unit(seq, dut, daq, run=1, operator=None, prompts=None):
    serial = scan_serial(seq, dut.serial)
    ctx = Ctx(dut, daq, operator or auto_operator(prompts if prompts is not None else []))
    res = UnitResult(serial, run, seq.station, seq.fixture, seq.name,
                     time.strftime("%Y-%m-%dT%H:%M:%S"))
    t0 = time.perf_counter()
    stopped = False
    for group in ("setup", "main", "cleanup"):
        for step in getattr(seq, group):
            r = StepResult(step.name, group, step.type, "Skipped", low=step.low, high=step.high,
                           units=step.units, comp=step.comp if step.type == "numeric" else "")
            res.steps.append(r)
            if stopped and group != "cleanup":
                continue
            for attempt in range(1, step.retries + 2):
                r.attempts = attempt
                try:
                    r.value = CALLS[step.call](ctx, **step.args)
                    r.message = ""
                except Exception as e:  # a crashing step must not skip cleanup
                    r.status, r.value, r.message = "Error", None, f"{type(e).__name__}: {e}"
                    continue
                if evaluate(step, r.value):
                    r.status = "Done" if step.type == "action" else "Passed"
                    break
                r.status = "Failed"
            if r.status in ("Failed", "Error") and step.on_fail == "stop" and group != "cleanup":
                stopped = True
    res.duration_s = round(time.perf_counter() - t0, 4)
    return res

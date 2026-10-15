"""Simulated device under test with defect injection."""
import random
from dataclasses import dataclass, field

NOMINAL = {"5V0": 5.0, "3V3": 3.3, "1V8": 1.8}
COMPONENTS = ("U3_PMIC", "U7_ETH_PHY", "J2_CONN")
FIRMWARE = "2.4.1"
IDLE_CURRENT_MA = 180.0
DEFECTS = ("rail_out_of_tol", "missing_component", "intermittent_contact", "wrong_firmware",
           "high_current")


@dataclass
class Dut:
    serial: str
    seed: int
    defects: dict = field(default_factory=dict)  # defect name -> detail

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        # unit-to-unit spread, fixed per board (about 0.5 % 1-sigma)
        self.rail_err = {r: self.rng.gauss(0, 0.005) for r in NOMINAL}
        self.current_ma = self.rng.gauss(IDLE_CURRENT_MA, 6.0)
        self.powered = False

    def contact_ok(self):
        p = self.defects.get("intermittent_contact")
        return p is None or self.rng.random() >= p

    def rail(self, name):
        if not self.powered or not self.contact_ok():
            return 0.0
        v = NOMINAL[name] * (1 + self.rail_err[name])
        bad = self.defects.get("rail_out_of_tol")
        if bad and bad[0] == name:
            v *= bad[1]
        return v

    def supply_current_ma(self):
        if not self.powered:
            return 0.0
        return self.current_ma * self.defects.get("high_current", 1.0)

    def component_present(self, ref):
        return self.defects.get("missing_component") != ref

    def firmware(self):
        return self.defects.get("wrong_firmware", FIRMWARE)

    def reseat(self, fix_prob=0.8):
        """Operator re-seats the board in the fixture; usually fixes a bad contact."""
        if "intermittent_contact" in self.defects and self.rng.random() < fix_prob:
            del self.defects["intermittent_contact"]


def make_batch(n, rates, seed=1, prefix="SN"):
    """n simulated units; each defect drawn independently with its rate."""
    unknown = set(rates) - set(DEFECTS)
    if unknown:
        raise ValueError(f"unknown defects {sorted(unknown)}")
    rng = random.Random(seed)
    units = []
    for i in range(n):
        d = {}
        for name in DEFECTS:
            if rng.random() < rates.get(name, 0.0):
                d[name] = {"rail_out_of_tol": lambda: (rng.choice(list(NOMINAL)), rng.choice((0.9, 1.08))),
                           "missing_component": lambda: rng.choice(COMPONENTS),
                           "intermittent_contact": lambda: 0.6,
                           "wrong_firmware": lambda: "2.3.9",
                           "high_current": lambda: 1.4}[name]()
        units.append(Dut(f"{prefix}{i + 1:06d}", seed * 100003 + i, d))
    return units

"""Sequence file parsing and limit evaluation."""
import math
import re
from dataclasses import dataclass, field

import yaml

STEP_TYPES = {"numeric", "passfail", "string", "action"}
# Same names as TestStand numeric limit comparison types.
COMPARISONS = {"GELE": ("low", "high"), "GTLT": ("low", "high"), "GELT": ("low", "high"),
               "GTLE": ("low", "high"), "GE": ("low",), "GT": ("low",), "LE": ("high",),
               "LT": ("high",), "EQ": ("low",), "NE": ("low",)}
GROUPS = ("setup", "main", "cleanup")


class SequenceError(ValueError):
    pass


@dataclass
class Step:
    name: str
    type: str
    call: str
    args: dict = field(default_factory=dict)
    low: float | None = None
    high: float | None = None
    units: str = ""
    comp: str = "GELE"
    expected: str | None = None
    retries: int = 0
    on_fail: str = "stop"  # stop | continue


@dataclass
class Sequence:
    name: str
    station: str
    fixture: str
    serial_pattern: str
    setup: list
    main: list
    cleanup: list

    def steps(self):
        return self.setup + self.main + self.cleanup


def evaluate(step, value):
    """True if value passes the step's limits."""
    if step.type == "action":
        return True
    if step.type == "passfail":
        return value is True
    if step.type == "string":
        return isinstance(value, str) and value == step.expected
    if not isinstance(value, (int, float)) or isinstance(value, bool) or math.isnan(value):
        return False
    lo, hi = step.low, step.high
    return {"GELE": lambda: lo <= value <= hi, "GTLT": lambda: lo < value < hi,
            "GELT": lambda: lo <= value < hi, "GTLE": lambda: lo < value <= hi,
            "GE": lambda: value >= lo, "GT": lambda: value > lo,
            "LE": lambda: value <= hi, "LT": lambda: value < hi,
            "EQ": lambda: value == lo, "NE": lambda: value != lo}[step.comp]()


def _step(d, where, calls):
    if not isinstance(d, dict):
        raise SequenceError(f"{where}: step must be a mapping")
    unknown = set(d) - set(Step.__dataclass_fields__)
    if unknown:
        raise SequenceError(f"{where}: unknown keys {sorted(unknown)}")
    for k in ("name", "type", "call"):
        if k not in d:
            raise SequenceError(f"{where}: missing '{k}'")
    s = Step(**d)
    where = f"{where} '{s.name}'"
    if s.type not in STEP_TYPES:
        raise SequenceError(f"{where}: unknown type '{s.type}'")
    if calls is not None and s.call not in calls:
        raise SequenceError(f"{where}: unknown call '{s.call}'")
    if s.on_fail not in ("stop", "continue"):
        raise SequenceError(f"{where}: on_fail must be stop or continue")
    if not isinstance(s.retries, int) or isinstance(s.retries, bool) or s.retries < 0:
        raise SequenceError(f"{where}: retries must be an integer >= 0")
    if s.type == "numeric":
        if s.comp not in COMPARISONS:
            raise SequenceError(f"{where}: unknown comparison '{s.comp}'")
        for lim in COMPARISONS[s.comp]:
            v = getattr(s, lim)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise SequenceError(f"{where}: comparison {s.comp} needs numeric '{lim}'")
        if len(COMPARISONS[s.comp]) == 2 and s.low > s.high:
            raise SequenceError(f"{where}: low {s.low} > high {s.high}")
    if s.type == "string" and not isinstance(s.expected, str):
        raise SequenceError(f"{where}: string step needs 'expected'")
    return s


def load(path_or_text, calls=None):
    """Parse a sequence from a YAML path or YAML text. calls = allowed call names."""
    text = path_or_text
    if "\n" not in path_or_text:
        with open(path_or_text, encoding="utf-8") as f:
            text = f.read()
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SequenceError(f"YAML error: {e}") from e
    if not isinstance(d, dict):
        raise SequenceError("sequence file must be a mapping")
    for k in ("name", "station", "fixture", "main"):
        if k not in d:
            raise SequenceError(f"missing top-level '{k}'")
    pattern = d.get("serial_pattern", r".+")
    try:
        re.compile(pattern)
    except re.error as e:
        raise SequenceError(f"bad serial_pattern: {e}") from e
    groups = {}
    names = set()
    for g in GROUPS:
        raw = d.get(g) or []
        if not isinstance(raw, list):
            raise SequenceError(f"'{g}' must be a list of steps")
        groups[g] = [_step(x, f"{g}[{i}]", calls) for i, x in enumerate(raw)]
        for s in groups[g]:
            if s.name in names:
                raise SequenceError(f"duplicate step name '{s.name}'")
            names.add(s.name)
    if not groups["main"]:
        raise SequenceError("'main' has no steps")
    return Sequence(d["name"], str(d["station"]), str(d["fixture"]), pattern, **groups)

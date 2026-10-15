"""Vendored from https://github.com/Alifizz01/daq-testbench (daqsim/server.py), same author, unmodified.

Simulated multi-channel DAQ/DMM speaking SCPI over a raw TCP socket.

Reach it with PyVISA via the pyvisa-py backend:
    rm = pyvisa.ResourceManager("@py")
    inst = rm.open_resource("TCPIP::127.0.0.1::5025::SOCKET",
                            read_termination="\\n", write_termination="\\n")

SOUR:VOLT sets the "true" voltage applied to a channel (stands in for an
external calibrator). Offset, gain error, noise and crosstalk are set per model.
SIM:FAULT injects STUCK or DROP faults on a channel.
"""
import argparse
import random
import re
import socketserver
import threading

OVERLOAD = 9.9e37
RANGES = (0.1, 1.0, 10.0, 100.0)


class DaqModel:
    def __init__(self, channels=8, offset=50e-6, gain_err=20e-6, noise_rms=30e-6,
                 crosstalk=1e-6, seed=1):
        self.n = channels
        self.offset, self.gain_err = offset, gain_err
        self.noise_rms, self.crosstalk = noise_rms, crosstalk
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.src = [0.0] * self.n
        self.rng_v = [10.0] * self.n
        self.rate, self.count = 1000.0, 100
        self.fault = {}  # channel index -> "STUCK" | "DROP"
        self.errors = []

    def _sample(self, ch):
        others = sum(v for i, v in enumerate(self.src) if i != ch)
        v = self.src[ch] * (1 + self.gain_err) + self.offset + others * self.crosstalk
        v += self.rng.gauss(0, self.noise_rms)
        return OVERLOAD if abs(v) > self.rng_v[ch] * 1.2 else v

    def fetch(self, ch):
        if self.fault.get(ch) == "STUCK":
            return [0.0] * self.count
        n = self.count
        if self.fault.get(ch) == "DROP":
            n = int(n * 0.9)  # lose 10 % of the block
        return [self._sample(ch) for _ in range(n)]

    def handle(self, line):
        up = line.strip().upper()
        m = re.search(r"\(@([\d,]+)\)", up)
        chans = [int(c) - 1 for c in m.group(1).split(",")] if m else []
        if any(not 0 <= c < self.n for c in chans):
            self.errors.append('-222,"Data out of range"')
            return "ERR" if "?" in up else None
        arg = up.split(None, 1)[1].split(",(@")[0].split("(@")[0].strip() if " " in up else ""
        if up == "*IDN?":
            return f"SIMDAQ,VDAQ-{self.n},SIM0001,0.1"
        if up == "*RST":
            self.reset()
            return None
        if up == "SYST:ERR?":
            return self.errors.pop(0) if self.errors else '0,"No error"'
        if up == "SYST:CHAN?":
            return str(self.n)
        if up.startswith("SOUR:VOLT "):
            for c in chans:
                self.src[c] = float(arg)
            return None
        if up.startswith("CONF:VOLT:RANG?"):
            return str(self.rng_v[chans[0]])
        if up.startswith("CONF:VOLT:RANG "):
            r = float(arg)
            if r not in RANGES:
                self.errors.append('-224,"Illegal parameter value"')
                return None
            for c in chans:
                self.rng_v[c] = r
            return None
        if up.startswith("SAMP:RATE "):
            self.rate = float(arg)
            return None
        if up == "SAMP:RATE?":
            return str(self.rate)
        if up.startswith("SAMP:COUN "):
            self.count = int(arg)
            return None
        if up == "SAMP:COUN?":
            return str(self.count)
        if up.startswith("SIM:FAULT "):
            for c in chans:
                if arg == "NONE":
                    self.fault.pop(c, None)
                else:
                    self.fault[c] = arg
            return None
        if up.startswith("MEAS:VOLT?"):
            v = 0.0 if self.fault.get(chans[0]) == "STUCK" else self._sample(chans[0])
            return f"{v:.9E}"
        if up.startswith("FETC?"):
            return ",".join(f"{v:.9E}" for v in self.fetch(chans[0]))
        self.errors.append('-113,"Undefined header"')
        return "ERR" if "?" in up else None


def serve(model, host="127.0.0.1", port=5025):
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                with model.lock:
                    reply = model.handle(raw.decode())
                if reply is not None:
                    self.wfile.write((reply + "\n").encode())

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=5025)
    a = p.parse_args()
    print(f"SIMDAQ listening on 127.0.0.1:{a.port}")
    serve(DaqModel(), port=a.port).serve_forever()

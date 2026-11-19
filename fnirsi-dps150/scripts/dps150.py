#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyserial>=3.5"]
# ///
"""Driver + CLI for the FNIRSI DPS-150 programmable DC power supply (USB-C, CDC serial).

Run with:   uv run dps150.py <command> ...      (pyserial is installed automatically)
        or: python3 dps150.py <command> ...     (needs `pip install pyserial`)

Protocol notes live in ../references/protocol.md. Everything here was derived from
third-party reverse engineering and has NOT been validated by the vendor.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    sys.exit("pyserial is required: run with `uv run dps150.py ...` or `pip install pyserial`")

# --------------------------------------------------------------------------- protocol
HDR_TX, HDR_RX = 0xF1, 0xF0
CMD_GET, CMD_BAUD, CMD_SET, CMD_SESSION = 0xA1, 0xB0, 0xB1, 0xC1
# NOTE: command 0xC0 puts the unit into firmware-upgrade (DFU) mode. It is deliberately
# NOT defined anywhere in this file. Never send it.

R_VIN, R_VSET, R_ISET, R_OUT, R_TEMP = 0xC0, 0xC1, 0xC2, 0xC3, 0xC4
R_PRESET_BASE = 0xC5            # M1 V=0xC5, M1 I=0xC6, ... M6 I=0xD0
R_OVP, R_OCP, R_OPP, R_OTP, R_LVP = 0xD1, 0xD2, 0xD3, 0xD4, 0xD5
R_BRIGHT, R_VOLUME, R_METER = 0xD6, 0xD7, 0xD8
R_AH, R_WH, R_OUTPUT, R_PROT, R_MODE = 0xD9, 0xDA, 0xDB, 0xDC, 0xDD
R_MODEL, R_HW, R_FW = 0xDE, 0xDF, 0xE0
R_MAXV, R_MAXI = 0xE2, 0xE3
R_ALL = 0xFF

BAUD_INDEX_115200 = 5
PROT_NAMES = ["OK", "OVP", "OCP", "OPP", "OTP", "LVP", "REP"]
PROT_REGS = {"ovp": R_OVP, "ocp": R_OCP, "opp": R_OPP, "otp": R_OTP, "lvp": R_LVP}
PROT_UNITS = {"ovp": "V", "ocp": "A", "opp": "W", "otp": "C", "lvp": "V"}

USB_VID, USB_PID = 0x2E3C, 0x5740
FALLBACK_LIMITS = {"v": 30.0, "i": 5.1, "p": 150.0}   # DPS-150 nameplate


def checksum(reg: int, data: bytes) -> int:
    return (reg + len(data) + sum(data)) & 0xFF


def build_frame(cmd: int, reg: int, data: bytes = b"") -> bytes:
    return bytes([HDR_TX, cmd, reg, len(data)]) + data + bytes([checksum(reg, data)])


def f32(x: float) -> bytes:
    return struct.pack("<f", x)


def parse_frames(buf: bytearray) -> List[dict]:
    """Extract complete, checksum-valid device->host frames from buf (consumed in place).

    Resynchronises byte-by-byte on garbage or bad checksums.
    """
    frames = []
    while True:
        while buf and buf[0] != HDR_RX:
            del buf[0]
        if len(buf) < 5:
            break
        cmd, reg, ln = buf[1], buf[2], buf[3]
        total = 5 + ln
        if len(buf) < total:
            break
        data = bytes(buf[4:4 + ln])
        if buf[4 + ln] != checksum(reg, data) or cmd not in (CMD_GET, CMD_SET, CMD_SESSION, CMD_BAUD):
            del buf[0]          # bad frame -> slip one byte and rescan
            continue
        frames.append({"cmd": cmd, "reg": reg, "data": data})
        del buf[:total]
    return frames


def _f(d: bytes, off: int) -> float:
    return struct.unpack_from("<f", d, off)[0]


def parse_full_dump(d: bytes) -> dict:
    """Decode the 0xFF state blob (offsets agree across cho45 / nasheed-x implementations)."""
    if len(d) < 111:
        raise ValueError(f"full dump too short: {len(d)} bytes")
    s = {
        "vin": _f(d, 0), "vset": _f(d, 4), "iset": _f(d, 8),
        "vout": _f(d, 12), "iout": _f(d, 16), "pout": _f(d, 20), "temp": _f(d, 24),
        "presets": [(_f(d, 28 + 8 * n), _f(d, 32 + 8 * n)) for n in range(6)],
        "ovp": _f(d, 76), "ocp": _f(d, 80), "opp": _f(d, 84), "otp": _f(d, 88), "lvp": _f(d, 92),
        "brightness": d[96], "volume": d[97], "metering_raw": d[98],
        "ah": _f(d, 99), "wh": _f(d, 103),
        "output_on": bool(d[107]), "protection": d[108], "mode_raw": d[109],
    }
    if len(d) >= 119:
        s["max_v"], s["max_i"] = _f(d, 111), _f(d, 115)
    return s


# --------------------------------------------------------------------------- device
def find_port() -> Optional[str]:
    """Autodetect the DPS-150 by USB VID:PID (2E3C:5740, shows up as 'AT32 USB serial')."""
    cands = [p for p in list_ports.comports() if p.vid == USB_VID and p.pid == USB_PID]
    if not cands:
        cands = [p for p in list_ports.comports()
                 if "AT32" in (p.description or "") or "DPS" in (p.description or "").upper()]
    if cands:
        # macOS exposes both /dev/cu.* and /dev/tty.*; prefer cu.* (no carrier wait)
        cands.sort(key=lambda p: (not p.device.startswith("/dev/cu."), p.device))
        return cands[0].device
    return None


class DPS150:
    def __init__(self, port: Optional[str] = None, verbose: bool = False, dtr: bool = False):
        self.port_name = port or find_port()
        if not self.port_name:
            raise RuntimeError(
                "No DPS-150 found (USB VID:PID 2E3C:5740). Check the USB-C DATA cable, that the "
                "unit is powered, and that no other app (FNIRSI software, browser Web Serial tab) "
                "holds the port. Use `dps150.py ports` to list candidates, or pass --port.")
        self.verbose = verbose
        self.ser = serial.Serial()
        self.ser.port = self.port_name
        self.ser.baudrate = 115200
        self.ser.bytesize, self.ser.parity, self.ser.stopbits = 8, "N", 1
        self.ser.timeout = 0.1
        self.ser.rtscts = False
        self.ser.dtr = dtr           # official app leaves DTR low ...
        self.ser.rts = True          # ... and asserts RTS; device may not talk without it
        self.state: Dict[str, object] = {"limits": dict(FALLBACK_LIMITS)}
        self.model = self.hw = self.fw = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._listeners: List[Callable[[dict], None]] = []
        self._last_tx = 0.0
        self._connected = False

    # -- lifecycle
    def open(self) -> "DPS150":
        self.ser.open()
        self.ser.reset_input_buffer()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        self._tx(CMD_SESSION, 0x00, b"\x01")
        time.sleep(0.2)
        self._tx(CMD_BAUD, 0x00, bytes([BAUD_INDEX_115200]))
        self._connected = True
        for reg in (R_MODEL, R_HW, R_FW):
            self._tx(CMD_GET, reg, b"\x00")
        self.refresh()
        deadline = time.time() + 2.0
        while "vset" not in self.state and time.time() < deadline:
            time.sleep(0.05)
        if "vset" not in self.state:
            self.close()
            raise RuntimeError(
                f"Opened {self.port_name} but the device did not answer. Another program may be "
                "using it, the cable may be charge-only, or the unit may need a power cycle.")
        return self

    def close(self) -> None:
        try:
            if self._connected and self.ser.is_open:
                self._tx(CMD_SESSION, 0x00, b"\x00")
                time.sleep(0.05)
        except Exception:
            pass
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.ser.is_open:
            self.ser.close()
        self._connected = False

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # -- I/O
    def _tx(self, cmd: int, reg: int, data: bytes = b"") -> None:
        frame = build_frame(cmd, reg, data)
        gap = 0.05 - (time.time() - self._last_tx)      # device wants ~50 ms between writes
        if gap > 0:
            time.sleep(gap)
        if self.verbose:
            print("TX", frame.hex(" "), file=sys.stderr)
        self.ser.write(frame)
        self.ser.flush()
        self._last_tx = time.time()

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception:
                break
            if not chunk:
                continue
            self._buf.extend(chunk)
            for fr in parse_frames(self._buf):
                if self.verbose:
                    print("RX", f"{fr['cmd']:02x} {fr['reg']:02x}", fr["data"].hex(" "), file=sys.stderr)
                self._handle(fr)

    def _handle(self, fr: dict) -> None:
        reg, d = fr["reg"], fr["data"]
        upd: Dict[str, object] = {}
        try:
            if reg == R_VIN and len(d) == 4: upd["vin"] = _f(d, 0)
            elif reg == R_VSET and len(d) == 4: upd["vset"] = _f(d, 0)
            elif reg == R_ISET and len(d) == 4: upd["iset"] = _f(d, 0)
            elif reg == R_OUT and len(d) >= 12:
                upd.update(vout=_f(d, 0), iout=_f(d, 4), pout=_f(d, 8), _t_out=time.time())
            elif reg == R_TEMP and len(d) == 4: upd["temp"] = _f(d, 0)
            elif reg == R_AH and len(d) == 4: upd["ah"] = _f(d, 0)
            elif reg == R_WH and len(d) == 4: upd["wh"] = _f(d, 0)
            elif reg == R_OUTPUT and len(d) == 1: upd["output_on"] = bool(d[0])
            elif reg == R_PROT and len(d) == 1: upd["protection"] = d[0]
            elif reg == R_MODE and len(d) == 1: upd["mode_raw"] = d[0]
            elif reg == R_MODEL: self.model = d.decode("ascii", "replace").strip("\x00")
            elif reg == R_HW: self.hw = d.decode("ascii", "replace").strip("\x00")
            elif reg == R_FW: self.fw = d.decode("ascii", "replace").strip("\x00")
            elif reg == R_MAXV and len(d) == 4: self.state["limits"]["v"] = _f(d, 0)
            elif reg == R_MAXI and len(d) == 4: self.state["limits"]["i"] = _f(d, 0)
            elif reg == R_ALL:
                dump = parse_full_dump(d)
                if "max_v" in dump and 0 < dump["max_v"] <= 60: self.state["limits"]["v"] = dump["max_v"]
                if "max_i" in dump and 0 < dump["max_i"] <= 10: self.state["limits"]["i"] = dump["max_i"]
                upd.update({k: v for k, v in dump.items() if k not in ("max_v", "max_i")})
                upd["_t_out"] = time.time()
        except Exception as e:  # never kill the reader on a malformed frame
            if self.verbose:
                print("parse error:", e, file=sys.stderr)
            return
        if upd:
            with self._lock:
                self.state.update(upd)
            snap = self.snapshot()
            for cb in list(self._listeners):
                cb(snap)

    # -- reads
    def on_update(self, cb: Callable[[dict], None]) -> None:
        self._listeners.append(cb)

    def refresh(self) -> None:
        self._tx(CMD_GET, R_ALL, b"\x00")

    def snapshot(self) -> dict:
        with self._lock:
            s = dict(self.state)
        s["mode"] = None if "mode_raw" not in s else ("CC" if s["mode_raw"] == 0 else "CV")
        p = s.get("protection", 0)
        s["protection_name"] = PROT_NAMES[p] if isinstance(p, int) and p < len(PROT_NAMES) else str(p)
        return s

    def wait_fresh(self, timeout: float = 2.0) -> dict:
        """Request a full dump and wait until it lands."""
        t0 = time.time()
        self.refresh()
        while time.time() - t0 < timeout:
            if self.state.get("_t_out", 0) > t0:
                break
            time.sleep(0.02)
        return self.snapshot()

    # -- writes (all range-checked against the limits the device reports)
    def _check(self, name: str, val: float, hi: float) -> None:
        if not (0.0 <= val <= hi):
            raise ValueError(f"{name}={val} outside 0..{hi} (device limit)")

    def set_voltage(self, v: float) -> None:
        self._check("voltage", v, self.state["limits"]["v"])
        self._tx(CMD_SET, R_VSET, f32(v))

    def set_current(self, i: float) -> None:
        self._check("current", i, self.state["limits"]["i"])
        self._tx(CMD_SET, R_ISET, f32(i))

    def set_output(self, on: bool) -> None:
        self._tx(CMD_SET, R_OUTPUT, b"\x01" if on else b"\x00")

    def set_protection(self, name: str, value: float) -> None:
        self._tx(CMD_SET, PROT_REGS[name], f32(value))

    def set_preset(self, n: int, v: float, i: float) -> None:
        if not 1 <= n <= 6:
            raise ValueError("preset must be 1..6")
        self._check("voltage", v, self.state["limits"]["v"])
        self._check("current", i, self.state["limits"]["i"])
        self._tx(CMD_SET, R_PRESET_BASE + 2 * (n - 1), f32(v))
        self._tx(CMD_SET, R_PRESET_BASE + 2 * (n - 1) + 1, f32(i))

    def set_metering(self, run: bool) -> None:
        self._tx(CMD_SET, R_METER, b"\x01" if run else b"\x00")

    def set_brightness(self, level: int) -> None:
        self._tx(CMD_SET, R_BRIGHT, bytes([max(0, min(255, level))]))

    def set_volume(self, level: int) -> None:
        self._tx(CMD_SET, R_VOLUME, bytes([max(0, min(255, level))]))

    def apply(self, v: float, i: float, settle: float = 0.3) -> dict:
        """Set current limit first, then voltage, then read back and verify (writes are not echoed)."""
        self.set_current(i)
        self.set_voltage(v)
        time.sleep(settle)
        snap = self.wait_fresh()
        if abs(snap["vset"] - v) > 0.01 or abs(snap["iset"] - i) > 0.001:
            raise RuntimeError(f"readback mismatch: wanted {v} V/{i} A, device reports "
                               f"{snap['vset']:.3f} V/{snap['iset']:.3f} A")
        return snap


# --------------------------------------------------------------------------- logging
LOG_FIELDS = ["t_s", "timestamp", "vin", "vset", "iset", "vout", "iout", "pout", "temp",
              "mode", "output_on", "protection", "ah", "wh"]


def _row(snap: dict, t0: float) -> dict:
    now = time.time()
    r = {"t_s": round(now - t0, 3), "timestamp": datetime.fromtimestamp(now).isoformat(timespec="milliseconds")}
    for k in LOG_FIELDS[2:]:
        key = "protection_name" if k == "protection" else k
        v = snap.get(key)
        r[k] = round(v, 6) if isinstance(v, float) else v
    return r


def log_session(dev: DPS150, path: str, duration: float, poll_hz: Optional[float],
                quiet: bool = False) -> dict:
    """Record to CSV. Default: one row per ~2 Hz telemetry push. --poll-hz: poll the full dump."""
    t0 = time.time()
    rows: List[dict] = []
    lock_t = threading.Event()
    last_t = [0.0]

    def on_push(_snap: dict) -> None:
        if poll_hz is None and dev.state.get("_t_out", 0) != last_t[0]:
            last_t[0] = dev.state["_t_out"]
            lock_t.set()

    dev.on_update(on_push)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        w.writeheader()
        try:
            next_poll = t0
            while time.time() - t0 < duration:
                if poll_hz:
                    dev.refresh()
                    next_poll += 1.0 / poll_hz
                    time.sleep(max(0.0, next_poll - time.time()))
                    snap = dev.snapshot()
                else:
                    lock_t.wait(timeout=1.5)
                    lock_t.clear()
                    snap = dev.snapshot()
                if "vout" not in snap:
                    continue
                r = _row(snap, t0)
                rows.append(r)
                w.writerow(r)
                fh.flush()
                if not quiet:
                    print(f"{r['t_s']:8.2f}s  {r['vout']:7.3f} V  {r['iout']:6.3f} A  "
                          f"{r['pout']:7.3f} W  {r['temp']:5.1f} C  {r['mode']}  "
                          f"{'ON ' if r['output_on'] else 'off'} {r['protection']}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\ninterrupted - CSV is complete up to this point", file=sys.stderr)
    return summarize(rows, path)


def summarize(rows: List[dict], path: str) -> dict:
    if not rows:
        return {"file": os.path.abspath(path), "rows": 0}
    def col(k): return [r[k] for r in rows if isinstance(r.get(k), (int, float))]
    def stats(k):
        c = col(k)
        return {"min": min(c), "max": max(c), "mean": sum(c) / len(c)} if c else None
    energy_wh = 0.0
    for a, b in zip(rows, rows[1:]):                      # trapezoidal integration of Pout
        energy_wh += 0.5 * (a["pout"] + b["pout"]) * (b["t_s"] - a["t_s"]) / 3600.0
    return {"file": os.path.abspath(path), "rows": len(rows), "duration_s": rows[-1]["t_s"],
            "vout": stats("vout"), "iout": stats("iout"), "pout": stats("pout"), "temp": stats("temp"),
            "energy_wh_integrated": round(energy_wh, 6),
            "device_wh_end": rows[-1].get("wh"), "device_ah_end": rows[-1].get("ah")}


# --------------------------------------------------------------------------- CLI
def fmt_status(s: dict) -> str:
    L = s["limits"]
    lines = [
        f"Output       : {'ON' if s.get('output_on') else 'off'}   mode {s.get('mode')}   protection {s.get('protection_name')}",
        f"Set          : {s['vset']:.3f} V / {s['iset']:.3f} A   (device max {L['v']:.1f} V / {L['i']:.2f} A)",
        f"Measured     : {s['vout']:.3f} V  {s['iout']:.3f} A  {s['pout']:.3f} W",
        f"Input / temp : {s['vin']:.2f} V in,  {s['temp']:.1f} C",
        f"Protection   : OVP {s.get('ovp', 0):.2f} V  OCP {s.get('ocp', 0):.3f} A  OPP {s.get('opp', 0):.1f} W  "
        f"OTP {s.get('otp', 0):.0f} C  LVP {s.get('lvp', 0):.2f} V",
        f"Counters     : {s.get('ah', 0):.4f} Ah  {s.get('wh', 0):.4f} Wh",
    ]
    return "\n".join(lines)


def clean(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}


def cmd_ports(a) -> int:
    for p in list_ports.comports():
        tag = "  <-- DPS-150" if (p.vid, p.pid) == (USB_VID, USB_PID) else ""
        vp = f"{p.vid:04X}:{p.pid:04X}" if p.vid else "----:----"
        print(f"{p.device}\t{vp}\t{p.description}{tag}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: autodetect by USB VID:PID 2E3C:5740)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print raw TX/RX frames to stderr")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--dtr", action="store_true", help="assert DTR (default low, like the official app)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ports", help="list serial ports")
    sub.add_parser("info", help="model / hardware / firmware / limits")
    sub.add_parser("status", help="full state snapshot")

    p = sub.add_parser("monitor", help="live readout until Ctrl-C / --duration")
    p.add_argument("--duration", type=float, default=0)

    p = sub.add_parser("set", help="set voltage and current limit (does NOT change output state)")
    p.add_argument("--v", type=float, required=True)
    p.add_argument("--i", type=float, required=True, help="current limit, A")

    p = sub.add_parser("on", help="enable output (optionally set V/I first)")
    p.add_argument("--v", type=float)
    p.add_argument("--i", type=float)
    sub.add_parser("off", help="disable output")

    p = sub.add_parser("protect", help="set protection threshold")
    p.add_argument("kind", choices=sorted(PROT_REGS))
    p.add_argument("value", type=float, help="V for ovp/lvp, A for ocp, W for opp, C for otp")

    p = sub.add_parser("preset", help="store preset M1..M6")
    p.add_argument("n", type=int)
    p.add_argument("v", type=float)
    p.add_argument("i", type=float)

    p = sub.add_parser("meter", help="start/stop/reset-style Ah/Wh metering")
    p.add_argument("action", choices=["start", "stop"])

    p = sub.add_parser("log", help="record telemetry to CSV (does not change output state)")
    p.add_argument("--out", default=None, help="CSV path (default dps150_<timestamp>.csv)")
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--poll-hz", type=float, default=None,
                   help="poll full state at this rate instead of using the ~2 Hz push stream")
    p.add_argument("--quiet", action="store_true")

    p = sub.add_parser("sweep", help="step voltage, log each step (I-V characterisation). Turns output ON, then OFF and restores previous setpoints.")
    p.add_argument("--vstart", type=float, required=True)
    p.add_argument("--vstop", type=float, required=True)
    p.add_argument("--vstep", type=float, required=True)
    p.add_argument("--ilimit", type=float, required=True, help="current limit held during sweep, A")
    p.add_argument("--dwell", type=float, default=1.0, help="seconds per step (>=0.6 recommended)")
    p.add_argument("--out", default=None)
    p.add_argument("--keep-on", action="store_true", help="leave output on at the end (default: off)")

    a = ap.parse_args(argv)
    if a.cmd == "ports":
        return cmd_ports(a)

    try:
        dev = DPS150(a.port, verbose=a.verbose, dtr=a.dtr).open()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        return run(dev, a)
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        dev.close()


def out(a, obj, text: str) -> None:
    print(json.dumps(obj, indent=2, default=str) if a.json else text)


def run(dev: DPS150, a) -> int:
    if a.cmd == "info":
        s = dev.wait_fresh()
        o = {"port": dev.port_name, "model": dev.model, "hardware": dev.hw, "firmware": dev.fw, "limits": s["limits"]}
        out(a, o, f"{dev.model}  HW {dev.hw}  FW {dev.fw}  on {dev.port_name}\n"
                  f"limits: {s['limits']['v']} V / {s['limits']['i']} A")
    elif a.cmd == "status":
        s = dev.wait_fresh()
        out(a, clean(s), fmt_status(s))
    elif a.cmd == "monitor":
        t0 = time.time()
        dev.on_update(lambda s: None)
        last = 0.0
        try:
            while not a.duration or time.time() - t0 < a.duration:
                time.sleep(0.1)
                s = dev.snapshot()
                if s.get("_t_out", 0) != last and "vout" in s:
                    last = s["_t_out"]
                    print(f"{time.time() - t0:7.1f}s  {s['vout']:7.3f} V  {s['iout']:6.3f} A  {s['pout']:7.3f} W  "
                          f"{s['temp']:5.1f} C  {s.get('mode')}  {'ON' if s.get('output_on') else 'off'}  {s['protection_name']}")
        except KeyboardInterrupt:
            pass
    elif a.cmd == "set":
        s = dev.apply(a.v, a.i)
        out(a, clean(s), f"set {s['vset']:.3f} V / {s['iset']:.3f} A (output state unchanged: "
                          f"{'ON' if s.get('output_on') else 'off'})")
    elif a.cmd == "on":
        if (a.v is None) != (a.i is None):
            raise ValueError("give both --v and --i, or neither")
        if a.v is not None:
            dev.apply(a.v, a.i)
        dev.set_output(True)
        time.sleep(0.4)
        s = dev.wait_fresh()
        if not s.get("output_on"):
            raise RuntimeError(f"output did not turn on (protection: {s['protection_name']})")
        out(a, clean(s), "output ON\n" + fmt_status(s))
    elif a.cmd == "off":
        dev.set_output(False)
        time.sleep(0.4)
        s = dev.wait_fresh()
        if s.get("output_on"):
            raise RuntimeError("output still reports ON after off command!")
        out(a, clean(s), "output off")
    elif a.cmd == "protect":
        dev.set_protection(a.kind, a.value)
        time.sleep(0.3)
        s = dev.wait_fresh()
        got = s.get(a.kind)
        if got is None or abs(got - a.value) > max(0.01, 0.001 * abs(a.value)):
            raise RuntimeError(f"{a.kind.upper()} readback {got} != requested {a.value}")
        out(a, {a.kind: got}, f"{a.kind.upper()} = {got:g} {PROT_UNITS[a.kind]}")
    elif a.cmd == "preset":
        dev.set_preset(a.n, a.v, a.i)
        time.sleep(0.3)
        s = dev.wait_fresh()
        out(a, {"preset": a.n, "value": s["presets"][a.n - 1]}, f"M{a.n} = {s['presets'][a.n - 1]}")
    elif a.cmd == "meter":
        dev.set_metering(a.action == "start")
        out(a, {"metering": a.action}, f"metering {a.action}")
    elif a.cmd == "log":
        path = a.out or f"dps150_{datetime.now():%Y%m%d_%H%M%S}.csv"
        summ = log_session(dev, path, a.duration, a.poll_hz, a.quiet)
        out(a, summ, json.dumps(summ, indent=2))
    elif a.cmd == "sweep":
        return do_sweep(dev, a)
    return 0


def do_sweep(dev: DPS150, a) -> int:
    if a.vstep == 0 or (a.vstop - a.vstart) * a.vstep < 0:
        raise ValueError("vstep must move from vstart toward vstop")
    n = int(round((a.vstop - a.vstart) / a.vstep)) + 1
    volts = [round(a.vstart + k * a.vstep, 6) for k in range(n)]
    for v in volts:
        dev._check("voltage", v, dev.state["limits"]["v"])
    path = a.out or f"dps150_sweep_{datetime.now():%Y%m%d_%H%M%S}.csv"
    t0 = time.time()
    rows = []
    before = dev.wait_fresh()                      # restore these setpoints afterwards
    try:
        dev.apply(volts[0], a.ilimit)
        dev.set_output(True)
        time.sleep(0.5)
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["vset_cmd"] + LOG_FIELDS)
            w.writeheader()
            for v in volts:
                dev.set_voltage(v)
                time.sleep(max(0.2, a.dwell - 0.5))
                snap = dev.wait_fresh()                     # fresh full-state read at end of dwell
                if not snap.get("output_on") or snap.get("protection", 0):
                    print(f"stopping: output_on={snap.get('output_on')} protection={snap.get('protection_name')}",
                          file=sys.stderr)
                    break
                r = _row(snap, t0)
                r["vset_cmd"] = v
                rows.append(r)
                w.writerow(r)
                fh.flush()
                print(f"Vset {v:7.3f}  ->  {r['vout']:7.3f} V  {r['iout']:7.4f} A  {r['pout']:7.3f} W  {r['mode']}",
                      file=sys.stderr)
    finally:
        if not a.keep_on:
            dev.set_output(False)
            time.sleep(0.3)
            try:                                   # don't leave the last sweep point armed
                dev.set_current(before["iset"])
                dev.set_voltage(before["vset"])
                time.sleep(0.3)
            except Exception as e:
                print(f"warning: could not restore setpoints: {e}", file=sys.stderr)
    summ = summarize(rows, path)
    summ["points"] = len(rows)
    summ["output_left_on"] = bool(a.keep_on)
    out(a, summ, json.dumps(summ, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8,<3.11"
# dependencies = ["logic2-automation"]
# ///
"""Control a Saleae Logic Pro 16 (and Logic 8 / Pro 8) through the Logic 2 automation API.

Every command prints exactly one JSON object on stdout. Progress goes to stderr.
Exit code: 0 ok, 1 error, 2 capture timed out waiting for a trigger.
Keep this file Python 3.8 compatible (logic2-automation supports 3.8 to 3.10 only).
"""
import argparse
import csv
import datetime
import json
import os
import socket
import subprocess
import sys
import threading
import time

DEFAULT_PORT = 10430
MAC_APP = "/Applications/Saleae Logic.app/Contents/MacOS/Logic"
APP_CANDIDATES = [
    MAC_APP,
    "/Applications/Logic.app/Contents/MacOS/Logic",
    os.path.expanduser("~/Applications/Saleae Logic.app/Contents/MacOS/Logic"),
    os.path.expanduser("~/Applications/Logic.app/Contents/MacOS/Logic"),
    r"C:\Program Files\Logic\Logic.exe",
    os.path.expanduser("~/Downloads/Logic-2.AppImage"),
]


# ----------------------------------------------------------------------------- helpers
def emit(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    sys.exit(code)


def fail(msg, hint=None, **extra):
    d = {"error": msg}
    if hint:
        d["hint"] = hint
    d.update(extra)
    emit(d, 1)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def num(s):
    """'10M' -> 10_000_000, '500k' -> 500000, '1e6' -> 1000000. Returns int when whole."""
    s = str(s).strip().lower().replace("_", "")
    mult = {"k": 1e3, "m": 1e6, "g": 1e9, "u": 1e-6, "n": 1e-9}
    if s and s[-1] in mult and not s.endswith("e"):
        v = float(s[:-1]) * mult[s[-1]]
    else:
        v = float(s)
    return int(v) if float(v).is_integer() else v


def int_list(s):
    """'0,1,4-7' -> [0,1,4,5,6,7]"""
    out = []
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def coerce(v):
    lv = v.strip()
    if lv.lower() in ("true", "false"):
        return lv.lower() == "true"
    try:
        return int(lv)
    except ValueError:
        pass
    try:
        return float(lv)
    except ValueError:
        return lv


def port_open(port, host="127.0.0.1", timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_app(explicit=None):
    for p in ([explicit] if explicit else []) + APP_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def connect(port):
    from saleae import automation

    if not port_open(port):
        fail(
            "Logic 2 automation server is not reachable on port %d" % port,
            hint="Run `sl.py app start` (launches Logic 2 with --automation), or in Logic 2 open "
            "Preferences, scroll to the bottom and enable the automation server. Logic 2 must be running.",
        )
    try:
        return automation.Manager.connect(port=port, connect_timeout_seconds=10)
    except Exception as e:  # noqa
        fail("Could not connect to Logic 2: %s" % e,
             hint="Logic 2 must be 2.4.0+ and the package logic2-automation must match its API version.")


def pick_device(mgr, device_id, include_sim):
    devs = mgr.get_devices(include_simulation_devices=include_sim)
    if not devs:
        fail(
            "No Saleae device found",
            hint="Check USB 3.0 cable/port, that Logic 2 shows the device, and that no other app "
            "(e.g. another Logic 2 window) has it open. Use --sim to use the built-in demo device.",
        )
    if device_id:
        for d in devs:
            if d.device_id.lower() == device_id.lower():
                return d
        fail("Device %s not found" % device_id, devices=[d.device_id for d in devs])
    real = [d for d in devs if not d.is_simulation]
    return (real or devs)[0]


# ----------------------------------------------------------------------------- stats from digital.csv
def digital_stats(path, duration=None):
    """Per-channel edge/period/pulse statistics from a Saleae raw digital CSV (rows only on change)."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader)]
        chan_names = header[1:]
        n = len(chan_names)
        prev = None
        prev_t = None
        first_t = last_t = None
        rise = [[] for _ in range(n)]
        fall = [[] for _ in range(n)]
        for row in reader:
            if len(row) < n + 1:
                continue
            t = float(row[0])
            vals = [int(x) for x in row[1:n + 1]]
            if first_t is None:
                first_t = t
            last_t = t
            if prev is not None:
                for i in range(n):
                    if vals[i] != prev[i]:
                        (rise if vals[i] else fall)[i].append(t)
            else:
                prev_t = t
            prev = vals
    span = duration if duration else ((last_t - first_t) if first_t is not None else 0.0)
    result = {}
    for i, name in enumerate(chan_names):
        r, fl = rise[i], fall[i]
        info = {"rising_edges": len(r), "falling_edges": len(fl)}
        if len(r) >= 2:
            periods = [b - a for a, b in zip(r, r[1:])]
            mean_p = sum(periods) / len(periods)
            info["period_s"] = {"min": min(periods), "mean": mean_p, "max": max(periods)}
            info["frequency_hz"] = 1.0 / mean_p if mean_p > 0 else None
        # pulse widths from complete pulses only (edge to next opposite edge)
        highs = [f - r0 for r0 in r for f in [next((x for x in fl if x > r0), None)] if f is not None]
        lows = [r0 - f0 for f0 in fl for r0 in [next((x for x in r if x > f0), None)] if r0 is not None]
        if highs:
            info["high_s"] = {"min": min(highs), "mean": sum(highs) / len(highs), "max": max(highs)}
        if lows:
            info["low_s"] = {"min": min(lows), "mean": sum(lows) / len(lows), "max": max(lows)}
        if highs and lows:
            mh, ml = sum(highs) / len(highs), sum(lows) / len(lows)
            info["duty_cycle"] = mh / (mh + ml)
        if not r and not fl:
            info["note"] = "no transitions (stuck at constant level)"
        result[name] = info
    return {"capture_span_s": span, "channels": result}


# ----------------------------------------------------------------------------- commands
def cmd_app(a):
    if a.action == "status":
        emit({"automation_port_open": port_open(a.port), "port": a.port, "app_found": find_app(a.path)})
    if a.action == "start":
        if port_open(a.port):
            emit({"started": False, "note": "Logic 2 automation server already reachable", "port": a.port})
        app = find_app(a.path)
        if not app:
            fail("Logic 2 not found", hint="Install from saleae.com/downloads, or pass --path to the Logic executable.")
        cmd = [app, "--automation", "--automationPort", str(a.port)]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(60):
            if port_open(a.port):
                emit({"started": True, "port": a.port, "app": app})
            time.sleep(0.5)
        fail("Launched Logic 2 but the automation port never opened",
             hint="If Logic 2 is already running without --automation, quit it first (or enable the "
             "automation server in Preferences) and retry.")


def cmd_info(a):
    mgr = connect(a.port)
    with mgr:
        app = mgr.get_app_info()
        devs = mgr.get_devices(include_simulation_devices=True)
        emit({
            "app_version": app.app_version,
            "api_version": "%d.%d.%d" % (app.api_version.major, app.api_version.minor, app.api_version.patch),
            "devices": [{"device_id": d.device_id, "type": str(d.device_type).split(".")[-1],
                         "simulation": d.is_simulation} for d in devs],
            "real_device_present": any(not d.is_simulation for d in devs),
        })


def build_analyzer_specs(specs):
    out = []
    for s in specs or []:
        parts = [p for p in s.split("|") if p.strip()]
        name, label, settings = parts[0].strip(), None, {}
        for p in parts[1:]:
            if "=" not in p:
                fail("Bad --analyzer part %r; use NAME|key=value|key=value (use label=NAME for a label)" % p)
            k, v = p.split("=", 1)
            if k.strip().lower() == "label":
                label = v.strip()
            else:
                settings[k.strip()] = coerce(v)
        out.append((name, label or name, settings))
    return out


def safe_name(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def preview_csv(path, n):
    rows = []
    total = 0
    with open(path, newline="") as f:
        for i, row in enumerate(csv.reader(f)):
            if i == 0:
                header = row
                continue
            total += 1
            if len(rows) < n:
                rows.append(row)
    return {"file": path, "rows": total, "columns": header, "preview": rows}


def cmd_capture(a):
    from saleae import automation as au

    digital = int_list(a.digital) if a.digital else []
    analog = int_list(a.analog) if a.analog else []
    if not digital and not analog:
        fail("Enable at least one channel", hint="e.g. --digital 0-3 or --analog 0")
    out = a.out or os.path.join("saleae_captures", datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out, exist_ok=True)
    out = os.path.abspath(out)

    kw = {"enabled_digital_channels": digital, "enabled_analog_channels": analog}
    if a.rate:
        kw["digital_sample_rate"] = num(a.rate)
    if a.analog_rate:
        kw["analog_sample_rate"] = num(a.analog_rate)
    if a.threshold is not None:
        kw["digital_threshold_volts"] = float(a.threshold)
    if a.glitch:
        kw["glitch_filters"] = [
            au.GlitchFilterEntry(channel_index=int(c), pulse_width_seconds=num(w))
            for c, w in (g.split(":") for g in a.glitch)
        ]
    dev_cfg = au.LogicDeviceConfiguration(**kw)

    trim = num(a.trim) if a.trim else None
    if a.trigger:
        tmap = {"rising": au.DigitalTriggerType.RISING, "falling": au.DigitalTriggerType.FALLING,
                "pulse-high": au.DigitalTriggerType.PULSE_HIGH, "pulse-low": au.DigitalTriggerType.PULSE_LOW}
        if a.trigger_channel is None:
            fail("--trigger needs --trigger-channel")
        linked = []
        for l in a.linked or []:
            c, st = l.split(":")
            linked.append(au.DigitalTriggerLinkedChannel(
                channel_index=int(c),
                state=au.DigitalTriggerLinkedChannelState.HIGH if st.lower() in ("1", "high", "h")
                else au.DigitalTriggerLinkedChannelState.LOW))
        mode = au.DigitalTriggerCaptureMode(
            trigger_type=tmap[a.trigger], trigger_channel_index=a.trigger_channel,
            min_pulse_width_seconds=num(a.min_pulse) if a.min_pulse else None,
            max_pulse_width_seconds=num(a.max_pulse) if a.max_pulse else None,
            linked_channels=linked, trim_data_seconds=trim,
            after_trigger_seconds=num(a.after) if a.after else (num(a.time) if a.time else None))
        timed = False
    elif a.time:
        mode = au.TimedCaptureMode(duration_seconds=float(num(a.time)), trim_data_seconds=trim)
        timed = True
    else:
        fail("Specify -t/--time (timed capture) or --trigger (wait for an edge)")
    cap_cfg = au.CaptureConfiguration(
        buffer_size_megabytes=a.buffer_mb, capture_mode=mode)

    analyzers = build_analyzer_specs(a.analyzer)
    mgr = connect(a.port)
    result = {"out_dir": out}
    with mgr:
        dev = pick_device(mgr, a.device, a.sim)
        result["device"] = {"device_id": dev.device_id, "type": str(dev.device_type).split(".")[-1],
                            "simulation": dev.is_simulation}
        log("Starting capture on %s ..." % dev.device_id)
        try:
            cap = mgr.start_capture(device_id=dev.device_id, device_configuration=dev_cfg,
                                    capture_configuration=cap_cfg)
        except Exception as e:  # noqa
            fail("start_capture failed: %s" % e,
                 hint="Sample rate must be one Logic 2 offers for this channel set (16 digital channels "
                 "cap at about 100 MS/s; analog rate drops as analog channels are added). Try a lower --rate.")
        with cap:
            t0 = time.time()
            if a.timeout and not timed:
                th = threading.Thread(target=cap.wait, daemon=True)
                th.start()
                th.join(a.timeout)
                if th.is_alive():
                    try:
                        cap.stop()
                    except Exception:  # noqa
                        pass
                    result["trigger"] = "timeout"
                    emit(dict(result, error="Trigger did not occur within %ss" % a.timeout,
                              hint="Check threshold voltage, trigger channel, and edge polarity."), 2)
                result["trigger"] = "fired"
            else:
                cap.wait()
            result["capture_wall_s"] = round(time.time() - t0, 3)
            # Logic 2 refuses add_analyzer while a capture is running, so add after it ends.
            handles = []
            for name, label, settings in analyzers:
                try:
                    handles.append((label, cap.add_analyzer(name, label=label, settings=settings)))
                except Exception as e:  # noqa
                    fail("add_analyzer(%r) failed: %s" % (name, e),
                         hint="Name and setting keys/values must match the Logic 2 UI text exactly "
                         "(case, spaces, units). Open the analyzer in the Logic 2 UI to copy its labels.")

            sal = os.path.join(out, "capture.sal")
            if not a.no_save:
                cap.save_capture(sal)
                result["sal"] = sal
            if a.csv or a.stats:
                raw = os.path.join(out, "raw")
                os.makedirs(raw, exist_ok=True)
                cap.export_raw_data_csv(directory=raw, digital_channels=digital or None,
                                        analog_channels=analog or None,
                                        analog_downsample_ratio=a.analog_downsample)
                result["raw_csv_dir"] = raw
                if a.stats and digital:
                    result["digital_stats"] = digital_stats(
                        os.path.join(raw, "digital.csv"), float(num(a.time)) if a.time else None)
            if a.binary:
                b = os.path.join(out, "bin")
                os.makedirs(b, exist_ok=True)
                cap.export_raw_data_binary(directory=b, digital_channels=digital or None,
                                           analog_channels=analog or None,
                                           analog_downsample_ratio=a.analog_downsample)
                result["binary_dir"] = b
            if handles:
                result["analyzers"] = {}
                for label, h in handles:
                    path = os.path.join(out, "analyzer_%s.csv" % safe_name(label))
                    try:
                        cap.export_data_table(filepath=path, analyzers=[h])
                        result["analyzers"][label] = preview_csv(path, a.preview)
                    except Exception as e:  # noqa
                        result["analyzers"][label] = {"error": str(e)}
    emit(result)


def cmd_load(a):
    if not os.path.exists(a.file):
        fail("File not found: %s" % a.file)
    out = a.out or os.path.splitext(os.path.abspath(a.file))[0] + "_export"
    os.makedirs(out, exist_ok=True)
    mgr = connect(a.port)
    result = {"file": os.path.abspath(a.file), "out_dir": out}
    with mgr:
        cap = mgr.load_capture(os.path.abspath(a.file))
        with cap:
            raw = os.path.join(out, "raw")
            os.makedirs(raw, exist_ok=True)
            cap.export_raw_data_csv(
                directory=raw,
                digital_channels=int_list(a.digital) if a.digital else None,
                analog_channels=int_list(a.analog) if a.analog else None,
                analog_downsample_ratio=a.analog_downsample)
            result["raw_csv_dir"] = raw
            if os.path.exists(os.path.join(raw, "digital.csv")):
                result["digital_stats"] = digital_stats(os.path.join(raw, "digital.csv"))
    emit(result)


def cmd_stats(a):
    if not os.path.exists(a.csv):
        fail("File not found: %s" % a.csv)
    emit(digital_stats(a.csv, num(a.duration) if a.duration else None))


def main():
    p = argparse.ArgumentParser(description="Saleae Logic Pro 16 control via Logic 2 automation API")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("app", help="start Logic 2 with the automation server, or check status")
    s.add_argument("action", choices=["start", "status"])
    s.add_argument("--path")
    s.set_defaults(fn=cmd_app)

    s = sub.add_parser("info", help="app version and attached devices")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("capture", help="run a capture, optionally decode and export")
    s.add_argument("-d", "--device", help="device serial (default: first real device)")
    s.add_argument("--sim", action="store_true", help="allow the simulation/demo device")
    s.add_argument("-D", "--digital", help="digital channels, e.g. 0-3,8")
    s.add_argument("-A", "--analog", help="analog channels, e.g. 0,1")
    s.add_argument("-r", "--rate", help="digital sample rate, e.g. 10M, 100M, 500M")
    s.add_argument("--analog-rate", help="analog sample rate, e.g. 6.25M, 12.5M, 50M")
    s.add_argument("--threshold", type=float, help="digital threshold volts: 1.2, 1.8 or 3.3 (Pro series)")
    s.add_argument("--glitch", action="append", metavar="CH:SECONDS", help="glitch filter, e.g. 0:100n (repeatable)")
    s.add_argument("-t", "--time", help="capture seconds (timed mode). With --trigger: seconds recorded after trigger")
    s.add_argument("--trigger", choices=["rising", "falling", "pulse-high", "pulse-low"])
    s.add_argument("--trigger-channel", type=int)
    s.add_argument("--linked", action="append", metavar="CH:high|low", help="extra trigger condition (repeatable)")
    s.add_argument("--min-pulse"); s.add_argument("--max-pulse")
    s.add_argument("--after", help="seconds to record after trigger")
    s.add_argument("--trim", help="discard data older than this many seconds (rolling window)")
    s.add_argument("--timeout", type=float, help="give up waiting for a trigger after N s (exit 2)")
    s.add_argument("--buffer-mb", type=int, dest="buffer_mb")
    s.add_argument("--analyzer", action="append", metavar="'NAME|label=X|Setting=Val'",
                   help="decoder, repeatable. Name/settings must match the Logic 2 UI exactly")
    s.add_argument("--out", help="output directory (default saleae_captures/<timestamp>)")
    s.add_argument("--no-save", action="store_true", help="skip writing capture.sal")
    s.add_argument("--csv", action="store_true", help="export raw digital/analog CSV")
    s.add_argument("--binary", action="store_true", help="export raw binary (smaller, faster for big captures)")
    s.add_argument("--stats", action="store_true", help="export digital CSV and compute edge/frequency/pulse stats")
    s.add_argument("--analog-downsample", type=int, default=1)
    s.add_argument("--preview", type=int, default=20, help="decoded rows to show per analyzer")
    s.set_defaults(fn=cmd_capture)

    s = sub.add_parser("load", help="open a .sal file, export raw CSV and stats")
    s.add_argument("file")
    s.add_argument("-D", "--digital"); s.add_argument("-A", "--analog")
    s.add_argument("--out"); s.add_argument("--analog-downsample", type=int, default=1)
    s.set_defaults(fn=cmd_load)

    s = sub.add_parser("stats", help="edge/frequency/pulse stats from an exported digital.csv (no Logic 2 needed)")
    s.add_argument("csv"); s.add_argument("--duration")
    s.set_defaults(fn=cmd_stats)

    a = p.parse_args()
    try:
        a.fn(a)
    except SystemExit:
        raise
    except Exception as e:  # noqa
        fail("%s: %s" % (type(e).__name__, e))


if __name__ == "__main__":
    main()

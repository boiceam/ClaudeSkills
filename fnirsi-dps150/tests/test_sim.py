#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyserial>=3.5"]
# ///
"""End-to-end test of dps150.py against the pty simulator. No hardware needed.

    uv run tests/test_sim.py
"""
import csv, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "..", "scripts", "dps150.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import dps150  # noqa: E402

fails = 0
def check(name, cond, extra=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    fails += (not cond)

# ---- pure unit tests (frames from the public protocol docs)
check("connect frame", dps150.build_frame(0xC1, 0x00, b"\x01") == bytes.fromhex("f1c100010102"))
check("baud frame", dps150.build_frame(0xB0, 0x00, b"\x05") == bytes.fromhex("f1b0000105" "06"))
check("read-all frame", dps150.build_frame(0xA1, 0xFF, b"\x00") == bytes.fromhex("f1a1ff010000"))
check("model query frame", dps150.build_frame(0xA1, 0xDE, b"\x00") == bytes.fromhex("f1a1de0100df"))
check("output-on frame", dps150.build_frame(0xB1, 0xDB, b"\x01") == bytes.fromhex("f1b1db0101dd"))
buf = bytearray(bytes.fromhex("0102") + bytes.fromhex("f0a1db0101dd") + bytes.fromhex("f0a1db010100") + bytes.fromhex("f0a1db0100dc"))
fr = dps150.parse_frames(buf)
check("parser resyncs past junk + bad checksum", [f["data"] for f in fr] == [b"\x01", b"\x00"], str(fr))
half = bytearray(bytes.fromhex("f0a1db01"))
check("parser keeps partial frame", dps150.parse_frames(half) == [] and len(half) == 4)

# ---- simulator integration
sim = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_dps150.py"), "--load-ohms", "10"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
port = sim.stdout.readline().strip()
def cli(*args):
    r = subprocess.run([sys.executable, CLI, "--port", port, "--json", *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr
try:
    time.sleep(0.3)
    rc, o, e = cli("info"); j = json.loads(o) if rc == 0 else {}
    check("info", rc == 0 and j.get("model") == "DPS-150" and j.get("limits", {}).get("v") == 30.0, e or o)

    rc, o, e = cli("set", "--v", "5", "--i", "1"); j = json.loads(o) if rc == 0 else {}
    check("set 5V/1A verified by readback", rc == 0 and abs(j["vset"] - 5) < 1e-6, e or o)

    rc, o, e = cli("on"); j = json.loads(o) if rc == 0 else {}
    check("on -> CV, 0.5 A into 10 ohm", rc == 0 and j["output_on"] and abs(j["iout"] - 0.5) < 1e-3 and j["mode"] == "CV", e or o)

    rc, o, e = cli("set", "--v", "20", "--i", "1"); j = json.loads(o) if rc == 0 else {}
    rc, o, e = cli("status"); j = json.loads(o)
    check("current limit -> CC at 1 A", j["mode"] == "CC" and abs(j["iout"] - 1.0) < 1e-3 and abs(j["vout"] - 10.0) < 1e-3, o)

    rc, o, e = cli("set", "--v", "31", "--i", "1")
    check("rejects 31 V (>30 V device limit)", rc == 1 and "outside" in e, e)
    rc, o, e = cli("set", "--v", "-1", "--i", "1")
    check("rejects negative voltage", rc == 1, e)

    rc, o, e = cli("protect", "ovp", "12.5"); check("protect ovp readback", rc == 0 and abs(json.loads(o)["ovp"] - 12.5) < 1e-4, e or o)
    rc, o, e = cli("preset", "3", "9", "0.25"); check("preset 3 readback", rc == 0 and json.loads(o)["value"] == [9.0, 0.25], e or o)

    tmp = tempfile.mkdtemp(); csvp = os.path.join(tmp, "log.csv")
    rc, o, e = cli("log", "--out", csvp, "--duration", "3", "--quiet"); j = json.loads(o)
    rows = list(csv.DictReader(open(csvp)))
    check("log ~2 Hz pushes for 3 s", rc == 0 and 4 <= len(rows) <= 8 and "vout" in rows[0], f"{len(rows)} rows; {e}")
    rc, o, e = cli("log", "--out", csvp, "--duration", "2", "--poll-hz", "5", "--quiet"); j = json.loads(o)
    check("log polled at 5 Hz", rc == 0 and j["rows"] >= 8, o)

    swp = os.path.join(tmp, "sweep.csv")
    rc, o, e = cli("sweep", "--vstart", "1", "--vstop", "5", "--vstep", "2", "--ilimit", "1", "--dwell", "0.8", "--out", swp)
    rows = list(csv.DictReader(open(swp))) if rc == 0 else []
    ok = rc == 0 and len(rows) == 3 and all(abs(float(r["iout"]) - float(r["vset_cmd"]) / 10) < 1e-3 for r in rows)
    check("sweep 1..5 V is Ohmic (R=10)", ok, f"{e} {rows}")
    rc, o, e = cli("status"); j = json.loads(o)
    check("sweep leaves output off", j["output_on"] is False, o)
    check("sweep restores prior setpoints (20 V / 1 A)", abs(j["vset"] - 20) < 1e-3 and abs(j["iset"] - 1) < 1e-3, o)

    rc, o, e = cli("off"); check("off", rc == 0, e)
finally:
    sim.terminate()
    check("sim saw no bad checksums", "BAD CHECKSUM" not in sim.communicate(timeout=5)[1])
print(f"\n{'ALL PASSED' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)

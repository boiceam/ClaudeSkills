#!/usr/bin/env python3
"""Check G-code files made by stock_prep.py / mill_pcb.py against what the Makera Z1 accepts.

    python3 check_gcode.py FILE.nc [FILE.nc ...] [--blank 152x152] [--origin blank|anchor1] [--max-depth 2.5]

Static checks only. It does not simulate material or prove the job is safe:
  - every line is one Makera CAM would write (Makera Studio shows no preview otherwise):
    "%", "G90 G21", "T<n> M6", "M7", "S<rpm> M3", G0/G1 moves with feeds on the moves, "M9", "M5", "G28", "M02"
  - starts with the tool change, then an XY rapid, then spindle on; a multi-tool file repeats that
    after each further tool change, with the spindle stopped first
  - first move after the tool change is XY only (never downwards first)
  - every sideways rapid is at or above the travel height; no rapid ends below Z0 except
    re-entering a hole already drilled at the same XY
  - every cut has the spindle on and a feed set; nothing deeper than --max-depth
  - cuts stay on the blank and away from the dowel pins (with --blank)
  - ends with spindle off, G28 and M02
"""
import argparse, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import makera_z1 as z1

def kind(s):
    """Classify one line the way Makera CAM lays files out; None if it is not an expected line."""
    if s == "G90 G21":
        return "G90 G21"
    if re.fullmatch(r"T\d+ M6", s):
        return "M6"
    if re.fullmatch(r"S\d+ M3", s):
        return "M3"
    if s in ("M7", "M9"):
        return s
    if s in ("M5", "M05"):
        return "M5"
    if s in ("M02", "M2"):
        return "M2"
    if s == "G28":
        return "G28"
    if re.fullmatch(r"G[01]( [XYZF]-?\d+\.?\d*)+", s):
        return s[:2]
    return None


def check(path, blank, max_depth, travel=2.0, anchor=False):
    errs, lines = [], []
    raw_lines = open(path).read().split("\n")
    if raw_lines[0].strip() != "%":
        errs.append("first line is not '%'")
    for n, raw in enumerate(raw_lines, 1):
        s = re.split(r"[;(]", raw, maxsplit=1)[0].strip()      # the firmware cuts a line at the first ';' or '('
        if s and s != "%":
            lines.append((n, s))
    codes = [kind(s) for _, s in lines]
    for (n, s), c in zip(lines, codes):
        if c is None:
            errs.append(f"line {n}: not a line Makera CAM would write: {s}")
    core = [k for k in codes if k not in ("M7", "M9")]         # air on/off is optional
    want = ["G90 G21", "M6", "G0", "M3", "G0"]
    if core[:5] != want:
        errs.append(f"start is {core[:5]}, expected {want}")
    if core[-3:] != ["M5", "G28", "M2"]:
        errs.append(f"end is {core[-3:]}, expected ['M5', 'G28', 'M2']")
    if codes.count("M7") != codes.count("M9"):
        errs.append("air: every M7 needs an M9")
    for i, k in enumerate(codes):
        if k == "M7" and codes[i - 1] != "M6":
            errs.append(f"line {lines[i][0]}: M7 must follow a tool change")
        if k == "M9" and codes[i + 1] != "M5":
            errs.append(f"line {lines[i][0]}: M9 must be followed by M5")
        if k == "M6" and i > 1 and codes[i - 1] != "M5":
            errs.append(f"line {lines[i][0]}: the spindle must be stopped (M5) before a tool change")
    if codes.count("M6") < 1:
        errs.append("no tool change")
    tools = [s.split()[0] for (_, s), k in zip(lines, codes) if k == "M6"]
    if any(a == b for a, b in zip(tools, tools[1:])):
        errs.append("a tool change to the tool already loaded does nothing: " + ", ".join(tools))
    lay = z1.stock_layout(*blank) if blank else None
    ox, oy = lay["origin"] if (lay and anchor) else (0.0, 0.0)      # where the blank's corner is in file coordinates
    pins = [(px + ox, py + oy) for px, py in lay["pins_stock"]] if lay else []
    pos = {"X": None, "Y": None, "Z": None}
    spindle = feed = False
    drilled, seen_xy, deepest, ext = set(), False, 0.0, [1e9, 1e9, -1e9, -1e9]
    for n, s in lines:
        c = kind(s) or ""
        if c == "M6":
            seen_xy = False                # the tool change leaves the spindle over the tool setter
            pos = {"X": None, "Y": None, "Z": None}
        if c == "M3":
            spindle = True
        elif c == "M5":
            spindle = False
        if c not in ("G0", "G1"):
            continue
        new = dict(pos)
        for a in "XYZ":
            m = re.search(a + r"(-?\d+\.?\d*)", s)
            if m:
                new[a] = float(m.group(1))
        if "F" in s:
            feed = True
        moved_xy = (new["X"], new["Y"]) != (pos["X"], pos["Y"])
        moved_z = new["Z"] != pos["Z"]
        if not seen_xy:
            if moved_z and not moved_xy:
                errs.append(f"line {n}: Z move before the first XY move: {s}")
            seen_xy = seen_xy or moved_xy
        if c == "G0":
            if moved_xy and pos["Z"] is not None and (pos["Z"] < travel - 1e-6 or new["Z"] < travel - 1e-6):
                errs.append(f"line {n}: sideways rapid below the travel height: {s}")
            if new["Z"] is not None and new["Z"] < 0 and (new["X"], new["Y"]) not in drilled:
                errs.append(f"line {n}: rapid below Z0 where nothing was drilled: {s}")
        elif moved_xy or moved_z:
            if not spindle:
                errs.append(f"line {n}: feed move with the spindle off: {s}")
            if not feed:
                errs.append(f"line {n}: feed move before any feed rate: {s}")
            if new["Z"] is not None and new["Z"] < 0:
                deepest = min(deepest, new["Z"])
                if not moved_xy:
                    drilled.add((new["X"], new["Y"]))
                ext = [min(ext[0], new["X"]), min(ext[1], new["Y"]), max(ext[2], new["X"]), max(ext[3], new["Y"])]
                for px, py in pins:
                    d = ((new["X"] - px) ** 2 + (new["Y"] - py) ** 2) ** 0.5
                    if 2.5 < d < 6.0:
                        errs.append(f"line {n}: cut {d:.1f} mm from the dowel pin at {px}, {py}: {s}")
                        break
        pos = new
    if deepest < -max_depth:
        errs.append(f"deepest cut {deepest} mm is beyond {max_depth} mm")
    if blank and (ext[0] < ox or ext[1] < oy or ext[2] > ox + blank[0] or ext[3] > oy + blank[1]):
        errs.append(f"cuts leave the blank: X {ext[0]:.2f}..{ext[2]:.2f}, Y {ext[1]:.2f}..{ext[3]:.2f}")
    tool = " + ".join(s for _, s in lines if s.endswith("M6")) or "?"
    return errs, f"{len(lines)} lines, {tool}, cuts X {ext[0]:.1f}..{ext[2]:.1f} Y {ext[1]:.1f}..{ext[3]:.1f}, deepest Z {deepest:.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--blank", help="blank size WxH; checks cuts stay on it and clear of the pins")
    ap.add_argument("--origin", choices=["blank", "anchor1"], default="blank",
                    help="where X0 Y0 is: the blank's front-left corner, or Anchor 1 with the blank on the dowel pins")
    ap.add_argument("--max-depth", type=float, default=2.5)
    a = ap.parse_args()
    blank = tuple(float(v) for v in a.blank.lower().split("x")) if a.blank else None
    bad = 0
    for f in a.files:
        errs, info = check(f, blank, a.max_depth, anchor=(a.origin == "anchor1"))
        print(("FAIL " if errs else "ok   ") + os.path.basename(f) + ": " + info)
        for e in errs[:12]:
            print("       " + e)
        if len(errs) > 12:
            print(f"       ... and {len(errs) - 12} more")
        bad += bool(errs)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

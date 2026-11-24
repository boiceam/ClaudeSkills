#!/usr/bin/env python3
"""Join several single-tool G-code files (from mill_pcb.py or stock_prep.py) into one multi-tool file.

    python3 combine_gcode.py -o OUT.nc FILE1.nc FILE2.nc ...

For steps that need no change to the stock between them. The source files are not modified.
The result is laid out as Makera CAM writes a multi-tool file: each section starts with its
tool change (the machine prompts for the bit and measures it), then air on, an XY rapid, spindle
on; it ends with air off and spindle off. A section that uses the tool already loaded keeps the
spindle running and just carries on. The file ends with G28 and M02.

All files must share the same work origin; that is the caller's responsibility.
Run check_gcode.py on the result.
"""
import argparse, os, re, sys


def read(path):
    """-> (comments, tool, rpm, moves) of one single-tool file in Makera CAM layout."""
    comments, tool, rpm, moves, air = [], None, None, [], False
    for raw in open(path).read().split("\n"):
        s = raw.strip()
        if not s or s == "%" or s == "G90 G21":
            continue
        if s.startswith(";"):
            comments.append(s[1:].strip())
        elif re.fullmatch(r"T\d+ M6", s):
            if tool is not None:
                sys.exit(f"error: {path} already has more than one tool change")
            tool = int(s.split()[0][1:])
        elif re.fullmatch(r"S\d+ M3", s):
            rpm = int(s.split()[0][1:])
        elif s == "M7":
            air = True
        elif s in ("M9", "M5", "M05", "G28", "M02", "M2"):
            continue
        elif re.match(r"G[01] ", s):
            moves.append(s)
        else:
            sys.exit(f"error: {path}: unexpected line: {s}")
    if tool is None or rpm is None or not moves or not re.match(r"G0 X\S+ Y\S+$", moves[0]):
        sys.exit(f"error: {path} is not a single-tool file in the expected layout")
    return comments, tool, rpm, moves, air


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--title")
    a = ap.parse_args()
    parts = [(os.path.basename(f), *read(f)) for f in a.files]
    out = ["%", "; " + (a.title or "Combined: " + ", ".join(p[0] for p in parts)), "",
           "; Tool List"]
    seen = []
    for name, comments, tool, rpm, moves, air in parts:
        desc = next((c for c in comments if c.startswith(f"T{tool}-")), f"T{tool}")
        if (tool, desc) not in seen:
            seen.append((tool, desc))
            out.append("; " + desc)
    out += ["", "; Path List"] + [f"; [T{p[2]}] {p[0]}" for p in parts]
    origin = {c for p in parts for c in p[1] if c.startswith("Origin:")}
    if len(origin) > 1:
        sys.exit("error: the files do not state the same origin:\n  " + "\n  ".join(sorted(origin)))
    out += [""] + ["; " + c for c in origin] + ["; Made by combine_gcode.py. Check in a viewer and air-cut before milling.",
                                                 "", "G90 G21"]
    cur_tool = cur_rpm = None
    for i, (name, comments, tool, rpm, moves, air) in enumerate(parts):
        if tool != cur_tool and cur_tool is not None:
            out += ["M9", "M5"]
        out += ["", "; " + name]
        if tool != cur_tool:
            out += [f"T{tool} M6"] + (["M7"] if air else []) + [moves[0], f"S{rpm} M3"]
        else:
            out.append(moves[0])                    # same bit: already at the safe height, spindle running
            if rpm != cur_rpm:
                out.append(f"S{rpm} M3")
        out += moves[1:]
        cur_tool, cur_rpm = tool, rpm
    out += ["M9", "M5", "G28", "M02"]
    with open(a.output, "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"{a.output}: {len(parts)} files, tools " + ", ".join(f"T{t}" for t, _ in seen))


if __name__ == "__main__":
    main()

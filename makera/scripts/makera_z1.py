"""Makera Z1 / Z1 Pro facts and helpers: bed holes, Anchor 1, blank layout on the bed dowels, and
program(), which lays a list of moves out as a file the way Makera CAM does. Used by stock_prep.py,
mill_pcb.py and check_gcode.py; import it for any new generator.

BED HOLES. Positions are in millimetres from the centre of the 206 x 206 mm bed, as drawn in the
community reference files (github.com/cinetronixlabs/makera-z1-bed-cad-files, v1.1, CC BY 4.0,
credit: cinetronix), which their author checked against a Z1 and Makera's Z1-MDF-v2.1 STEP file.

ANCHOR 1 (the inner corner of the L-bracket, the machine's corner origin) is at (88, 88) in that
drawing, and machine +X / +Y point towards the drawing's -X / -Y. This is inferred, not published:
the L-bracket is 15 mm wide on a 103 mm half-bed (inner edge at 88), its screw and dowel holes match
the drawing's holes along the +X and +Y edges, and the Z1 firmware measures tool length at
Anchor 1 + (181, 181), which is the drawing's 10 mm corner hole at (-93, -93).
Verify on the machine before relying on it.
"""
import re

BED_HALF = 103.0
ANCHOR1 = (88.0, 88.0)             # drawing coordinates of Anchor 1 (inferred, see above)
TOOL_SETTER_FROM_ANCHOR = (181.0, 181.0)   # from the Z1 firmware (ATCHandler.cpp)
# 4.0 mm dowel holes in the aluminium base (5.0 mm clearance holes through the MDF bed above them)
DOWELS = [(-95.5, 7), (-57, -7), (-57, 38), (-57, 83), (-7, -57), (-7, -7), (-7, 38), (-7, 83), (7, 95.5),
          (38, -57), (38, -7), (38, 83), (83, -57), (83, -7), (83, 38), (83, 83), (95.5, 7)]
# M5 screw positions in the MDF bed (5.5 mm hole, 10 mm counterbore)
M5 = [(-93, -56), (-93, -20), (-93, 23), (-93, 60.5), (-93, 92.5), (-70, -56), (-70, -20), (-70, 23), (-70, 60.5),
      (-70, 92.5), (-67, -93), (-22, -93), (-22, -56), (-22, -20), (-22, 23), (-22, 60.5), (-22, 92.5), (18, 95.5),
      (23, -93), (23, -56), (23, -20), (23, 23), (23, 60.5), (58, 91), (63, 13), (69, -93), (69, -56), (69, -17),
      (69, 23), (69, 60.5), (73, 95.5), (91, 58), (92.5, -93), (92.5, -56), (92.5, -17), (95.5, 18)]
PIN_DIA = 4.0
FLIP_X = -7.0                      # bed column used for the flip axis: dowel holes at these Y values
FLIP_YS = [-57.0, -7.0, 38.0, 83.0]


def machine(p):
    """Drawing coordinates -> millimetres from Anchor 1 in machine axes (X right, Y back)."""
    return (ANCHOR1[0] - p[0], ANCHOR1[1] - p[1])


def stock_layout(width, height, edge=5.0):
    """Two dowel holes on the blank's vertical centre line that match two bed holes in column FLIP_X.

    A blank flipped left to right about that line goes back on the same two pins in the same place.
    The blank must stay inside Anchor 1 (clear of the L-bracket) and on the bed; of the pairs that
    allow that, the widest is used. Everything returned is in machine axes:
      pins_stock   pin centres from the blank's front-left corner
      pins_anchor  the same two bed holes from Anchor 1
      origin       the blank's front-left corner from Anchor 1 (the work origin once pinned)
      pins_bed     the two bed holes in drawing coordinates
    """
    best = None
    for i, a in enumerate(FLIP_YS):
        for b in FLIP_YS[i + 1:]:
            span = b - a
            y0 = (height - span) / 2
            x_lo, x_hi = FLIP_X - width / 2, FLIP_X + width / 2
            y_lo, y_hi = a - y0, a - y0 + height
            fits = (y0 >= edge and x_hi <= ANCHOR1[0] and y_hi <= ANCHOR1[1]
                    and x_lo >= -BED_HALF and y_lo >= -BED_HALF)
            if fits and (best is None or span > best[0]):
                best = (span, a, b, y0, x_hi, y_hi)
    if best is None:
        raise SystemExit(f"error: no pair of bed dowel holes fits a {width} x {height} mm blank inside Anchor 1")
    span, a, b, y0, x_hi, y_hi = best
    origin = machine((x_hi, y_hi))
    return {"pins_stock": [(width / 2, y0), (width / 2, y0 + span)],
            "pins_anchor": [machine((FLIP_X, b)), machine((FLIP_X, a))],
            "pins_bed": [(FLIP_X, b), (FLIP_X, a)], "origin": origin, "span": span}


# ---- G-code file format: the same as Makera CAM writes ----
# Makera Studio only previews (and so probably only fully understands) files laid out like Makera CAM's
# own output; resources/Moby Power Mixer.nc is the reference. A file with "M6 T3", "M3 S12000", separate
# G90 / G21 lines and feeds on lines of their own loads in Studio with zero travel and an empty preview.
#
#   %                      first line
#   ; comments             semicolon comments
#   G90 G21                on one line
#   T3 M6                  tool change: T first
#   M7                     air on (M9 before the spindle stops), as Makera CAM does
#   G0 X.. Y..             XY BEFORE Z: the tool change leaves the spindle high over the tool setter
#   S12000 M3              spindle on: S first
#   G0 Z15 / G0 Z2 / G1 Z-0.3 F300 / G1 X.. Y.. F500 ...   feed on the move that uses it
#   M9 / M5 / G28 / M02    air off, spindle off, lift and park at the clearance position, end of program

def num(v):
    t = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


def tidy(lines):
    """Trim numbers and move stand-alone feed lines ("G1 F500") onto the next cutting move."""
    out, pending = [], None
    for line in lines:
        m = re.fullmatch(r"G1 F(-?[\d.]+)", line.strip())
        if m:
            pending = m.group(1)
            continue
        line = re.sub(r"([XYZF])(-?\d+\.?\d*)", lambda k: k.group(1) + num(k.group(2)), line.strip())
        if line.startswith("G1"):
            if "F" not in line and pending is not None:
                line += " F" + num(pending)
            pending = None
        out.append(line)
    return out


def program(comments, tool, rpm, moves, air=True):
    """A complete file. `moves` are G0/G1 lines and must start with the rapid to the first XY position."""
    moves = tidy(moves)
    if not re.match(r"G0 X\S+ Y\S+$", moves[0]):
        raise SystemExit(f"error: the first move must be an XY rapid, not: {moves[0]}")
    head = ["%"] + ["; " + str(c) for c in comments] + ["", "G90 G21", "", f"T{int(tool)} M6"]
    if air:
        head.append("M7")
    head += [moves[0], f"S{int(rpm)} M3"]
    return head + moves[1:] + (["M9"] if air else []) + ["M5", "G28", "M02"]

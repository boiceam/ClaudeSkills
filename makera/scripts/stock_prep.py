#!/usr/bin/env python3
"""Stock prep for the Makera Z1: mill two dowel pin holes in a blank PCB stock.

    python3 stock_prep.py [--width 152 --height 152 --thickness 1.6] [--out-dir DIR]

Not specific to any board. Run it once per blank, with the blank in the machine's corner
(against the L-bracket, Anchor 1) and the work origin on the blank's front-left corner.
The two holes are on the blank's vertical centre line and match two of the dowel holes in
the Z1 bed, so the blank can afterwards be pinned to the bed, and flipped left to right onto
the same pins, in the same place. mill_pcb.py ("pins" enabled) makes board files for a blank
prepared this way.

Writes the G-code, a setup sheet and a bed layout drawing. Feeds, speed and depth per turn
default to Makera's PCB reference values for its corn bits. Not simulated: air-cut first.
"""
import argparse, math, os, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import makera_z1 as z1

OUT = os.path.expanduser("~/Documents/Projects/Makera/pcb_prep")   # the user's Makera folder


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--width", type=float, default=152.0)
    ap.add_argument("--height", type=float, default=152.0)
    ap.add_argument("--thickness", type=float, default=1.6)
    ap.add_argument("--hole-dia", type=float, default=z1.PIN_DIA, help="finished hole diameter (default 4.0)")
    ap.add_argument("--tool", type=int, default=3, help="tool number for the corn bit (Makera's libraries use T3)")
    ap.add_argument("--tool-dia", type=float, default=1.0)
    ap.add_argument("--through", type=float, default=0.4, help="how far below the blank to cut")
    ap.add_argument("--step", type=float, default=0.3, help="depth per turn")
    ap.add_argument("--feed", type=float, default=500)
    ap.add_argument("--plunge", type=float, default=300)
    ap.add_argument("--rpm", type=int, default=12000)
    ap.add_argument("--travel-z", type=float, default=2.0)
    ap.add_argument("--safe-z", type=float, default=15.0)
    ap.add_argument("--out-dir", default=OUT)
    a = ap.parse_args()
    if a.tool_dia >= a.hole_dia:
        raise SystemExit("error: the end mill must be smaller than the hole")
    lay = z1.stock_layout(a.width, a.height)
    depth = a.thickness + a.through
    r = (a.hole_dia - a.tool_dia) / 2
    seg = 48
    moves, first = [], True
    for cx, cy in lay["pins_stock"]:
        moves.append(f"G0 X{cx + r:.4f} Y{cy:.4f}")
        if first:
            moves.append(f"G0 Z{a.safe_z:.4f}")
            first = False
        moves += [f"G0 Z{a.travel_z:.4f}", f"G1 Z0 F{a.plunge:.1f}", f"G1 F{a.feed:.1f}"]
        turns = math.ceil(depth / a.step)
        for k in range(1, turns * seg + seg + 1):       # helix down, then one flat turn at full depth
            ang = 2 * math.pi * (k % seg) / seg
            z = -min(depth, depth * k / (turns * seg))
            moves.append(f"G1 X{cx + r * math.cos(ang):.4f} Y{cy + r * math.sin(ang):.4f} Z{z:.4f}")
        moves.append(f"G0 Z{a.travel_z:.4f}")
    moves.append(f"G0 Z{a.safe_z:.4f}")
    g = z1.program([f"Stock prep {a.width:g} x {a.height:g} x {a.thickness:g} mm: two {a.hole_dia:g} mm dowel pin holes",
                    f"T{a.tool}-{a.tool_dia:g}mm Corn, {depth:g} mm deep",
                    "Origin: X0 Y0 at the front-left corner of the blank, Z0 on the top surface",
                    "Made by stock_prep.py. Check in a viewer and air-cut before milling."], a.tool, a.rpm, moves)
    os.makedirs(a.out_dir, exist_ok=True)
    base = os.path.join(a.out_dir, f"Stock Prep {a.width:g}x{a.height:g}")
    open(base + " - Dowel Holes.nc", "w").write("\n".join(g) + "\n")

    (p1, p2), (m1, m2), (ox, oy) = lay["pins_stock"], lay["pins_anchor"], lay["origin"]
    sheet = f"""Stock prep {a.width:g} x {a.height:g} x {a.thickness:g} mm - Makera Z1
{'=' * 50}

NOT SIMULATED. Check the file in a G-code viewer and air-cut before milling.
All positions are in machine axes: X to the right, Y to the back.

1. Prepare the blank (once per blank)
   - Put the blank in the machine's corner, against the L-bracket (Anchor 1). Set the work origin:
     X0 Y0 on the blank's front-left corner, Z0 on its top surface.
   - Run "Stock Prep {a.width:g}x{a.height:g} - Dowel Holes.nc".
     Tool T{a.tool}: {a.tool_dia:g} mm corn bit, {a.rpm} rpm, feed {a.feed:g}, {a.step:g} mm per turn.
     It cuts {depth:g} mm deep, so {a.through:g} mm into the bed under the blank.
   - Holes ({a.hole_dia:g} mm), measured from the blank's front-left corner:
       X {p1[0]:.2f} Y {p1[1]:.2f}   and   X {p2[0]:.2f} Y {p2[1]:.2f}   ({lay['span']:g} mm apart, on the vertical centre line)

2. Pin the blank to the bed
   - Use the two bed dowel holes at X {m1[0]:g} Y {m1[1]:g} and X {m2[0]:g} Y {m2[1]:g} from Anchor 1
     (see the bed layout drawing): the column of four dowel holes near the middle of the bed,
     skipping the one nearest the front.
   - The blank's front-left corner is then X {ox:g} Y {oy:g} from Anchor 1. Set the work origin there
     (Anchor 1 plus that offset, or by probing the blank's corner) and keep it for every board file.
   - The blank then covers X {ox:g} to {ox + a.width:g}, Y {oy:g} to {oy + a.height:g} from Anchor 1, clear of the L-bracket.

3. Flipping
   - Lift the blank, turn it over left to right, and put it back on the same two pins.
     It sits in the same place, so the work origin does not change. Do not re-probe the corner.

Notes
   - Each file ends with G28, which lifts the spindle and parks it at the machine's clearance position.
   - The pins must not stand higher than the safe height of the board files ({a.safe_z:g} mm above the blank).
   - Keep the board at least 6 mm clear of both pin centres. The board has to fit between or beside the pins.
   - The bed hole positions are from a community reference drawing and the Anchor 1 position is inferred
     from it and the firmware (see makera_z1.py). Before milling a board, check that the pins drop
     through the prepared blank into the bed, and that the corner lands where this sheet says.
"""
    open(base + " - Setup.txt", "w").write(sheet)

    # bed layout drawing in machine axes, origin at Anchor 1, seen from above with the front at the bottom
    lo, hi = z1.ANCHOR1[0] - z1.BED_HALF, z1.ANCHOR1[0] + z1.BED_HALF        # bed extents from Anchor 1
    used = [tuple(m1), tuple(m2)]
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="660" height="740" viewBox="{lo - 12} {-hi - 12} {hi - lo + 24} {hi - lo + 50}" font-family="Helvetica,Arial,sans-serif">',
         f'<rect x="{lo - 12}" y="{-hi - 12}" width="{hi - lo + 24}" height="{hi - lo + 50}" fill="white"/>',
         f'<rect x="{lo}" y="{-hi}" width="{hi - lo}" height="{hi - lo}" fill="#eee" stroke="#555" stroke-width="0.6"/>',
         f'<path d="M{lo} {-lo} H100 V0 H0 V-100 H{lo} Z" fill="#bbb" stroke="#555" stroke-width="0.4"/>',
         f'<rect x="{ox}" y="{-(oy + a.height)}" width="{a.width}" height="{a.height}" fill="#c8e6c9" fill-opacity="0.8" stroke="#2e7d32" stroke-width="0.6"/>',
         f'<line x1="{m1[0]}" y1="{-(oy + a.height) - 3}" x2="{m1[0]}" y2="{-oy + 3}" stroke="#2e7d32" stroke-width="0.4" stroke-dasharray="3 2"/>']
    s += [f'<circle cx="{x}" cy="{-y}" r="2.75" fill="white" stroke="#bbb" stroke-width="0.4"/>' for x, y in map(z1.machine, z1.M5)]
    s += [f'<circle cx="{x}" cy="{-y}" r="2" fill="{"#c62828" if (x, y) in used else "#555"}"/>' for x, y in map(z1.machine, z1.DOWELS)]
    tx, ty = z1.TOOL_SETTER_FROM_ANCHOR
    s += [f'<circle cx="{tx}" cy="{-ty}" r="5" fill="none" stroke="#555" stroke-width="0.6"/>',
          f'<text x="{tx - 8}" y="{-ty + 10}" font-size="4.2" fill="#555" text-anchor="end">tool setter</text>',
          '<circle cx="0" cy="0" r="1.6" fill="#1565c0"/>',
          '<text x="3" y="6" font-size="4.6" fill="#1565c0">Anchor 1 (0, 0)</text>',
          f'<text x="{lo + 2}" y="{-lo - 4}" font-size="4.2" fill="#333">L-bracket</text>']
    for (x, y) in used:
        s.append(f'<text x="{x + 4}" y="{-y + 1.6}" font-size="4.6" fill="#c62828">pin X {x:g} Y {y:g}</text>')
    s += [f'<text x="{ox + 3}" y="{-(oy + a.height) + 8}" font-size="4.6" fill="#2e7d32">blank {a.width:g} x {a.height:g}; front-left corner X {ox:g} Y {oy:g} = work origin</text>',
          f'<text x="{lo}" y="{-lo + 10}" font-size="4.6">Makera Z1 bed seen from above, front of the machine at the bottom. Millimetres from Anchor 1.</text>',
          f'<text x="{lo}" y="{-lo + 17}" font-size="4.6">Dark dots: 4 mm dowel holes. Red: the two used. Open circles: M5 screws. Dashed: flip axis.</text>',
          f'<text x="{lo}" y="{-lo + 24}" font-size="4.6">Hole positions from a community drawing; Anchor 1 position inferred. Check on the machine.</text>',
          '</svg>']
    svg = base + " - Bed Layout.svg"
    open(svg, "w").write("\n".join(s))
    outs = [base + " - Dowel Holes.nc", base + " - Setup.txt"]
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", base + " - Bed Layout.pdf", svg], check=True)
        os.remove(svg); outs.append(base + " - Bed Layout.pdf")
    else:
        outs.append(svg)
    print("\n".join(os.path.abspath(o) for o in outs))


if __name__ == "__main__":
    main()

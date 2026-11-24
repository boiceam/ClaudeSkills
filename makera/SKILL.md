---
name: makera
description: Generate, check and review G-code for the user's Makera Z1 desktop CNC (also relevant to Makera Z1 Pro, Carvera and Carvera Air), and mill PCBs on it. Use this whenever the user wants a G-code or .nc file for the Makera, asks why a file will not preview or run in Makera Studio, wants feeds and speeds for Makera bits, asks about the Z1 bed, dowel holes, anchor or work origin, wants to mill, drill, isolate, cut out or prepare stock for a PCB, mentions FlatCAM, stock prep, dowel pins or flipping a board, or mentions the Makera, the Z1, the CNC or "the mill" in a machining context, even if they do not say "skill".
---

# Makera Z1

Everything needed to write G-code that the user's Makera Z1 will accept, preview and run, plus the
PCB milling workflow built on it.

**This skill never runs the machine.** It writes files. The user uploads and runs them from Makera
Studio. Only stock prep has been cut so far (2026-11-24, it worked); no board has been milled. Say
so, and tell the user to air-cut a new kind of job first.

How sure each fact is matters, because a wrong number crashes a bit. The reference files label facts:
**Makera** (published by Makera or read from its firmware source), **Tested** (confirmed with the
user's Makera Studio), **Community** (bed drawing by a Z1 owner), **Inferred** (worked out, not
stated anywhere; verify on the machine).

## The file layout is not optional

Write files exactly the way Makera CAM does. Use `makera_z1.program()`; do not hand-assemble headers.

```
%
; comments
G90 G21
T3 M6
M7
G0 X77.5 Y28.5
S12000 M3
G0 Z15
G0 Z2
G1 Z-0.3 F300
G1 X.. Y.. F500
...
G0 Z15
M9
M5
G28
M02
```

Three rules carry real consequences:

1. **Tool change is `T<n> M6`, T first.** Makera Studio shows no preview for `M6 T3` or for a file
   with no tool change (Tested). The firmware accepts either order, so the file would still run,
   but the user cannot check it.
2. **After the tool change, move XY before Z.** The Z1 measures the tool on the tool setter at the
   back-right of the bed and leaves the spindle there, high up. Z down first brings the bit down
   beside the tool setter (Makera firmware; Makera CAM and Makera's Fusion post both go XY first).
3. **One tool per file, and every physical bit gets its own number.** `M6` to the tool number
   already loaded does nothing: no prompt, no length measurement (Makera firmware).

`references/gcode.md` has the supported codes, what the firmware does with each, and the smaller
layout facts.

## Writing a file

```python
import sys; sys.path.insert(0, "<skill-dir>/scripts")
import makera_z1 as z1

moves = ["G0 X10 Y10", "G0 Z15", "G0 Z2", "G1 Z-0.3 F300", "G1 X40 Y10 F500", "G0 Z2", "G0 Z15"]
lines = z1.program(["Job title", "T3-1mm Corn", "Origin: ..."], tool=3, rpm=12000, moves=moves)
open("job.nc", "w").write("\n".join(lines) + "\n")
```

- `moves` are `G0`/`G1` lines only and must start with the rapid to the first XY position.
  `program()` trims numbers and moves a stand-alone `G1 F500` onto the next cut.
- Then always run the checker and fix every failure before giving the file to the user:

```bash
python3 <skill-dir>/scripts/check_gcode.py job.nc [--blank 152x152] [--max-depth 2.5]
```

  It checks the layout, the XY-before-Z rule, that sideways rapids stay at or above 2 mm, that
  cuts have the spindle on and a feed, depth, and (with `--blank`) that cuts stay on the blank and
  clear of the dowel pins. It is a static check, not a simulation.
- To confirm a preview without asking the user to describe it: after they open the file, read the
  newest log in `~/Library/Application Support/MakeraStudio/logs/`. Each load logs
  `Total distance:` and the X, Y, Z bounds. Zero distance means Studio parsed no moves.

## Feeds and speeds

Use Makera's reference values for its own bits unless the user gives others. PCB/FR4:

| Tool (Makera number) | rpm | Feed | Plunge | Depth of cut |
|---|---|---|---|---|
| Engraving bit for metal, 60 deg 0.1 mm or 30 deg 0.2 mm (T2) | 12,000 | 500 | 200 | 0.1 mm |
| Corn bit, 0.6 to 3.175 mm (T3) | 12,000 | 500 | 300 | 0.3 mm per pass |
| Drill, 0.2 to 1.2 mm (T4) | 10,000 | n/a | 200 | peck 1.0 mm |
| UV solder mask removal tool, 0.3 mm tip (T5) | 6,000 | 400 | 200 | press 0.2 mm; 0.2 mm stepover |

`references/feeds_speeds.md` has the other materials (wood, plastic, aluminium, copper), bit
cutting lengths, the V-bit width formula, the user's own bit inventory, and where sources disagree.

## The bed and the work origin

- 206 mm square bed, 200 x 200 x 100 mm travel. X to the right, Y to the back.
- **Anchor 1** is the inner corner of the L-bracket at the front-left. Positions in this skill are
  millimetres from Anchor 1. Its place on the bed was worked out, then confirmed on the machine.
- The tool setter is at Anchor 1 + (181, 181). Keep stock and clamps away from it.
- The bed has 17 dowel holes for 4 mm pins and 36 M5 screw positions.

`references/bed.md` has every hole position and how Anchor 1 was worked out. `makera_z1.py` holds
the same data (`DOWELS`, `M5`, `machine()`, `stock_layout()`).

## Milling a PCB

The user mills two-layer boards from 152 x 152 x 1.6 mm blanks located on two 4 mm dowel pins, so a
blank can be flipped left to right and land in the same place.

```bash
python3 <skill-dir>/scripts/stock_prep.py                      # once per blank: two dowel holes
python3 <skill-dir>/scripts/mill_pcb.py "<KiCad project dir>"  # a board's files, from its Gerbers
python3 <skill-dir>/scripts/check_gcode.py --blank 152x152 --origin anchor1 "<project>/outputs/Milling/"*.nc
```

The user's standing choices: work origin at Anchor 1, the 30 deg 0.2 mm engraving bit for all
isolation (planned with `plan_width`, which over-mills slightly), double-sided tape under the blank
and so no cutout tabs.

`references/pcb_workflow.md` has the run order, the blank and pin positions, `milling.json`
settings, bit choice and over-milling, FlatCAM Evo, and the limits.

## Where things live

| Path | What |
|---|---|
| `<skill-dir>/scripts/` | The four scripts. This is their home; `~/Documents/Projects/Makera/tools/` links here |
| `~/Documents/Projects/Makera/MAKERA_Z1_PCB_MILLING.md` | The user's own write-up of the same material |
| `~/Documents/Projects/Makera/pcb_prep/` | Stock prep output (G-code, setup sheet, bed layout PDF) |
| `~/Documents/Projects/Makera/resources/` | Makera's Fusion 360 post (`Carvera.cps`), tool libraries, and `Moby Power Mixer.nc`, a real Makera CAM file |
| `<KiCad project>/outputs/Milling/` | A board's G-code and setup sheet. `<project>/milling.json` holds its settings |

## When something new is learned

This machine's rules were found by reading firmware and by testing, and some are still inferred.
When a cut, a preview test or the user's experience confirms or contradicts something here, update
the reference file and its label, and the checker if a rule changes.

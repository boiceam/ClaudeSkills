# Milling a two-layer PCB on the Makera Z1

The user's workflow, set up for the Moby Tester in November 2026. No board has been cut with it
yet: air-cut first, and expect to adjust.

## Contents
- Overview
- Stock prep
- Pinning and flipping
- Board files
- Settings (`milling.json`)
- Choosing the isolation bit
- Designing a board for milling
- FlatCAM Evo
- Limits and unverified points

## Overview

1. **Stock prep**, once per blank: mill two dowel holes in the blank.
2. **Pin** the blank to two of the bed's dowel holes.
3. **Mill** the board from numbered files, flipping the blank on the pins for the bottom side.

A blank flipped left to right about the line through its two pin holes goes back in the same
place, so the work origin is set once and survives both flips.

## Stock prep

`scripts/stock_prep.py [--width 152 --height 152 --thickness 1.6]` writes to
`~/Documents/Projects/Makera/pcb_prep/` (or `--out-dir`):

- `Stock Prep <W>x<H> - Dowel Holes.nc`
- `Stock Prep <W>x<H> - Setup.txt`
- `Stock Prep <W>x<H> - Bed Layout.pdf`

For the 152 x 152 mm blank:

- Put the blank against the L-bracket. Origin on its front-left corner, Z0 on top.
- The file mills two 4.0 mm holes with the 1.0 mm corn bit (T3), 2.0 mm deep (0.4 mm into the
  bed), at X 76 Y 28.5 and X 76 Y 123.5: on the blank's vertical centre line, 95 mm apart.
- The nominal 4.0 mm holes fit the user's 4 mm dowels: tight but usable (cut 2026-11-24, the first
  job run on the machine from this skill). `--hole-dia` adjusts it.
- With the blank against the L-bracket its corner is at Anchor 1, so this file's origin is Anchor 1.

`makera_z1.stock_layout(width, height)` picks the bed holes: the widest pair in the bed's X = 95
column that keeps the blank on the bed and clear of the L-bracket. For 152 mm that excludes the
140 mm pair, which would push the blank 1 mm into the bracket.

## Pinning and flipping

- Pins go through the blank into the bed dowel holes at (95, 50) and (95, 145) from Anchor 1.
- The blank's front-left corner is then at Anchor 1 + (19, 21.5).
- **The user's choice of origin is Anchor 1** (`"pins": {"origin": "anchor1"}`): the dowel holes are
  fixed relative to it, so nothing has to be probed. The alternative, `"blank"`, puts X0 Y0 on the
  blank's corner. Either way set it once and keep it; do not re-probe after flipping.
- The blank covers X 19 to 171, Y 21.5 to 173.5. Its back-right corner is about 12 mm from the
  tool setter's centre: check they do not touch.
- To flip: lift, turn over left to right, back onto the same two pins.
- Pins must stand lower than the files' safe height (15 mm above the blank).

## Board files

`scripts/mill_pcb.py "<KiCad project dir>"` reads `<project>/outputs/Fabrication/` (`*F_Cu.gtl`,
`*B_Cu.gbl`, `*Edge_Cuts.gm1`, `*.drl`, and `*F_Mask.gts`, `*B_Mask.gbs` for mask removal) and
writes numbered files and `<Project> - Milling Setup.txt` to `<project>/outputs/Milling/`.

| Order | File | Side up | Tool |
|---|---|---|---|
| 1 | `top_isolation` | Top | T2 engraving bit |
| | `top_copper_clear` (only with `clear`) | Top | T2 |
| | Flip | | |
| 2 | `bottom_isolation` | Bottom | T2 |
| | `bottom_copper_clear` (only with `clear`) | Bottom | T2 |
| | Take off, apply and cure UV solder mask on both sides, refit bottom up | | |
| 3 | `bottom_mask_removal` | Bottom | T5 |
| | Flip | | |
| 4 | `top_mask_removal` | Top | T5 |
| 5 | `drill_<size>mm`, one per drilled size | Top | T4, then the next free numbers |
| 6 | `mill_holes`: every hole not drilled | Top | T3 corn bit |
| 7 | `cutout`: board outline with tabs | Top | T3 |

- Steps 4 to 7 need nothing done to the blank between them, so they can be joined into one file
  with `combine_gcode.py` (the user asked for this on the Moby Tester). `mill_pcb.py` deletes every
  `.nc` in the output folder when it regenerates, so re-run the combine afterwards.
- **Isolation only cuts a line around each conductor. It does not remove the copper anywhere
  else.** A milled board keeps copper everywhere the design has none, on both sides, as isolated
  islands. Where that matters (under an antenna, between high-voltage nets), turn on `clear`: it
  rubs out the copper inside the board's copper keep-out areas, on each side the keep-out covers,
  with the isolation bit at the isolation depth. The areas are read from the `.kicad_pcb` (rule
  areas that forbid copper fill, including ones inside footprints) using KiCad's Python and
  `scripts/kicad_keepouts.py`, or given as `clear.areas`. Found on the Moby Tester 2026-11-24: the
  Pico W antenna keep-out was left full of copper after isolation.
- Holes and the cutout are last so the mask does not fill them and the board stays attached to
  the blank through both flips.
- The origin is Anchor 1 or the blank's front-left corner (pins enabled, see `pins.origin`), or the
  board outline's corner (pins disabled). The setup sheet says which and where the board sits.
- Bottom-side files are mirrored about the blank's vertical centre line.
- The setup sheet lists every copper gap narrower than the isolation cut, with its KiCad
  position. Do not hand over files with such gaps without telling the user.
- Run `check_gcode.py --blank 152x152 --origin anchor1` (or `--origin blank`) on the results.

Milled boards have no plated holes. Vias need a soldered wire or a rivet (0.4 mm solid rivet in
a 0.4 mm hole is the plan; fit not yet tried). Through-hole pads connect only where soldered.

## Settings (`milling.json`)

Defaults are in `DEFAULTS` at the top of `mill_pcb.py`: Makera's feeds, speeds and depths, and
Makera's tool numbers. A project's `milling.json` holds only what differs. The Moby Tester's:

```json
{
  "isolation": {"tip_dia": 0.2, "angle": 30, "cut_z": -0.07, "passes": 3, "overlap": 30, "plan_width": 0.2},
  "drill": {"dias": [0.4, 1.0]},
  "mill": {"dia": 1.0},
  "cutout": {"tabs": "None"},
  "clear": {"enabled": true},
  "pins": {"enabled": true, "origin": "anchor1"},
  "stock": {"width": 152.0, "height": 152.0, "board_offset": null},
  "mask": {"enabled": true}
}
```

| Key | Meaning |
|---|---|
| `isolation` | V-bit tip, angle, depth, number of passes and overlap percent |
| `isolation.plan_width` | Plan the paths as if the cut were this wide. Use when the real cut is wider than the smallest gap; see Over-milling |
| `drill.dias` | Hole sizes drilled with a bit of exactly that size. Everything else is milled |
| `drill.peck` | Peck depth, default 1.0; 0 for one stroke |
| `mill` | Corn bit diameter, depth, depth per pass |
| `clear.enabled` | Add the copper clearing files for the board's copper keep-out areas |
| `clear.areas` | Optional. Areas to clear instead of reading the board: `[{"top": true, "bottom": true, "pts": [[x, y], ...]}]` in KiCad millimetres |
| `cutout` | Tab pattern (FlatCAM's: `None`, `4`, `2LR`, `2TB`, `8`, ...) and tab width. The user holds the blank with double-sided tape and uses `None` |
| `pins.enabled` | The blank was prepared with `stock_prep.py` and is on the dowel pins |
| `pins.origin` | `"anchor1"` or `"blank"` (default) |
| `stock` | Blank size, and `board_offset` [x, y] of the board on it; `null` centres it |
| `mask.enabled` | Add the two mask removal files |
| `mask.passes` | Times each mask opening is covered (default 1). 2 runs each path forward, then back along itself, without lifting |

The board must stay at least 6 mm from both pin centres and inside the blank; the tool stops with
an error otherwise. With the pins 95 mm apart a centred board can be about 83 mm tall.

## Choosing the isolation bit

See the V-bit width table in `feeds_speeds.md`. With the user's KiCad minimum clearance of
0.2032 mm (8 mil), only the 60 deg 0.1 mm bit at 0.07 mm deep (0.181 mm cut) fits without
over-milling.

**The user prefers the 30 deg 0.2 mm bit for all isolation** (sturdier, and far less sensitive to
depth). Its cut is wider than the smallest gaps, so the paths are planned with `plan_width`.

### Over-milling

If the bit cuts wider than a gap, FlatCAM will not route a path through that gap and the two
conductors stay bridged. `plan_width` tells FlatCAM to plan for a narrower cut that does fit, so
every path is laid as if the bit were that wide. The real, wider bit then removes
`(real width - plan_width) / 2` from every copper edge, everywhere, and that is also the worst
case in the narrow gaps.

For the 30 deg 0.2 mm bit planned at 0.2 mm:

| Depth | Real cut | Lost per copper edge | A 0.3 mm track becomes |
|---|---|---|---|
| 0.05 mm | 0.227 mm | 0.013 mm | 0.273 mm |
| 0.07 mm | 0.238 mm | 0.019 mm | 0.262 mm |
| 0.10 mm | 0.254 mm | 0.027 mm | 0.246 mm |

Each 0.01 mm of extra depth adds 0.003 mm per edge. Bit runout and a worn tip add to this and are
not known. The setup sheet states the over-mill for the settings used.

Keep `plan_width` at or just under the board's minimum clearance; the setup sheet lists any gap
narrower than it.

## Designing a board for milling

The user's KiCad conventions are in `~/Documents/Projects/KiCad/CNC_MILL_READY.md`. In short:
tracks on top, an unbroken ground plane on the bottom, 0.2032 mm minimum track and clearance,
1.2 mm via pads with 0.4 mm holes, enlarged through-hole pads, no 90 degree bends. Makera CAM's
own advice is track and gap above 0.2 mm and holes of 0.4 mm or more; designing to 0.25 mm
clearance would allow the sturdier 30 deg bit.

## FlatCAM Evo

`mill_pcb.py` drives FlatCAM Evo (`/Applications/FlatCAM_EVO.app`, v1.10, from
`github.com/MariusAdrian88/FlatCAM_Evo`) without its window:
`FlatCAM --headless --shellfile=job.tcl`. It is an unsigned build that the user approved by hand.
Set `FLATCAM` to use another copy.

Useful Tcl commands: `open_gerber`, `open_excellon`, `offset`, `mirror -axis X -origin x,y`
(a left-to-right flip), `isolate`, `paint`, `milldrills`, `drillcncjob`, `cutout`, `cncjob`,
`write_gcode`, `check_rules -iso <width> -csv file` (lists gaps narrower than the cut), `bounds`.
`help <command>` inside a script prints the full options.

FlatCAM's own G-code is not in Makera's layout. `mill_pcb.py` takes only its moves and passes
them through `makera_z1.program()`.

## Limits and unverified points

- Nothing has been cut. The files preview in Makera Studio, which shows the path, not the result.
- Confirmed on the machine (2026-11-24): Anchor 1, the pin holes at (95, 50) and (95, 145), and the
  blank position on them. Other blank sizes would use other bed holes, which are not yet tried.
- Bed pockets (dowel clearance holes, M5 counterbores) lie under the board area. The 0.07 mm
  isolation cut may vary over them.
- Mask removal at Makera's numbers needed three runs on real cured mask; see `feeds_speeds.md` for the
  slower two-pass setting being tried.
- The dowel hole fit and the rivet fit are untried.
- There is no simulation of material removal anywhere in this workflow.

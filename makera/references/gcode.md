# G-code for the Makera Z1

Labels: **Makera** = published by Makera or read from its firmware source. **Tested** = confirmed in
the user's Makera Studio on 2026-11-24. **Inferred** = worked out; verify.

## Contents
- Machine and firmware
- File layout
- What Makera Studio's preview needs
- Supported codes
- Firmware behaviour worth knowing
- Differences between Makera's own tools
- Checking a file

## Machine and firmware

- The Z1 runs a branch of Smoothieware shared with the Carvera family; the motion code is Smoothie's
  port of grbl. Source: `github.com/MakeraInc/MakeraZ1Firmware`. (Makera)
- Tool changes are manual (quick-change spindle, no rack). The firmware prompts for the tool and
  then measures its length on the tool setter. (Makera)
- Makera's supported-codes page (`wiki.makera.com/en/supported-codes`) is written for the Carvera;
  the Z1 firmware repository points to it as its documentation. (Makera)

## File layout

Reference: `~/Documents/Projects/Makera/resources/Moby Power Mixer.nc`, a real Makera CAM file.

| Line | Notes |
|---|---|
| `%` | First line. Optional for the preview (Tested), written by Makera CAM |
| `; text` | Comments. `(text)` also works. The firmware cuts every line at the first `;` or `(` |
| `G90 G21` | Absolute, millimetres. One line or two both preview (Tested) |
| `T<n> M6` | Tool change, **T first** |
| `M7` | Air on. Makera CAM writes it after each tool change. Optional |
| `G0 X.. Y..` | First move: XY only |
| `S<rpm> M3` | Spindle on. `M3 S<rpm>` also previews (Tested) |
| `G0 Z15`, `G0 Z2` | Down to a safe height, then to travel height |
| `G1 Z-0.3 F300` | Plunge. Feed on the move that uses it; a feed on its own line also previews (Tested) |
| `G1 X.. Y.. F500` | Cuts. Trailing zeros trimmed (`X18.87`); four decimals also preview (Tested) |
| `G0 Z15` | Retract |
| `M9` | Air off |
| `M5` | Spindle off |
| `G28` | Lift Z to clearance, then park XY at the clearance position |
| `M02` | End of program. The firmware also stops the spindle and air |

Several tools in one file: Makera CAM repeats from `T<n> M6`, with `M9` and `M5` before each
change. Our generators write one file per tool; `scripts/combine_gcode.py -o OUT.nc A.nc B.nc ...`
joins them the same way when nothing has to be done to the stock in between. Consecutive sections
with the same tool number run on without a tool change. The source files must share one origin.

## What Makera Studio's preview needs

- **The tool change must be `T<n> M6`.** With `M6 T3`, or with no tool change, Studio reads the file,
  reports zero travel and shows nothing. (Tested: nine one-difference files)
- Nothing else in the layout matters to the preview. (Tested)
- Studio still uploads and the machine still runs a file it cannot preview. Do not rely on "it
  uploaded" as a check.
- Studio logs each load in `~/Library/Application Support/MakeraStudio/logs/log_*.txt`:
  `Loading Gcode file`, `Total lines`, `Total distance`, then `xmin/xmax`, `ymin/ymax`, `zmin/zmax`.
  Compare those bounds with what the file should cut.
- Files Makera Studio generates itself start with a block of `;@MKR|...` comment lines (machine,
  stock, origin, each tool's shape, each toolpath). It is not needed for the preview. (Tested)

## Supported codes

From Makera's page. The ones a milling file is likely to need:

| Code | What it does | Notes |
|---|---|---|
| `G0`, `G1` | Rapid and feed moves | `F` is modal |
| `G2`, `G3` | Arcs, with I and J | Supported. Our generators use straight segments only |
| `G4 P<s>` | Dwell | |
| `G17`, `G18`, `G19` | Plane | XY is the default |
| `G20`, `G21` | Inches, millimetres | |
| `G28` | Go to the clearance position | Lifts Z first. Not a homing cycle on these machines |
| `G53` | Next move in machine coordinates | Must be first on the line |
| `G54` | Work coordinates | The default; Makera CAM does not write it |
| `G90`, `G91` | Absolute, relative | |
| `G92` | Set work coordinates here | |
| `G32` | Probe a grid and turn on height compensation | `M370` clears it |
| `G38.x` | Probe moves | |
| `M3 S<rpm>`, `M5` | Spindle on, off | |
| `M6 T<n>` | Tool change | T0 is the probe; T-1 is no tool |
| `M7`, `M9` | Air on, off | |
| `M331`, `M332` | Auto vacuum mode on, off | Vacuum follows the spindle |
| `M600` | Pause until resumed | |
| `M2` | End of program | Stops spindle and air |
| `M30` | End of program | Makera: "no action". See below |
| `M220 S<%>`, `M223 S<%>` | Feed and spindle override | |
| `M321`, `M322` | Enter, leave laser mode | |

## Firmware behaviour worth knowing

Read from the Z1 firmware source (`GcodeDispatch.cpp`, `ATCHandler.cpp`, `Robot.cpp`). (Makera)

- A line is cut at the first `;` or `(`. Nothing after either is read, so never put a command
  after a comment on the same line.
- A line with several G or M words is split and run in order. `G90 G21` is fine.
- `M6` acts only with a `T` on the same line. Both `M6 T3` and `T3 M6` work in the firmware.
- `M6` to the tool already loaded does nothing: no prompt and no length measurement.
- `M6` turns the spindle off first, and halts with an error if it will not stop.
- On a manual-change machine `M6` prompts "Please change the tool to: T<n>", then measures the
  tool at Anchor 1 + (181, 181) and lifts Z. It leaves the spindle there.
- `G28` lifts Z to the clearance height, then moves XY to the clearance position.
- `M30` is "delete file" in stock Smoothieware unless the build is in grbl mode. Makera's Fusion
  post writes it, so it is harmless on their builds, but it does nothing useful. Use `M02`.
- `G94` is not on Makera's list and the firmware has no case for it. Makera's Fusion post writes
  it; Makera CAM does not. Leave it out.
- An X, Y, Z or F word on a line by itself repeats the last `G0`/`G1`.

## Differences between Makera's own tools

| | Makera CAM (`.nc`) | Makera's Fusion post (`Carvera.cps`, `.cnc`) |
|---|---|---|
| Comments | `; text` | `(text)` |
| Modes | `G90 G21` | `G90 G94`, `G17`, `G21` |
| Work offset | not written | `G54` after the tool change |
| Tool change | `T<n> M6` | `T<n> M6` |
| Spindle | `S<rpm> M3` | `S<rpm> M3` |
| Air | `M7` / `M9` | only if the operation asks for it |
| End | `M5`, `G28`, `M02` | `M5`, `G28`, `M30` |

Both go XY first after a tool change. We follow Makera CAM.

## Checking a file

`scripts/check_gcode.py FILE.nc ...` fails a file unless:

- every line is one of: `%`, `G90 G21`, `T<n> M6`, `M7`, `M9`, `S<rpm> M3`, `M5`, `G28`, `M02`,
  or a `G0`/`G1` move with X, Y, Z, F words;
- it starts tool change, XY rapid, spindle on, Z move, and ends `M5`, `G28`, `M02`;
- every tool change after the first has the spindle stopped before it and goes to a different
  tool number; `M7` follows a tool change and `M9` is followed by `M5`;
- no Z move comes before the first XY move, at the start and after every tool change;
- every sideways rapid starts and ends at or above 2 mm, and no rapid ends below Z0 except
  re-entering a hole already drilled at that XY;
- every feed move has the spindle on and a feed rate set;
- nothing is deeper than `--max-depth` (default 2.5 mm);
- with `--blank WxH`: cuts stay on the blank and at least 6 mm from the dowel pin centres.

If a job legitimately needs something the checker rejects (arcs, deeper cuts, another code), extend
the checker deliberately. Do not skip it.

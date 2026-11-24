# Makera Z1 bed, Anchor 1 and work origin

Labels: **Makera** = published or from firmware source. **Community** = the cinetronixlabs
reference drawing (`github.com/cinetronixlabs/makera-z1-bed-cad-files`, v1.1, CC BY 4.0, credit
cinetronix), whose author checked it against a Z1 and Makera's `Z1-MDF-v2.1.STEP`.
**Inferred** = worked out here; verify on the machine.

All positions below are millimetres from **Anchor 1**, X to the right, Y to the back, seen from
above with the front of the machine at the bottom.

## The bed

- 206 x 206 mm: an aluminium base under a 6 mm MDF bed. (Community)
- Travel 200 x 200 x 100 mm. (Makera product page)
- The bed spans X and Y from -15 to +191. (Inferred, from the Anchor 1 position)

## Anchor 1

The inner corner of the L-bracket, at the front-left. The firmware names it `anchor1` and places
the tool setter relative to it. (Makera)

Its position on the bed drawing is **Inferred**: (88, 88) in the drawing's centre-origin
coordinates, with machine +X and +Y pointing to the drawing's -X and -Y. Three things agree:

1. The L-bracket model is 15 mm wide on a 103 mm half-bed, putting its inner edge at 88.
2. The bracket's screw and dowel holes line up with the drawing's holes along those two edges.
3. The firmware measures tool length at Anchor 1 + (181, 181), which lands exactly on the
   drawing's 10 mm corner hole at (-93, -93).

`makera_z1.machine((x, y))` converts drawing coordinates to this frame.

## Tool setter

At (181, 181), the back-right corner. (Makera firmware) Every tool change goes there. Its
physical size is not known: keep stock, clamps and dowel pins well clear.

## Dowel holes

17 holes, 4.0 mm in the aluminium base with 5.0 mm clearance holes through the MDF. (Community)

| X | Y values |
|---|---|
| 5 | 5, 50, 95, 145 |
| 50 | 5, 95, 145 |
| 95 | 5, 50, 95, 145 |
| 145 | 5, 50, 95 |

Bracket-locating holes at the bed edges: (183.5, 81), (-7.5, 81), (81, -7.5).

The grid is on 45 and 50 mm steps (5, 50, 95, 145). (50, 50) and (145, 145) are absent.

## M5 screws in the MDF bed

36 positions, each a 5.5 mm hole in a 10 mm counterbore. (Community) These and the dowel
clearance holes are open pockets: thin stock over them is unsupported.

(181, 144), (181, 108), (181, 65), (181, 27.5), (181, -4.5), (158, 144),
(158, 108), (158, 65), (158, 27.5), (158, -4.5), (155, 181), (110, 181),
(110, 144), (110, 108), (110, 65), (110, 27.5), (110, -4.5), (70, -7.5),
(65, 181), (65, 144), (65, 108), (65, 65), (65, 27.5), (30, -3),
(25, 75), (19, 181), (19, 144), (19, 105), (19, 65), (19, 27.5),
(15, -7.5), (-3, 30), (-4.5, 181), (-4.5, 144), (-4.5, 105), (-7.5, 70)

Several of these are under the L-bracket or the tool setter corner; check the drawing in
`~/Documents/Projects/Makera/pcb_prep/Stock Prep 152x152 - Bed Layout.pdf` before using one.

## Setting the work origin

- G-code from this skill puts X0 Y0 at a stated corner of the stock and Z0 on its top surface.
  Say which corner in the file's comments and in what you tell the user.
- Stock pushed into the L-bracket has its front-left corner at Anchor 1.
- Stock on dowel pins sits at a known offset from Anchor 1 (see `pcb_workflow.md`). The Carvera
  controller can set the origin as an offset from Anchor 1; whether Makera Studio does the same for
  the Z1 is not confirmed. Probing the stock corner is the fallback.
- Run auto-levelling for shallow cuts such as PCB isolation.

## Not yet verified

- The Anchor 1 position, and so every number in this file measured from it.
- The tool setter's size and the clearance to stock near the back-right corner.
- Which bed version the user's machine has (the drawing matches the dark grey MDF bed).

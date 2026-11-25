# Feeds and speeds for Makera bits

All values are Makera's own reference numbers, from the "Tool Reference Parameters" tables on its
product pages (read 2026-11-24) and the Fusion 360 tool libraries in
`~/Documents/Projects/Makera/resources/` (2022). They are listed for the bits, across the Carvera
family; none is stated to be tuned for the Z1. Treat them as starting points.

Format below: spindle rpm / feed mm/min / plunge mm/min / depth of cut mm.

## PCB (FR4, copper clad)

| Tool | Values | Notes |
|---|---|---|
| Engraving bit for metal, 60 deg 0.1 mm tip | 12000 / 500 / 200 / 0.1 | For trace isolation |
| Engraving bit for metal, 30 deg 0.2 to 0.5 mm tip | 12000 / 500 / 200 / 0.1 | |
| Corn bit, 0.6 to 3.175 mm | 12000 / 500 / 300 / 0.3 per pass | Holes, slots, board outline |
| Drill, 0.2 to 3.175 mm | 10000 / n/a / 200 / 1.0 | Read 1.0 as a peck depth |
| UV solder mask removal tool, 0.3 mm tip | 6000 / 400 / 200 / 0.2 | Spring-loaded: 0.2 is how far it is pressed. Stepover 0.2 mm |

- Mask removal, from use (2026-11-24, Moby Tester bottom side): at Makera's 6000 rpm / 400 mm/min one pass
  did not clear all the cured mask; it took three runs. The Moby Tester's `milling.json` now sets
  `"mask": {"feed": 200, "passes": 2}`: 6000 rpm, half the feed, and every opening covered twice
  (`passes` runs each path forward, then back along itself, without lifting). Not yet tried; if it
  clears the mask in one run, make it the default in `mill_pcb.py`.
- The plain (non-metal) "Single Flute Engraving Bit" has no PCB entry. Only the "for Metal" one does.
- Drill plunge: the product page says 200 for PCB; the Fusion library says 100, in a preset
  shared with aluminium and copper. Use 200; drop to 100 if small drills break.
- Corn bit plunge: Fusion library 333, product page 300. Use 300.
- The product page lists 0.2 mm depth of cut for the 0.3 mm drill in PCB, against 1.0 for its
  neighbours. Probably a typo.

## Other materials

| Tool | Aluminium | Brass | Copper | Carbon fibre | Hardwood | Plastic | Softwood / MDF |
|---|---|---|---|---|---|---|---|
| Engraving bit for metal | 12000/500/200/0.2 | 12000/300/100/0.1 | 12000/300/100/0.1 | n/a | 12000/1000/500/1 | 12000/1000/500/1 | 12000/1000/500/2 |
| Engraving bit (plain) | n/a | n/a | n/a | n/a | 12000/1000/500/1 | 12000/1000/500/1 | 12000/1000/500/2 |
| Corn bit | n/a | n/a | n/a | 12000/500/300/0.3 | n/a | n/a | n/a |
| Drill | 10000/-/100/0.2 | 10000/-/100/0.2 | 10000/-/100/0.2 | 10000/-/200/0.5 to 1 | 10000/-/300/1 | 10000/-/300/1 | 10000/-/300/1 |

From the Fusion libraries:

| Tool | Material | Values | Stepover |
|---|---|---|---|
| Single flute end mill, 3.175 mm | Hardwood, plastic | 10000 / 1000 / 300 / 1 | 2 mm |
| Single flute end mill, 3.175 mm | Softwood, MDF, epoxy | 10000 / 1000 / 300 / 2 | 2 mm |
| Single flute end mill, 1.5 and 2 mm | Hardwood, plastic | 10000 / 500 / 300 / 1 | 2 mm |
| Single flute end mill, 2.5 mm | Hardwood, plastic | 10000 / 600 / 300 / 1 | 2 mm |
| End mill for metal, 3.175 mm | Aluminium | 12000 / 500 / 200 / 0.2 | |
| End mill for metal, 3.175 mm | Copper | 12000 / 400 / 200 / 0.2 | |
| Chamfer bit, 45 deg | Default | 12000 / 300 / 200 | |

The Fusion drill presets for hardwood, plastic and softwood use 8000 rpm, against 10000 on the
product page.

## Makera's tool numbers

In the Fusion libraries: T1 end mill, T2 engraving bit, T3 corn bit, T4 drill, T5 solder mask
removal, T6 chamfer. On the Z1 the number is only a label, but `M6` prompts only when it changes,
so give every physical bit its own number (a second drill size takes the next free one).

## Bit dimensions

All Makera bits here have a 3.175 mm (1/8 inch) shank and are about 38 mm long (mask removal tool
about 50 mm).

| Corn bit diameter | Cutting length |
|---|---|
| 0.6 mm | 3 mm |
| 0.7 mm | 4 mm |
| 0.8 mm | 5.5 mm |
| 0.9 mm | 6 mm |
| 1.0 to 1.2 mm | 7 mm |
| 1.3, 1.4 mm | 7.5 mm |
| 1.5, 1.6 mm | 8.5 mm |
| 1.7 to 1.9 mm | 9.5 mm |
| 2.0 to 2.5 mm | 10.5 mm |
| 3.0, 3.175 mm | 12 mm |

Drills: 0.2 mm has a 3.5 mm flute, 0.3 mm 5.5 mm, 0.4 mm 7 mm, 0.5 mm 8.5 mm, 0.6 mm 9.5 mm,
0.7 to 1.2 mm 10 mm, larger 12 mm.

Never cut deeper than the cutting length.

## V-bit cut width

`width = tip + 2 x depth x tan(angle / 2)`

| Bit | Depth | Width |
|---|---|---|
| 60 deg, 0.1 mm tip | 0.07 mm | 0.181 mm |
| 60 deg, 0.1 mm tip | 0.10 mm | 0.215 mm |
| 30 deg, 0.2 mm tip | 0.06 mm | 0.232 mm |
| 30 deg, 0.2 mm tip | 0.10 mm | 0.254 mm |

The cut must be narrower than the smallest gap it has to pass through. A 60 deg bit widens by
0.115 mm for every 0.1 mm of extra depth, so it needs auto-levelling.

## What the user owns (2026-11-24)

- Corn bits 0.6 to 1.5 mm in 0.1 mm steps.
- Drills 0.3 to 1.1 mm in 0.1 mm steps.
- Engraving bits: 30 deg 0.2 mm and 60 deg 0.1 mm.
- UV solder mask removal tool.
- PCB rivets: solid 0.2, 0.3, 0.4, 0.5 mm, and hollow 1.0 mm outside by 0.6 mm inside.
- 4 mm precision dowel pins.
- Blanks: 152 x 152 x 1.6 mm double-sided copper clad.

Ask before assuming any other tool.

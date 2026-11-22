---
name: hand_pcb_assembly
description: Make hand-assembly drawings for a KiCad PCB - a PDF per populated side with one page per BOM line, showing the part number, quantity and reference designators across the top and the bare board with every location for that part highlighted. Use this whenever the user wants an assembly drawing, assembly guide, placement sheets, "where does each part go", a build or soldering guide, a pick-and-place sheet for hand soldering, or a per-part location map for a board they will populate by hand, even if they do not say "hand assembly". Needs a .kicad_pcb and a BOM CSV.
---

# Hand PCB assembly drawings

Turns a KiCad board and its BOM into printable placement sheets for soldering a board by hand:
one page per BOM line, so the builder takes one bag of parts, opens its page, and fits every
highlighted location before moving on.

One script does the work. It **only reads** the board and BOM; it never writes either.

```bash
python3 <skill-dir>/scripts/assembly_pdf.py <board.kicad_pcb> <bom.csv> [options]
```

`<skill-dir>` is the folder containing this file. It prints one JSON summary; exit 0 ok, 1 error
(with a `hint`).

## What it produces

- `<name> hand assembly top.pdf`, and `<name> hand assembly bottom.pdf` if any footprint is on
  the back. A side with no parts gets no document. Tell the user when there is only one.
- Landscape US Letter by default (`--paper a4` for A4).
- Each page: part number large at top left, `Qty N` at top right, the reference designators in
  red beneath, then one line of value, manufacturer and description. Below, the bare board
  (outline, pad openings, silkscreen) with a red box on each location for that line.
- The bottom document is drawn mirrored, as the board looks when turned over.
- A BOM line whose parts are split across sides appears in both documents, showing only that
  side's references and `Qty 2 of 8`.
- Not-fitted lines get a page headed **DO NOT FIT** with blue boxes, so spare footprints are
  not populated by mistake (`--skip-dnp` to leave them out).

## Inputs

- **Board:** a `.kicad_pcb`. The drawing reflects the file as last saved; if KiCad has it open
  with unsaved changes, say so.
- **BOM:** a CSV with one row per line item and a reference-designator column. Columns are
  found by header name (`References`/`Designator`, `Qty`, `Manufacturer Part Number`/`MPN`,
  `Value`, `Description`, `Manufacturer`, `Item`, `DNP`). If the reference column is not
  found the script lists the headers; pass `--map refs='Column Name'` (also `mpn=`, `qty=`
  and so on). References may be separated by spaces or commas, and ranges such as `R1-R4`
  are expanded.
- A line is treated as not fitted when its Value or MPN is `DNP`/`DNF`/`Do not fit`, or a DNP
  column is set.
- If there is no BOM yet, export one first:
  `kicad-cli sch export bom --fields 'Reference,Value,Description,Footprint,Manufacturer,MPN,${QUANTITY}' --labels 'References,Value,Description,Footprint,Manufacturer,Manufacturer Part Number,Qty' --group-by 'MPN,Value' --ref-range-delimiter '' -o bom.csv <root>.kicad_sch`

## Options worth knowing

| Option | Use |
|---|---|
| `--out-dir DIR` | Where the PDFs go. Default is beside the board; write them into the project so the user can open them |
| `--name TEXT` | Base file name and page title. Default is the board file name |
| `--at 'REFS=TARGETS'` | For a BOM line that has no footprint of its own (a module that plugs onto headers, a heatsink, a socketed part), highlight another footprint instead, e.g. `--at 'J6 J7=U3'`. `REFS` must be exactly that line's references |
| `--note 'REFS=text'` | Adds a bold line to that BOM line's page, e.g. fitting order or orientation |
| `--digikey-order CSV` | An order CSV from the `digikey_api` skill. Lines whose Note starts `ALTERNATE` are shown as "Order list substitute: ..." so the part in hand matches the page |
| `--preview N` | Also writes PNGs of the first N pages per side, for checking |
| `--keep-svg` | Keeps the per-page SVGs |

## Workflow

1. Find the board and BOM. Check whether KiCad has the board open (`~*.lck` beside it); the
   script is read-only, but an open editor may hold unsaved changes.
2. Run the script with `--preview 2`.
3. **Read the JSON summary and act on it** before reporting:
   - `bom_refs_not_on_board`: BOM parts with no footprint. Either the BOM and board disagree
     (tell the user), or the part genuinely has no pads; then re-run with `--at` and a `--note`
     saying how it fits. Without `--at` such a line gets no page.
   - `footprints_not_in_bom`: on the board but on no BOM line. Mounting holes, fiducials and
     test points are expected; anything else means the BOM is stale.
   - `refs_on_more_than_one_bom_line`, `qty_differs_from_ref_count`: BOM errors to report.
4. Look at a preview image (and one bottom page if there is a bottom document) to confirm the
   highlights sit on the right pads, then delete the PNGs.
5. Report the file paths, page counts, which sides exist, and anything from step 3. Say how
   many pages you actually looked at.

Pages follow BOM order. If the user wants soldering order (lowest parts first), reorder the BOM
CSV rows into a temporary copy and run on that; do not edit their BOM.

## Things to know

- **Requirements.** KiCad 9 or 10 (uses `kicad-cli` and KiCad's bundled Python for `pcbnew`)
  and `rsvg-convert` (macOS: `brew install librsvg`). Paths are found automatically on macOS;
  otherwise set `KICAD_CLI`, `KICAD_PYTHON` or `RSVG_CONVERT`.
- **Highlight box.** It is the footprint's courtyard, or the bounding box of its pads when
  there is no courtyard. Large through-hole parts with small courtyards get small boxes.
- **Bare board.** Only the outline, solder-mask openings (pads and holes) and silkscreen are
  drawn; tracks are left out on purpose. The board fill comes from the board outline; if the
  outline is not closed the fill is omitted and the page still works.
- **Unusual sizes.** The board is scaled to fit the page under the header, so very long or
  tall boards come out small. A line with very many references wraps and pushes the board
  down.
- **Regenerate** after parts move or the BOM changes; the PDF is a snapshot.
- Verified on KiCad 10.0.6 with a 123-part single-sided board (48 pages), and with a scratch
  copy that had parts flipped to the back to check the mirrored bottom document.

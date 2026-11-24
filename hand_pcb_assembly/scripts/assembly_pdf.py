#!/usr/bin/env python3
"""Hand-assembly drawings for a KiCad board: one PDF per populated side, one page per BOM line.

Each page shows the part number, quantity and reference designators across the top and the bare
board (outline, pads, silkscreen) with every location for that part highlighted.

Reads the board and BOM only; never modifies them. Needs KiCad (kicad-cli and its Python) and
rsvg-convert. Prints one JSON summary; exit 0 ok, 1 error.
"""
import argparse, csv, json, os, re, shutil, subprocess, sys, tempfile, textwrap
from xml.sax.saxutils import escape as E

PAPER = {"letter": (279.4, 215.9), "a4": (297.0, 210.0)}          # landscape, mm
HEADS = {"refs": ["references", "reference", "refs", "ref", "designator", "designators", "reference designators", "ref des"],
         "qty": ["qty", "quantity", "qty per board", "count"],
         "mpn": ["manufacturer part number", "mpn", "mfr part", "mfr part number", "mfg part number", "part number", "manufacturer_part_number"],
         "value": ["value", "val", "comment"],
         "desc": ["description", "desc"],
         "mfr": ["manufacturer", "mfr", "mfg"],
         "item": ["item", "line", "#"],
         "dnp": ["dnp", "do not populate", "do not fit"]}


def fail(msg, hint=None):
    d = {"error": msg}
    if hint:
        d["hint"] = hint
    print(json.dumps(d, indent=2))
    sys.exit(1)


def find_tools():
    cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    py = os.environ.get("KICAD_PYTHON")
    if not py:
        base = "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions"
        cands = [os.path.join(base, "Current", "bin", "python3")]
        if os.path.isdir(base):
            cands += [os.path.join(base, v, "bin", "python3") for v in sorted(os.listdir(base))]
        cands += ["/usr/lib/kicad/bin/python3", "/usr/bin/python3"]
        py = next((c for c in cands if os.path.exists(c)), None)
    rsvg = os.environ.get("RSVG_CONVERT") or shutil.which("rsvg-convert")
    if not os.path.exists(cli):
        fail("kicad-cli not found.", "Install KiCad, or set KICAD_CLI to its path.")
    if not py:
        fail("KiCad's Python (with pcbnew) not found.", "Set KICAD_PYTHON to the interpreter bundled with KiCad.")
    if not rsvg:
        fail("rsvg-convert not found.", "Install librsvg (macOS: brew install librsvg), or set RSVG_CONVERT.")
    return cli, py, rsvg


def run(cmd):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def split_refs(s):
    """'C1 C2', 'C1,C2', 'R1-R4' -> list of references."""
    out = []
    for tok in re.split(r"[\s,;]+", s.strip()):
        if not tok:
            continue
        m = re.fullmatch(r"([A-Za-z_]+)(\d+)-(?:([A-Za-z_]+))?(\d+)", tok)
        if m and (m.group(3) in (None, m.group(1))) and int(m.group(4)) >= int(m.group(2)):
            out += ["%s%d" % (m.group(1), n) for n in range(int(m.group(2)), int(m.group(4)) + 1)]
        else:
            out.append(tok)
    return out


def read_bom(path, overrides):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        fail("The BOM has no rows: %s" % path)
    cols = {}
    lower = {h.strip().lower(): h for h in rows[0].keys() if h}
    for key, names in HEADS.items():
        if key in overrides:
            if overrides[key] not in rows[0]:
                fail("BOM has no column %r." % overrides[key], "Columns: %s" % ", ".join(rows[0].keys()))
            cols[key] = overrides[key]
        else:
            cols[key] = next((lower[n] for n in names if n in lower), None)
    if not cols["refs"]:
        fail("Could not find the reference-designator column in the BOM.",
             "Pass --map refs='Column Name'. Columns: %s" % ", ".join(rows[0].keys()))
    lines = []
    for n, r in enumerate(rows, 1):
        g = lambda k: (r.get(cols[k]) or "").strip() if cols[k] else ""
        refs = split_refs(g("refs"))
        if not refs:
            continue
        dnp = g("dnp").lower() in ("1", "yes", "y", "true", "dnp", "x") or g("value").upper() in ("DNP", "DNF", "DO NOT FIT", "NOT FITTED") \
            or g("mpn").upper() in ("DNP", "DNF")
        lines.append({"item": g("item") or str(n), "refs": refs, "qty": g("qty"), "mpn": g("mpn"), "value": g("value"),
                      "desc": g("desc"), "mfr": g("mfr"), "dnp": dnp, "key": " ".join(refs)})
    return lines, cols


def text(x, y, s, size, weight="normal", anchor="start", fill="#111"):
    return ('<text x="%g" y="%g" font-family="Helvetica, Arial, sans-serif" font-size="%g" font-weight="%s" '
            'text-anchor="%s" fill="%s">%s</text>' % (x, y, size, weight, anchor, fill, E(s)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcb", help=".kicad_pcb file (read only)")
    ap.add_argument("bom", help="BOM CSV, one row per line item, with a reference-designator column")
    ap.add_argument("--out-dir", help="where to write the PDFs (default: an 'outputs' folder beside the board)")
    ap.add_argument("--name", help="base name for the PDFs and page title (default: board file name)")
    ap.add_argument("--paper", choices=sorted(PAPER), default="letter")
    ap.add_argument("--map", nargs="*", default=[], help="BOM column overrides, e.g. refs=Designator mpn='Mfr Part' qty=Qty")
    ap.add_argument("--at", action="append", default=[], metavar="REFS=TARGETS",
                    help="for a BOM line with no footprint of its own, highlight these footprints instead, "
                         "e.g. --at 'J6 J7=U3' (REFS must match the BOM line's references)")
    ap.add_argument("--note", action="append", default=[], metavar="REFS=TEXT", help="extra line of text on that BOM line's page")
    ap.add_argument("--digikey-order", help="order CSV from the digikey_api skill; lines noted ALTERNATE are shown as substitutes")
    ap.add_argument("--skip-dnp", action="store_true", help="leave out pages for not-fitted lines")
    ap.add_argument("--preview", type=int, default=0, metavar="N", help="also write PNGs of the first N pages of each side, for checking")
    ap.add_argument("--keep-svg", action="store_true", help="keep the per-page SVG files (in <out-dir>/<name> assembly svg/)")
    a = ap.parse_args()

    for p in (a.pcb, a.bom):
        if not os.path.isfile(p):
            fail("File not found: %s" % p)
    cli, kpy, rsvg = find_tools()
    here = os.path.dirname(os.path.abspath(__file__))
    name = a.name or os.path.splitext(os.path.basename(a.pcb))[0]
    out_dir = os.path.abspath(a.out_dir or os.path.join(os.path.dirname(os.path.abspath(a.pcb)), "outputs"))
    os.makedirs(out_dir, exist_ok=True)
    overrides = dict(m.split("=", 1) for m in a.map)
    bom, cols = read_bom(a.bom, overrides)
    keyset = {ln["key"] for ln in bom}

    def keyed(items, what):
        d = {}
        for it in items:
            if "=" not in it:
                fail("%s needs REFS=VALUE, got %r" % (what, it))
            k, v = it.split("=", 1)
            k = " ".join(split_refs(k))
            if k not in keyset:
                fail("%s: no BOM line has exactly the references %r." % (what, k))
            d[k] = v.strip()
        return d
    at = {k: split_refs(v) for k, v in keyed(a.at, "--at").items()}
    notes = keyed(a.note, "--note")
    subs = {}
    if a.digikey_order:
        with open(a.digikey_order, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("Note") or "").startswith("ALTERNATE"):
                    subs[" ".join(split_refs(r.get("Customer Reference") or ""))] = \
                        ("%s %s" % (r.get("Manufacturer") or "", r.get("Manufacturer Part Number") or "")).strip()

    r = run([kpy, os.path.join(here, "board_dump.py"), a.pcb])
    if r.returncode != 0 or not r.stdout.strip().startswith("{"):
        fail("Could not read the board with KiCad's Python.", (r.stderr or r.stdout)[-600:])
    brd = json.loads(r.stdout[r.stdout.index("{"):])
    fps = brd["footprints"]
    for k, targets in at.items():
        bad = [t for t in targets if t not in fps]
        if bad:
            fail("--at target not on the board: %s" % " ".join(bad))

    work = tempfile.mkdtemp(prefix="hand_asm_")
    PW, PH = PAPER[a.paper]
    M = 10
    result = {"board": os.path.abspath(a.pcb), "bom": os.path.abspath(a.bom), "bom_lines": len(bom), "bom_columns": cols,
              "sides": {}, "warnings": []}
    on_page = set()

    for side, sname in (("F", "top"), ("B", "bottom")):
        side_refs = {ref for ref, f in fps.items() if f["side"] == side}
        if not side_refs:
            continue
        # artwork for this side, one black-and-white SVG per layer, recoloured below
        art = {}
        for lay in ("Edge.Cuts", side + ".Mask", side + ".SilkS"):
            p = os.path.join(work, "%s_%s.svg" % (side, lay))
            cmd = [cli, "pcb", "export", "svg", "--mode-single", "--black-and-white", "--page-size-mode", "2",
                   "--exclude-drawing-sheet", "--drill-shape-opt", "2", "-l", lay, "-o", p]
            if side == "B":
                cmd.append("--mirror")
            rr = run(cmd + [a.pcb])
            if not os.path.isfile(p):
                fail("kicad-cli could not export layer %s." % lay, (rr.stderr or rr.stdout)[-600:])
            art[lay] = open(p, encoding="utf-8").read()
        vb = re.search(r'viewBox="([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)"', art["Edge.Cuts"])
        if not vb:
            fail("Unexpected SVG from kicad-cli (no viewBox).")
        BW, BH = float(vb.group(3)), float(vb.group(4))
        l, t, rgt, btm = brd["edge_box"]
        OX, OY = l + ((rgt - l) - BW) / 2.0, t + ((btm - t) - BH) / 2.0     # board coordinate of the SVG origin
        fx = (lambda x: BW - (x - OX)) if side == "B" else (lambda x: x - OX)
        fy = lambda y: y - OY

        def inner(svg, colour, hole="#ffffff"):
            body = svg[svg.index("</desc>") + 7:svg.rindex("</svg>")] if "</desc>" in svg else svg[svg.index(">", svg.index("<svg")) + 1:svg.rindex("</svg>")]
            return body.replace("#000000", colour).replace("#FFFFFF", hole)
        fill = ""
        for pg in brd.get("outline") or []:
            d = ""
            for ring in [pg["outer"]] + pg["holes"]:
                if len(ring) >= 3:
                    d += "M" + " L".join("%.3f %.3f" % (fx(x), fy(y)) for x, y in ring) + " Z "
            if d:
                fill += '<path d="%s" fill="#e9f1e6" fill-rule="evenodd" stroke="none"/>' % d
        base = fill + inner(art[side + ".Mask"], "#c9ad5c") + inner(art[side + ".SilkS"], "#4d4d4d") + inner(art["Edge.Cuts"], "#222222")

        pages_for_side = []
        for ln in bom:
            if ln["dnp"] and a.skip_dnp:
                continue
            here_refs = [x for x in ln["refs"] if x in side_refs]
            targets = here_refs
            subst = False
            if ln["key"] in at:
                targets = [x for x in at[ln["key"]] if x in side_refs]
                subst = True
            if not targets:
                continue
            pages_for_side.append((ln, here_refs, targets, subst))
        n = len(pages_for_side)
        svgs = []
        for i, (ln, here_refs, targets, subst) in enumerate(pages_for_side, 1):
            colour = "#1d6fd6" if ln["dnp"] else "#e0201b"
            o = ['<svg xmlns="http://www.w3.org/2000/svg" width="%gmm" height="%gmm" viewBox="0 0 %g %g">' % (PW, PH, PW, PH),
                 '<rect width="%g" height="%g" fill="#ffffff"/>' % (PW, PH),
                 text(M, 9, "%s  -  hand assembly  -  %s side%s" % (name, sname, "  (viewed from the bottom)" if side == "B" else ""), 3.6, fill="#555"),
                 text(PW - M, 9, "Item %s    page %d of %d" % (ln["item"], i, n), 3.6, anchor="end", fill="#555")]
            head = "DO NOT FIT" if ln["dnp"] else (ln["mpn"] or ln["value"] or "(no part number)")
            size = 10 if len(head) <= 30 else max(5.5, 10 * 30.0 / len(head))
            o.append(text(M, 22, head, size, "bold", fill=colour if ln["dnp"] else "#111"))
            shown = here_refs if not subst else ln["refs"]
            total = ln["qty"] or str(len(ln["refs"]))
            qty = "Qty %s" % total
            if not subst and len(here_refs) != len(ln["refs"]):
                qty = "Qty %d of %s" % (len(here_refs), total)
            o.append(text(PW - M, 22, qty, 10, "bold", anchor="end"))
            y = 30
            for w in textwrap.wrap(" ".join(shown), int((PW - 2 * M) / 3.33)):
                o.append(text(M, y, w, 5.6, "bold", fill=colour)); y += 6.6
            desc = "Spare footprints: leave empty" if ln["dnp"] else "   |   ".join(x for x in (ln["value"], ln["mfr"], ln["desc"]) if x)
            for w in textwrap.wrap(desc, int((PW - 2 * M) / 1.73))[:2]:
                o.append(text(M, y + 0.5, w, 3.3, fill="#333")); y += 4.4
            extra = []
            if ln["key"] in subs:
                extra.append("Order list substitute: " + subs[ln["key"]])
            if not subst and len(here_refs) != len(ln["refs"]):
                extra.append("The other %d are on the other side of the board." % (len(ln["refs"]) - len(here_refs)))
            if subst:
                extra.append("No pads of its own: fits at %s." % " ".join(targets))
            if ln["key"] in notes:
                extra.append(notes[ln["key"]])
            for e in extra:
                for w in textwrap.wrap(e, int((PW - 2 * M) / 1.85)):
                    o.append(text(M, y + 0.5, w, 3.3, "bold", fill="#333")); y += 4.4
            top = max(y + 2, 52)
            s = min((PW - 2 * M) / BW, (PH - top - 8) / BH)
            bx, by = (PW - BW * s) / 2, top + (PH - top - 6 - BH * s) / 2
            o.append('<g transform="translate(%g %g) scale(%g)">' % (bx, by, s))
            o.append(base)
            pad = 0.35
            for ref in targets:
                x0, y0, x1, y1 = fps[ref]["box"]
                xa, xb = sorted((fx(x0), fx(x1)))
                o.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" rx="0.4" fill="%s" fill-opacity="0.26" stroke="%s" stroke-width="0.35"/>'
                         % (xa - pad, fy(y0) - pad, xb - xa + 2 * pad, y1 - y0 + 2 * pad, colour, colour))
                on_page.add(ref)
            o.append("</g></svg>")
            p = os.path.join(work, "%s_p%03d.svg" % (side, i))
            open(p, "w", encoding="utf-8").write("\n".join(o))
            svgs.append(p)
        if not svgs:
            result["warnings"].append("Parts are on the %s side but no BOM line places any there." % sname)
            continue
        pdf = os.path.join(out_dir, "%s - Hand Assembly - %s.pdf" % (name, sname.capitalize()))
        rr = run([rsvg, "-f", "pdf", "-o", pdf] + svgs)
        if rr.returncode != 0:
            fail("rsvg-convert failed.", rr.stderr[-600:])
        info = {"pdf": pdf, "pages": len(svgs), "footprints_on_side": len(side_refs)}
        if a.preview:
            info["previews"] = []
            for p in svgs[:a.preview]:
                png = os.path.join(out_dir, "%s - Hand Assembly - %s %s.png" % (name, sname.capitalize(), os.path.basename(p)[2:-4]))
                run([rsvg, "-w", "1700", "-o", png, p]); info["previews"].append(png)
        if a.keep_svg:
            d = os.path.join(out_dir, "%s assembly svg" % name)
            os.makedirs(d, exist_ok=True)
            for p in svgs:
                shutil.copy(p, d)
            info["svg_dir"] = d
        result["sides"][sname] = info
    shutil.rmtree(work, ignore_errors=True)

    bom_refs = [x for ln in bom for x in ln["refs"]]
    dup = sorted({x for x in bom_refs if bom_refs.count(x) > 1})
    covered = {x for ln in bom for x in ln["refs"]} | on_page
    result["bom_refs_not_on_board"] = sorted(x for ln in bom if ln["key"] not in at for x in ln["refs"] if x not in fps)
    result["footprints_not_in_bom"] = sorted(x for x in fps if x not in covered)
    result["refs_on_more_than_one_bom_line"] = dup
    qty_bad = [ln["key"] for ln in bom if ln["qty"].isdigit() and int(ln["qty"]) != len(ln["refs"])]
    result["qty_differs_from_ref_count"] = qty_bad
    if not result["sides"]:
        fail("No pages were produced.", "No BOM reference matched a footprint on the board.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Make CNC milling G-code for a two-layer board from its Gerbers, using FlatCAM Evo.

    python3 mill_pcb.py PROJECT_DIR [--config milling.json] [--fab-dir DIR] [--out-dir DIR]

Reads   <project>/outputs/Fabrication/  (*F_Cu.gtl, *B_Cu.gbl, *Edge_Cuts.gm1, *.drl)
Writes  <project>/outputs/Milling/      (numbered .nc files and a setup sheet)
Optional: dowel pin holes for flipping ("pins") and solder mask removal ("mask",
which also needs *F_Mask.gts and *B_Mask.gbs).

It drives FlatCAM Evo headless with a Tcl script, then rewrites the G-code into the
layout Makera CAM writes (makera_z1.program): one tool per file, starting with
`T<n> M6` (prompted manual tool change with automatic tool-length measurement).

Settings come from DEFAULTS below, overridden by <project>/milling.json or --config.
Feeds, speeds and depths of cut in DEFAULTS are Makera's published PCB reference values
for its own bits (product pages, read 2026-11-24). Bit sizes are per project.
The G-code is not simulated here. Check it in a viewer and air-cut before milling.
"""
import argparse, csv, glob, json, math, os, re, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import makera_z1 as z1

FLATCAM = os.environ.get("FLATCAM", "/Applications/FlatCAM_EVO.app/Contents/MacOS/FlatCAM")

DEFAULTS = {
    "machine": "Makera Z1",
    # Tool numbers follow Makera's Fusion libraries: T2 engraving, T3 corn bit, T4 drill, T5 mask removal.
    # On the Z1 they are only labels, but M6 prompts only when the number changes, so every bit needs its own.
    "travel_z": 2.0,          # height for moves between cuts, mm above the board
    "safe_z": 15.0,           # height for the first approach and at the end
    "isolation": {            # V-bit engraving around the copper
        "tool": 2, "tip_dia": 0.2, "angle": 30, "cut_z": -0.06,
        "passes": 2, "overlap": 20, "feed": 500, "plunge": 200, "rpm": 12000,
        "plan_width": None},  # plan the paths for a cut this wide when the real cut is wider than the gaps (over-mills)
    "drill": {                # hole sizes that are drilled with a bit of that size
        "first_tool": 4, "dias": [0.4], "cut_z": -1.9, "plunge": 200, "rpm": 10000,
        "peck": 1.0},         # peck depth; 0 drills in one stroke
    "mill": {                 # end mill: every other hole, and the board outline
        "tool": 3, "dia": 0.8, "cut_z": -1.8, "depth_per_pass": 0.3,
        "feed": 500, "plunge": 300, "rpm": 12000},
    "cutout": {"tabs": "4", "tab_width": 1.5},
    "pins": {
        "enabled": False,     # True: the blank was prepared with stock_prep.py and sits on the bed dowel pins
        "origin": "blank"},   # "blank": X0 Y0 at the blank's front-left corner; "anchor1": X0 Y0 at Anchor 1
    "stock": {                # the blank, used when pins are enabled
        "width": 152.0, "height": 152.0,
        "board_offset": None},   # [x, y] of the board outline's bottom-left corner on the blank; None = centred
    "mask": {                 # solder mask removal over the mask openings
        "enabled": False, "tool": 5, "dia": 0.3, "overlap": 33,   # 0.2 mm stepover, as Makera's Fusion preset
        "cut_z": -0.2,        # how far the spring-loaded tool is pressed down, not a cut into the board
        "feed": 400, "plunge": 200, "rpm": 6000},
}


def merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def one(folder, pattern):
    hits = sorted(glob.glob(os.path.join(folder, pattern)))
    if len(hits) != 1:
        sys.exit(f"error: expected one file matching {pattern} in {folder}, found {len(hits)}")
    return hits[0]


def drill_sizes(path):
    sizes = {}
    cur = None
    for line in open(path):
        m = re.match(r"T(\d+)C([\d.]+)", line)
        if m:
            sizes[int(m.group(1))] = [float(m.group(2)), 0]
            continue
        m = re.match(r"T(\d+)\s*$", line)
        if m:
            cur = int(m.group(1))
        elif cur in sizes and line.startswith("X"):
            sizes[cur][1] += 1
    return sorted((d, n) for d, n in sizes.values())


def q(p):
    return '"' + p.replace("\\", "/") + '"'


def build_tcl(files, cfg, tmp, plan, place):
    iso, drl, mil, cut = cfg["isolation"], cfg["drill"], cfg["mill"], cfg["cutout"]
    tz, sz = cfg["travel_z"], cfg["safe_z"]
    eff = iso["tip_dia"] + 2 * abs(iso["cut_z"]) * math.tan(math.radians(iso["angle"] / 2))
    plan_w = iso.get("plan_width") or eff
    t = ["set_sys units MM",
         f"open_gerber {q(files['top'])} -outname top",
         f"open_gerber {q(files['bot'])} -outname bot",
         f"open_gerber {q(files['edge'])} -outname edge",
         f"open_excellon {q(files['drl'])} -outname drl",
         'puts "BOUNDS_ORIG [bounds edge]"']
    objs = "top bot edge drl"
    if cfg["mask"]["enabled"]:
        t += [f"open_gerber {q(files['fmask'])} -outname fmask", f"open_gerber {q(files['bmask'])} -outname bmask"]
        objs += " fmask bmask"
    t += [f'offset {objs} -to "{place["to"][0]:.4f},{place["to"][1]:.4f}" -ref bl',
          'puts "BOUNDS_EDGE [bounds edge]"',
          f"check_rules -copper_top top -copper_bottom bot -iso {plan_w:.4f} -csv {q(os.path.join(tmp, 'gaps.csv'))}",
          # bottom-side objects are mirrored left to right about the board's vertical centre line
          f"mirror bot -axis X {place['mirror']}"]
    if cfg["mask"]["enabled"]:
        t.append(f"mirror bmask -axis X {place['mirror']}")
    t.append('puts "BOUNDS_BOT [bounds bot]"')
    msk = cfg["mask"]
    mskjob = (f"-dia {msk['dia']} -z_cut {msk['cut_z']} -z_move {tz} -endz {sz} -feedrate {msk['feed']} "
              f"-feedrate_z {msk['plunge']} -spindlespeed {msk['rpm']} -pp Default_no_M6")
    mskpaint = f"-tooldia {msk['dia']} -overlap {msk['overlap']} -offset 0 -method standard -connect 1 -contour 1"
    shape = (f"-dia {plan_w:.4f} " if iso.get("plan_width") else
             f"-tool_shape V -vtipdia {iso['tip_dia']} -vtipangle {iso['angle']} -cutz {iso['cut_z']} ")
    isoargs = (shape +
               f"-passes {iso['passes']} -overlap {iso['overlap']} -combine True -iso_type 2 -check_valid True")
    isojob = (f"-z_cut {iso['cut_z']} -z_move {tz} -endz {sz} -feedrate {iso['feed']} -feedrate_z {iso['plunge']} "
              f"-spindlespeed {iso['rpm']} -pp Default_no_M6")
    miljob = (f"-dia {mil['dia']} -z_cut {mil['cut_z']} -dpp {mil['depth_per_pass']} -z_move {tz} -endz {sz} "
              f"-feedrate {mil['feed']} -feedrate_z {mil['plunge']} -spindlespeed {mil['rpm']} -pp Default_no_M6")

    def out(key):
        return q(os.path.join(tmp, key + ".nc"))

    for key, kind, arg in plan:
        if kind == "iso_top":
            t += [f"isolate top {isoargs} -outname g_{key}", f"cncjob g_{key} {isojob} -outname c_{key}"]
        elif kind == "drill":
            t += [f"drillcncjob drl -drilled_dias {arg} -drillz {drl['cut_z']} -travelz {tz} -endz {sz} "
                  f"-feedrate_z {drl['plunge']} -spindlespeed {drl['rpm']} -toolchange 0 "
                  + (f"-peck 1 -peck_depth {drl['peck']} -peck_retract 0.2 " if drl.get("peck") else "")
                  + f"-pp Default_no_M6 -outname c_{key}"]
        elif kind == "holes":
            t += [f'milldrills drl -milled_dias "{arg}" -tooldia {mil["dia"]} -outname g_{key}',
                  f"cncjob g_{key} {miljob} -outname c_{key}"]
        elif kind in ("flip", "note"):
            continue
        elif kind in ("mask_top", "mask_bot"):
            src = "fmask" if kind == "mask_top" else "bmask"
            t += [f"paint {src} {mskpaint} -outname g_{key}", f"cncjob g_{key} {mskjob} -outname c_{key}"]
        elif kind == "iso_bot":
            t += [f"isolate bot {isoargs} -outname g_{key}", f"cncjob g_{key} {isojob} -outname c_{key}"]
        elif kind == "cutout":
            t += [f"cutout edge -dia {mil['dia']} -margin 0 -gapsize {cut['tab_width']} -gaps {cut['tabs']} "
                  f"-type any -outname g_{key}", f"cncjob g_{key} {miljob} -outname c_{key}"]
        t.append(f"write_gcode c_{key} {out(key)}")
    return "\n".join(t) + "\n", eff, plan_w


def to_makera(src, dst, title, tool, tooldesc, cfg):
    """Rewrite FlatCAM G-code for the Z1 (framing as in makera_z1.py) and return its extents."""
    tz, sz = cfg["travel_z"], cfg["safe_z"]
    lines, rpm = [], None
    for raw in open(src):
        line = raw.strip()
        if not line or line.startswith("(") or line in ("G21", "G90", "G94"):
            continue
        line = re.sub(r"\bG0([0-3])\b", r"G\1", line)
        line = re.sub(r"^M0([35])\b", r"M\1", line)
        if not re.match(r"^(G[01]|M[35])\b", line):
            sys.exit(f"error: unexpected G-code line in {os.path.basename(src)}: {line}")
        m = re.match(r"M3 S([\d.]+)", line)
        if m:
            rpm = float(m.group(1))
        lines.append(line)
    if rpm is None or "M5" not in lines:
        sys.exit(f"error: {os.path.basename(src)} has no spindle start or stop")
    lines = lines[:len(lines) - lines[::-1].index("M5") - 1]     # drop FlatCAM's stop and parking moves
    body, x, y, z = [], [], [], []
    first_xy, feeds = False, []
    pos = {"X": None, "Y": None, "Z": None}
    for line in lines:
        if line.startswith("M"):
            continue                                              # spindle start is in the header
        if not first_xy:
            if re.fullmatch(r"G1 F[\d.]+", line):
                feeds = [line]                                    # keep the last feed set before the first move
            if not re.match(r"G0 X", line):
                continue                                          # nothing moves in Z before the first XY move
            first_xy = True
            # XY first, then down: safe height, travel height; then the feed FlatCAM had set
            body += [line, f"G0 Z{sz:.4f}", f"G0 Z{tz:.4f}"] + feeds
        else:
            body.append(line)
        for axis in "XYZ":
            m = re.search(axis + r"(-?[\d.]+)", line)
            if m:
                pos[axis] = float(m.group(1))
        if line.startswith("G1") and pos["Z"] is not None and pos["Z"] < 0 and pos["X"] is not None:
            x.append(pos["X"]); y.append(pos["Y"]); z.append(pos["Z"])
    if not first_xy or not x:
        sys.exit(f"error: {os.path.basename(src)} has no cutting moves")
    prog = z1.program([title, f"T{tool}-{tooldesc}",
                       f"Origin: X0 Y0 at {cfg['_origin']}, Z0 on the copper surface",
                       "Made by mill_pcb.py with FlatCAM Evo. Check in a viewer and air-cut before milling."],
                      tool, rpm, body + [f"G0 Z{sz:.4f}"])
    with open(dst, "w") as f:
        f.write("\n".join(prog) + "\n")
    return {"x": (min(x), max(x)), "y": (min(y), max(y)), "z": (min(z), max(z)), "lines": len(body)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project_dir")
    ap.add_argument("--config")
    ap.add_argument("--fab-dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--name")
    a = ap.parse_args()
    proj = os.path.abspath(a.project_dir)
    name = a.name or os.path.basename(proj)
    fab = a.fab_dir or os.path.join(proj, "outputs", "Fabrication")
    outd = a.out_dir or os.path.join(proj, "outputs", "Milling")
    cfgfile = a.config or os.path.join(proj, "milling.json")
    cfg = merge(DEFAULTS, json.load(open(cfgfile))) if os.path.exists(cfgfile) else DEFAULTS
    if a.config and not os.path.exists(a.config):
        sys.exit(f"error: {a.config} not found")
    if not os.path.exists(FLATCAM):
        sys.exit(f"error: FlatCAM Evo not found at {FLATCAM} (set FLATCAM)")
    files = {"top": one(fab, "*F_Cu.gtl"), "bot": one(fab, "*B_Cu.gbl"),
             "edge": one(fab, "*Edge_Cuts.gm1"), "drl": one(fab, "*.drl")}

    cfg = dict(cfg, _origin=("Anchor 1, with the blank on the dowel pins" if cfg["pins"].get("origin") == "anchor1"
                             else "front-left corner of the blank on the dowel pins") if cfg["pins"]["enabled"]
               else "front-left corner of the board outline")
    iso, drl, mil = cfg["isolation"], cfg["drill"], cfg["mill"]
    sizes = drill_sizes(files["drl"])
    drilled = [(d, n) for d, n in sizes if any(abs(d - x) < 1e-6 for x in drl["dias"])]
    milled = [(d, n) for d, n in sizes if (d, n) not in drilled]
    small = [d for d, n in milled if d < mil["dia"] + 0.05]
    if small:
        sys.exit(f"error: holes {small} mm are too small for the {mil['dia']} mm end mill; add them to drill.dias")

    # (key, kind, argument, title, tool number, tool description)
    isodesc = f"V-bit {iso['angle']} deg, {iso['tip_dia']} mm tip, cut {abs(iso['cut_z'])} mm deep"
    mildesc = f"{mil['dia']} mm end mill, cut {abs(mil['cut_z'])} mm deep in {mil['depth_per_pass']} mm steps"
    pin, msk = cfg["pins"], cfg["mask"]
    if msk["enabled"]:
        files["fmask"], files["bmask"] = one(fab, "*F_Mask.gts"), one(fab, "*B_Mask.gbs")
    # true board size from the outline Gerber (line centres), for the pin positions
    pts = [(float(a), float(b)) for a, b in re.findall(r"X(-?\d+)Y(-?\d+)D0[12]", open(files["edge"]).read())]
    lw = float(re.search(r"%ADD\d+C,([\d.]+)", open(files["edge"]).read()).group(1))
    bw = (max(p[0] for p in pts) - min(p[0] for p in pts)) / 1e6
    bh = (max(p[1] for p in pts) - min(p[1] for p in pts)) / 1e6
    place = {"to": (0.0, 0.0), "mirror": "-box edge"}
    lay = None
    if pin["enabled"]:
        st = cfg["stock"]
        lay = z1.stock_layout(st["width"], st["height"])
        off = st["board_offset"] or [(st["width"] - bw - lw) / 2, (st["height"] - bh - lw) / 2]
        if pin.get("origin", "blank") not in ("blank", "anchor1"):
            sys.exit('error: pins.origin must be "blank" or "anchor1"')
        shift = lay["origin"] if pin.get("origin") == "anchor1" else (0.0, 0.0)
        place = {"to": (off[0] + shift[0], off[1] + shift[1]),
                 "mirror": f"-origin {st['width'] / 2 + shift[0]:.4f},{st['height'] / 2 + shift[1]:.4f}"}
        reach = max(mil["dia"], 1.0) + 5.0       # keep cuts this far from the pin centres
        for px, py in lay["pins_stock"]:
            if (off[0] - reach < px < off[0] + bw + lw + reach) and (off[1] - reach < py < off[1] + bh + lw + reach):
                sys.exit(f"error: the board at {off} on the blank is too close to the dowel pin at {px}, {py}")
        if off[0] < 2 or off[1] < 2 or off[0] + bw + lw > st["width"] - 2 or off[1] + bh + lw > st["height"] - 2:
            sys.exit(f"error: the board ({bw:.1f} x {bh:.1f} mm) at {off} does not fit the {st['width']} x {st['height']} mm blank")
    flipnote = ("Lift the blank off the dowel pins, flip it left to right, and put it back on the same two pins."
                if pin["enabled"] else
                "Flip the board left to right and re-register so the bottom-left corner of the outline is X0 Y0.")
    ops = []
    ops.append(("top_isolation", "iso_top", None, "Top copper isolation", iso["tool"], isodesc))
    ops.append(("FLIP", "flip", None, flipnote + " Bottom side is now up.", None, None))
    ops.append(("bottom_isolation", "iso_bot", None, "Bottom copper isolation (bottom side up)", iso["tool"], isodesc))
    if msk["enabled"]:
        mskdesc = f"Solder mask removal tool, {msk['dia']} mm tip, pressed {abs(msk['cut_z'])} mm (spring-loaded)"
        ops.append(("MASK", "note", None, "Take the blank off. Apply the UV solder mask to both sides and cure it. "
                    "Keep the dowel holes clear. Refit the blank bottom side up.", None, None))
        ops.append(("bottom_mask_removal", "mask_bot", None, "Remove solder mask over the bottom pads (bottom side up)",
                    msk["tool"], mskdesc))
    ops.append(("FLIP", "flip", None, flipnote + " Top side is now up.", None, None))
    if msk["enabled"]:
        ops.append(("top_mask_removal", "mask_top", None, "Remove solder mask over the top pads", msk["tool"], mskdesc))
    taken = {iso["tool"], mil["tool"]} | ({msk["tool"]} if msk["enabled"] else set())
    drill_tools, nxt = [], drl["first_tool"]
    for _ in drilled:
        while nxt in taken:
            nxt += 1
        drill_tools.append(nxt); taken.add(nxt)
    for i, (d, n) in enumerate(drilled):
        ops.append((f"drill_{d:.2f}mm", "drill", f"{d}", f"Drill {n} holes {d:.2f} mm (from the top side)",
                    drill_tools[i], f"{d:.2f} mm drill, to {abs(drl['cut_z'])} mm deep"
                    + (f", pecking {drl['peck']} mm" if drl.get("peck") else "")))
    if milled:
        ops.append(("mill_holes", "holes", ",".join(str(d) for d, n in milled),
                    "Mill holes " + ", ".join(f"{n} x {d:.2f} mm" for d, n in milled) + " (from the top side)",
                    mil["tool"], mildesc))
    ops.append(("cutout", "cutout", None, "Board outline cutout (top side up)", mil["tool"], mildesc))

    os.makedirs(outd, exist_ok=True)
    for old in glob.glob(os.path.join(outd, "*.nc")):
        os.remove(old)
    with tempfile.TemporaryDirectory() as tmp:
        tcl, eff, plan_w = build_tcl(files, cfg, tmp, [(k, kind, arg) for k, kind, arg, *_ in ops], place)
        script = os.path.join(tmp, "job.tcl")
        open(script, "w").write(tcl)
        r = subprocess.run([FLATCAM, "--headless", f"--shellfile={script}"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        log = r.stdout + r.stderr
        open(os.path.join(outd, "flatcam.log"), "w").write(log)
        if r.returncode != 0:
            sys.exit(f"error: FlatCAM exited {r.returncode}; see {os.path.join(outd, 'flatcam.log')}")
        m = re.search(r"BOUNDS_EDGE \{?([-\d.e ]+)", log)
        eb = [float(v) for v in m.group(1).split()] if m else None
        m = re.search(r"BOUNDS_ORIG \{?([-\d.e ]+)", log)
        ob = [float(v) for v in m.group(1).split()] if m else [0, 0, 0, 0]
        gaps = []
        gp = os.path.join(tmp, "gaps.csv")
        if os.path.exists(gp):
            gaps = [row for row in csv.DictReader(open(gp)) if row.get("status", "").upper().startswith("FAIL")]
        results, step = [], 0
        for key, kind, arg, title, tool, desc in ops:
            if kind in ("flip", "note"):
                results.append(title)
                continue
            step += 1
            src = os.path.join(tmp, key + ".nc")
            if not os.path.exists(src):
                sys.exit(f"error: FlatCAM did not write {key}; see flatcam.log")
            fn = f"{step} - {key}.nc"
            ext = to_makera(src, os.path.join(outd, fn), f"{name} - {title}", tool, desc, cfg)
            results.append((fn, title, tool, desc, ext))

    warn = []
    over = (eff - plan_w) / 2
    if over > 1e-6:
        warn.append(f"Over-milling: the paths are planned for a {plan_w:.3f} mm cut but the bit cuts {eff:.3f} mm wide. "
                    f"Every copper edge loses {over:.3f} mm; a track loses {2 * over:.3f} mm of width.")
    if "Incomplete isolation" in log:
        warn.append(f"FlatCAM reported incomplete isolation: the {plan_w:.3f} mm planned cut does not fit through every gap.")
    if gaps:
        warn.append(f"{len(gaps)} copper gaps are narrower than the {plan_w:.3f} mm planned isolation cut (listed below). "
                    "Copper stays bridged there unless a finer bit is used or the layout is opened up.")
    w, h = (eb[2] - eb[0], eb[3] - eb[1]) if eb else (None, None)
    for res in results:
        if isinstance(res, tuple) and eb:
            fn, _, _, _, e = res
            if e["x"][0] < eb[0] - 2 or e["y"][0] < eb[1] - 2 or e["x"][1] > eb[2] + 2 or e["y"][1] > eb[3] + 2:
                warn.append(f"{fn}: cuts extend more than 2 mm outside the board outline.")
    sheet = [f"{name} - Milling setup", "=" * (len(name) + 16), "",
             f"Machine: {cfg['machine']}. Made with FlatCAM Evo by mill_pcb.py.",
             "NOT SIMULATED. Check each file in a G-code viewer and air-cut before milling.", "",
             "Work origin: X0 Y0 at the front-left corner of the board outline, Z0 on the copper surface."]
    if eb:
        sheet.append(f"Board outline: {w:.2f} x {h:.2f} mm (measured over the outline line width). "
                     f"Stock must be larger by at least the end mill diameter on every side.")
    if pin["enabled"]:
        st = cfg["stock"]
        (p1, p2), (m1, m2), og = lay["pins_stock"], lay["pins_anchor"], lay["origin"]
        if pin.get("origin") == "anchor1":
            sheet[6] = ("Work origin: X0 Y0 at ANCHOR 1 (the inner corner of the L-bracket), Z0 on the copper surface. "
                        f"The blank's front-left corner is at X {og[0]:g} Y {og[1]:g}.")
        else:
            sheet[6] = ("Work origin: X0 Y0 at the front-left corner of the BLANK as it sits on the bed dowel pins "
                        f"(Anchor 1 + X {og[0]:g} Y {og[1]:g}), Z0 on the copper surface.")
        sheet += [f"Blank: {st['width']:g} x {st['height']:g} mm, prepared with stock_prep.py (dowel holes at "
                  f"X {p1[0]:g} Y {p1[1]:g} and X {p2[0]:g} Y {p2[1]:g}), on the bed dowel holes at "
                  f"X {m1[0]:g} Y {m1[1]:g} and X {m2[0]:g} Y {m2[1]:g} from Anchor 1.",
                  f"Board outline, from the origin: X {eb[0]:.2f} to {eb[2]:.2f}, Y {eb[1]:.2f} to {eb[3]:.2f}.",
                  "Set the origin once and keep it for every file, on both sides: the pins put the flipped blank "
                  "back in the same place. Do not re-probe after flipping."]
    sheet += ["Run auto-levelling before the isolation files: the cut is only "
              f"{abs(iso['cut_z'])} mm deep.", "", "Run in this order:", ""]
    for res in results:
        if isinstance(res, str):
            sheet += [f"  >> {res}", ""]
            continue
        fn, title, tool, desc, e = res
        sheet += [f"  {fn}", f"     {title}", f"     Tool T{tool}: {desc}",
                  f"     Cuts X {e['x'][0]:.2f} to {e['x'][1]:.2f}, Y {e['y'][0]:.2f} to {e['y'][1]:.2f}, "
                  f"deepest Z {e['z'][0]:.2f} mm, {e['lines']} lines", ""]
    sheet += ["Each file starts with M6 T<n>: the machine asks for that tool and measures its length. "
              "It ends with G28: the spindle lifts and parks at the clearance position. "
              "The layout matches Makera CAM's own files, which Makera Studio needs to show a preview.",
              ("Cutout: NO TABS. The board comes free on the last pass; it must be held down (tape) under its whole area."
               if str(cfg['cutout']['tabs']).lower() == "none" else
               f"Cutout tabs: {cfg['cutout']['tabs']} (FlatCAM gap pattern), {cfg['cutout']['tab_width']} mm wide, full board thickness."),
              "", "Settings used:", json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, indent=2), ""]
    if warn:
        sheet += ["WARNINGS", "--------"] + [f"- {x}" for x in warn] + [""]
    if gaps:
        sheet += ["Gaps narrower than the isolation cut, seen from the top side. 'Board' is measured from the",
                  "bottom-left corner of the outline; 'KiCad' is the position in the board editor:", ""]
        sheet += [f"  {g.get('layer','')}: board X {float(g['x']):.2f} Y {float(g['y']):.2f}   "
                  f"KiCad X {float(g['x']) + ob[0]:.2f} Y {-(float(g['y']) + ob[1]):.2f}   gap {float(g['value']):.3f} mm"
                  for g in gaps[:200]]
    open(os.path.join(outd, f"{name} - Milling Setup.txt"), "w").write("\n".join(sheet) + "\n")
    print(json.dumps({"out_dir": outd, "files": [r[0] for r in results if isinstance(r, tuple)], "isolation_cut_mm": round(eff, 4), "planned_cut_mm": round(plan_w, 4),
                      "overmill_per_edge_mm": round(max(over, 0), 4),
                      "narrow_gaps": len(gaps), "warnings": warn}, indent=2))


if __name__ == "__main__":
    main()

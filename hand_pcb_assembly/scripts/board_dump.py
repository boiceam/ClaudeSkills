"""Run with KiCad's bundled Python. Reads a .kicad_pcb (never writes it) and prints JSON:
board edge box, filled outline polygons, and one entry per footprint."""
import json, sys
import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
mm = pcbnew.ToMM
bb = b.GetBoardEdgesBoundingBox()
out = {"edge_box": [mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())],
       "outline": [], "footprints": {}}

poly = pcbnew.SHAPE_POLY_SET()
try:
    try:
        ok = b.GetBoardPolygonOutlines(poly, True)      # KiCad 10: second argument is required
    except TypeError:
        ok = b.GetBoardPolygonOutlines(poly)            # older KiCad
    if ok:
        for i in range(poly.OutlineCount()):
            def chain(c):
                return [[mm(c.CPoint(k).x), mm(c.CPoint(k).y)] for k in range(c.PointCount())]
            out["outline"].append({"outer": chain(poly.Outline(i)),
                                   "holes": [chain(poly.Hole(i, h)) for h in range(poly.HoleCount(i))]})
except Exception as e:                      # outline not closed, API difference: page falls back to no fill
    out["outline_error"] = str(e)

for f in b.GetFootprints():
    back = f.IsFlipped()
    box = None
    for p in f.Pads():
        r = p.GetBoundingBox()
        if box is None:
            box = r
        else:
            box.Merge(r)
    src = "pads"
    try:
        cy = f.GetCourtyard(pcbnew.B_CrtYd if back else pcbnew.F_CrtYd)
        if cy.OutlineCount():
            box, src = cy.BBox(), "courtyard"
    except Exception:
        pass
    if box is None:
        box, src = f.GetBoundingBox(False), "body"
    def flag(name):
        fn = getattr(f, name, None)
        try:
            return bool(fn()) if fn else False
        except Exception:
            return False
    out["footprints"][f.GetReference()] = {
        "side": "B" if back else "F", "value": f.GetValue(),
        "box": [mm(box.GetLeft()), mm(box.GetTop()), mm(box.GetRight()), mm(box.GetBottom())], "box_from": src,
        "pads": len(f.Pads()), "dnp": flag("IsDNP"), "no_bom": flag("IsExcludeFromBOM") or flag("IsExcludedFromBOM")}
json.dump(out, sys.stdout)

"""Run under KiCad's Python: print the board's copper keep-out areas as JSON.

    <KiCad python> kicad_keepouts.py BOARD.kicad_pcb

A keep-out here is a rule area (on the board or inside a footprint) that forbids copper fill.
Output: [{"name", "ref", "top": bool, "bottom": bool, "pts": [[x, y], ...]}] in KiCad millimetres.
Read-only: the board is never saved.
"""
import json, sys
import pcbnew

b = pcbnew.LoadBoard(sys.argv[1])
zones = [(None, z) for z in b.Zones()]
for f in b.GetFootprints():
    zones += [(f.GetReference(), z) for z in f.Zones()]
out = []
for ref, z in zones:
    if not z.GetIsRuleArea() or not z.GetDoNotAllowZoneFills():
        continue
    ls = z.GetLayerSet()
    top, bot = ls.Contains(pcbnew.F_Cu), ls.Contains(pcbnew.B_Cu)
    if not (top or bot):
        continue
    o = z.Outline()
    for k in range(o.OutlineCount()):
        ol = o.Outline(k)
        pts = [[pcbnew.ToMM(ol.CPoint(i).x), pcbnew.ToMM(ol.CPoint(i).y)] for i in range(ol.PointCount())]
        out.append({"name": z.GetZoneName(), "ref": ref, "top": top, "bottom": bot, "pts": pts})
print("KEEPOUTS " + json.dumps(out))

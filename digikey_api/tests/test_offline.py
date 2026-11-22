"""Offline test: run with `python3 tests/test_offline.py` (no Digi-Key account needed)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fake_digikey  # noqa: E402

DK = os.path.join(HERE, "..", "scripts", "dk.py")
srv = fake_digikey.start()
tmp = tempfile.mkdtemp()
env = dict(os.environ, DIGIKEY_API_BASE="http://127.0.0.1:%d" % srv.server_port, DIGIKEY_CLIENT_ID="id",
           DIGIKEY_CLIENT_SECRET="s3cr3t-value", DIGIKEY_CONFIG_DIR=os.path.join(tmp, "cfg"),
           DIGIKEY_REDIRECT_URI="https://localhost")


def run(*args, ok=True):
    r = subprocess.run([sys.executable, DK] + list(args), env=env, capture_output=True, text=True)
    assert (r.returncode == 0) == ok, (args, r.stdout, r.stderr)
    return json.loads(r.stdout)


assert run("auth-check")["product_search_ok"]
assert run("part", "RC0603FR-0710KL", "--qty", "250")["part"]["recommended"]["variation"]["unit_price"] == 0.02

bom = os.path.join(tmp, "bom.csv")
open(bom, "w").write("Designator,Qty,Manufacturer Part Number,Description\n"
                      "R1,2,RC0603FR-0710KL,10k\nR2,3,RC0603FR-0710KL,10k\n"
                      "U1,1,LM358DR,opamp\nU2,1,NOPE123,missing\n")
run("bom-match", bom, ok=False)  # the number of boards is mandatory
# quantity rules off: need only
m = run("bom-match", bom, "--boards", "10", "--overage", "0", "--cheap-min", "0")
assert {l["bom_mpn"]: l for l in m["lines"]}["RC0603FR-0710KL"]["order_qty"] == 50
# 10% overage (default) applies when it adds no more than the per-line limit...
m = run("bom-match", bom, "--boards", "10", "--cheap-min", "0")
assert {l["bom_mpn"]: l for l in m["lines"]}["RC0603FR-0710KL"]["order_qty"] == 55
# ...and is skipped when it would add more
m = run("bom-match", bom, "--boards", "10", "--cheap-min", "0", "--overage-max-cost", "0.1")
ln = {l["bom_mpn"]: l for l in m["lines"]}["RC0603FR-0710KL"]
assert ln["order_qty"] == 50 and "overage skipped" in ln["qty_notes"][0]
# defaults: overage, then cheap cut-tape parts are raised to 100
m = run("bom-match", bom, "--boards", "10")
by = {l["bom_mpn"]: l for l in m["lines"]}
assert len(m["lines"]) == 3, "duplicate MPN lines must merge"
r10k = by["RC0603FR-0710KL"]
assert r10k["order_need"] == 50 and r10k["order_qty"] == 100 and r10k["status"] == "matched"
assert r10k["unit_price"] == 0.02 and len(r10k["qty_notes"]) == 2
assert by["LM358DR"]["status"] == "needs_review" and by["NOPE123"]["status"] == "not_found"

c = run("list-csv", m["match_file"])
assert c["lines_written"] == 1 and len(c["lines_skipped"]) == 2
assert "311-10.0KHRCT-ND" in open(c["csv"]).read()

assert run("mylist", m["match_file"], "--name", "t1")["dry_run"]
run("mylist", m["match_file"], "--name", "t1", "--confirm", ok=False)  # not logged in yet
run("login", "--url", "https://localhost/?code=abc&state=dk")
r = run("mylist", m["match_file"], "--name", "t1", "--confirm")
assert r["created"] and fake_digikey.STATE["lists"]["L1"][0]["Quantities"][0]["Quantity"] == 100
assert "s3cr3t-value" not in json.dumps(run("config"))
print("all offline tests passed")

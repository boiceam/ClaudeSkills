"""Tiny fake of the Digi-Key endpoints dk.py uses, for offline tests."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PARTS = {
    "RC0603FR-0710KL": ("311-10.0KHRCT-ND", "Cut Tape (CT)", 5000, 1, [(1, 0.10), (100, 0.02), (1000, 0.008)]),
    "LM358DR": ("296-1395-1-ND", "Cut Tape (CT)", 0, 1, [(1, 0.5)]),          # out of stock
}
STATE = {"lists": {}, "calls": []}


def product(mpn):
    dk, pkg, stock, moq, br = PARTS[mpn]
    return {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": "Acme"},
            "Description": {"ProductDescription": "desc " + mpn}, "ProductStatus": {"Status": "Active"},
            "QuantityAvailable": stock, "DatasheetUrl": "http://ds",
            "ProductVariations": [{"DigiKeyProductNumber": dk, "PackageType": {"Name": pkg},
                                   "QuantityAvailableforPackageType": stock, "MinimumOrderQuantity": moq,
                                   "StandardPricing": [{"BreakQuantity": b, "UnitPrice": u} for b, u in br]}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode()

    def do_GET(self):
        STATE["calls"].append(("GET", self.path))
        if "/productdetails" in self.path:
            mpn = self.path.split("/search/")[1].split("/")[0]
            return self.reply(200, {"Product": product(mpn)}) if mpn in PARTS else self.reply(404, {})
        if "/lists/validate/" in self.path:
            return self.reply(200, True)
        self.reply(404, {})

    def do_POST(self):
        b = self.body()
        STATE["calls"].append(("POST", self.path))
        if self.path == "/v1/oauth2/token":
            if "client_credentials" in b:
                return self.reply(200, {"access_token": "T2", "expires_in": 599, "token_type": "Bearer"})
            return self.reply(200, {"access_token": "T3", "refresh_token": "R", "expires_in": 1799})
        if self.headers.get("Authorization") not in ("Bearer T2", "Bearer T3"):
            return self.reply(401, {})
        if self.path == "/products/v4/search/keyword":
            kw = json.loads(b)["Keywords"]
            hits = [product(m) for m in PARTS if kw.lower() in m.lower()]
            return self.reply(200, {"Products": hits, "ProductsCount": len(hits)})
        if self.path == "/mylists/v1/lists":
            STATE["lists"]["L1"] = []
            return self.reply(200, "L1")
        if self.path == "/mylists/v1/lists/L1/parts":
            STATE["lists"]["L1"] = json.loads(b)
            return self.reply(200, {})
        self.reply(404, {})


def start():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

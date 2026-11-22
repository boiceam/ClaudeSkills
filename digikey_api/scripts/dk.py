#!/usr/bin/env python3
"""Digi-Key API v4 helper: part lookup, BOM matching, order-list CSV, MyList creation.

Standard library only (Python 3.8+). Every command prints exactly one JSON object on stdout;
progress goes to stderr. Exit code: 0 ok, 1 error.
Secrets are never printed. Config and tokens live in ~/.config/digikey_api/ (mode 0600).
"""
import argparse
import base64
import csv
import getpass
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

CONF_DIR = os.environ.get("DIGIKEY_CONFIG_DIR", os.path.expanduser("~/.config/digikey_api"))
CONF_FILE = os.path.join(CONF_DIR, "config.json")
TOKEN_FILE = os.path.join(CONF_DIR, "tokens.json")
HOST = {"prod": "https://api.digikey.com", "sandbox": "https://sandbox-api.digikey.com"}
MIN_INTERVAL = 0.55  # Product Information limit is 120 calls/min


# ----------------------------------------------------------------------------- helpers
def emit(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    sys.exit(code)


def fail(msg, hint=None, **extra):
    d = {"error": msg}
    if hint:
        d["hint"] = hint
    d.update(extra)
    emit(d, 1)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_private(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


def load_cfg():
    c = read_json(CONF_FILE)
    env = {
        "client_id": "DIGIKEY_CLIENT_ID", "client_secret": "DIGIKEY_CLIENT_SECRET",
        "redirect_uri": "DIGIKEY_REDIRECT_URI", "account_id": "DIGIKEY_ACCOUNT_ID",
        "site": "DIGIKEY_SITE", "language": "DIGIKEY_LANGUAGE", "currency": "DIGIKEY_CURRENCY",
        "env": "DIGIKEY_ENV", "api_base": "DIGIKEY_API_BASE", "authorize_url": "DIGIKEY_AUTH_URL",
    }
    for k, e in env.items():
        if os.environ.get(e):
            c[k] = os.environ[e]
    c.setdefault("env", "prod")
    c.setdefault("site", "US")
    c.setdefault("language", "en")
    c.setdefault("currency", "USD")
    c.setdefault("authorize_url", "https://api.digikey.com/v1/oauth2/authorize")
    c.setdefault("api_base", HOST.get(c["env"], HOST["prod"]))
    return c


def need_creds(cfg):
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        fail("No Digi-Key client ID/secret configured.",
             "Run `dk.py config --client-id <id>` then `dk.py config --prompt-secret`, "
             "or set DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET.")


_last_call = [0.0]


def _q(v):
    """Quote a value for a curl config file."""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def curl_once(method, url, headers, data):
    """One request via curl (uses the system trust store, so corporate TLS inspection works).
    The config goes in on stdin so secrets never appear in the process list."""
    cfg = ["url = " + _q(url), "request = " + _q(method), "include", "silent", "show-error",
           "max-time = 30"]
    for k, v in headers.items():
        cfg.append("header = " + _q("%s: %s" % (k, v)))
    if data is not None:
        cfg.append("data-raw = " + _q(data))
    try:
        r = subprocess.run(["curl", "-K", "-"], input="\n".join(cfg), capture_output=True, text=True)
    except FileNotFoundError:
        fail("curl not found.", "Install curl; dk.py uses it for HTTPS.")
    if r.returncode != 0:
        fail("Network error talking to Digi-Key: %s" % r.stderr.strip(), "Check connectivity / DIGIKEY_API_BASE.")
    out = r.stdout
    while out.startswith("HTTP/"):  # skip 100-continue / proxy CONNECT blocks, keep the last
        head, sep, rest = out.partition("\r\n\r\n")
        if not sep:
            head, sep, rest = out.partition("\n\n")
        if rest.startswith("HTTP/"):
            out = rest
            continue
        lines = head.splitlines()
        status = int(lines[0].split()[1])
        rh = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            rh[k.strip()] = v.strip()
        return status, rest, rh
    fail("Unparseable response from curl.")


def http(method, url, headers=None, body=None, form=None, retries=3):
    """Return (status, parsed_json_or_text, response_headers). Throttled; retries 429/5xx."""
    data = None
    h = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body)
        h["Content-Type"] = "application/json"
    h.setdefault("Accept", "application/json")
    for attempt in range(retries + 1):
        wait = MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        status, txt, rh = curl_once(method, url, h, data)
        rh = {k.title(): v for k, v in rh.items()}
        if status in (429, 500, 502, 503, 504) and attempt < retries:
            delay = float(rh.get("Retry-After", 2 ** (attempt + 1)))
            log("HTTP %s, retrying in %.0fs" % (status, delay))
            time.sleep(min(delay, 60))
            continue
        try:
            return status, json.loads(txt), rh
        except ValueError:
            return status, txt, rh
    return status, txt, rh


# ----------------------------------------------------------------------------- auth
def token_url(cfg):
    return cfg["api_base"] + "/v1/oauth2/token"


def two_legged_token(cfg):
    need_creds(cfg)
    t = read_json(TOKEN_FILE).get("app", {})
    if t.get("access_token") and t.get("expires_at", 0) > time.time() + 30 and t.get("client_id") == cfg["client_id"]:
        return t["access_token"]
    st, js, _ = http("POST", token_url(cfg), form={
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "grant_type": "client_credentials"})
    if st != 200 or not isinstance(js, dict) or "access_token" not in js:
        fail("Digi-Key rejected the client credentials (HTTP %s)." % st,
             "Check client ID/secret, that the app is on the right environment (prod vs sandbox) "
             "and has the Product Information API enabled.", response=js)
    allt = read_json(TOKEN_FILE)
    allt["app"] = {"access_token": js["access_token"], "client_id": cfg["client_id"],
                   "expires_at": time.time() + int(js.get("expires_in", 600))}
    write_private(TOKEN_FILE, allt)
    return js["access_token"]


def user_token(cfg):
    """3-legged token, refreshing when needed."""
    need_creds(cfg)
    allt = read_json(TOKEN_FILE)
    u = allt.get("user", {})
    if not u.get("access_token"):
        fail("Not logged in for account-level calls.", "Run `dk.py login` (needed for MyLists).")
    if u.get("expires_at", 0) > time.time() + 60:
        return u["access_token"]
    st, js, _ = http("POST", token_url(cfg), form={
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "refresh_token": u.get("refresh_token", ""), "grant_type": "refresh_token"})
    if st != 200 or "access_token" not in js:
        fail("Refresh failed (HTTP %s). The login has expired." % st, "Run `dk.py login` again.", response=js)
    save_user_token(js)
    return js["access_token"]


def save_user_token(js):
    allt = read_json(TOKEN_FILE)
    allt["user"] = {"access_token": js["access_token"], "refresh_token": js.get("refresh_token"),
                    "expires_at": time.time() + int(js.get("expires_in", 1800))}
    write_private(TOKEN_FILE, allt)  # refresh tokens rotate: always store the newest


def api_headers(cfg, token):
    h = {"Authorization": "Bearer " + token, "X-DIGIKEY-Client-Id": cfg["client_id"],
         "X-DIGIKEY-Locale-Site": cfg["site"], "X-DIGIKEY-Locale-Language": cfg["language"],
         "X-DIGIKEY-Locale-Currency": cfg["currency"]}
    if cfg.get("account_id"):
        h["X-DIGIKEY-Account-Id"] = str(cfg["account_id"])
    return h


def call(cfg, method, path, token, body=None, params=None):
    url = cfg["api_base"] + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    st, js, rh = http(method, url, api_headers(cfg, token), body=body)
    rem = rh.get("X-Ratelimit-Remaining")
    if rem is not None and str(rem).isdigit() and int(rem) < 50:
        log("warning: only %s Digi-Key API calls left today" % rem)
    return st, js


# ----------------------------------------------------------------------------- parts
def summarize_product(p, qty=None):
    """Flatten a v4 Product into the fields we care about, with the variations."""
    status = p.get("ProductStatus")
    status = status.get("Status") if isinstance(status, dict) else status
    vars_ = []
    for v in p.get("ProductVariations") or []:
        pkg = v.get("PackageType")
        pkg = pkg.get("Name") if isinstance(pkg, dict) else pkg
        breaks = [(b.get("BreakQuantity"), b.get("UnitPrice")) for b in v.get("StandardPricing") or []]
        vars_.append({"dk_pn": v.get("DigiKeyProductNumber"), "package": pkg,
                      "stock": v.get("QuantityAvailableforPackageType"),
                      "moq": v.get("MinimumOrderQuantity"), "std_pack": v.get("StandardPackage"),
                      "breaks": breaks, "marketplace": v.get("MarketPlace")})
    desc = p.get("Description") or {}
    return {"mpn": p.get("ManufacturerProductNumber"),
            "manufacturer": (p.get("Manufacturer") or {}).get("Name"),
            "description": desc.get("ProductDescription") if isinstance(desc, dict) else desc,
            "status": status, "discontinued": p.get("Discontinued"), "eol": p.get("EndOfLife"),
            "stock_total": p.get("QuantityAvailable"), "datasheet": p.get("DatasheetUrl"),
            "url": p.get("ProductUrl"), "variations": vars_}


def unit_price_at(var, qty):
    price = None
    for bq, up in sorted(var["breaks"], key=lambda x: x[0] or 0):
        if bq is not None and bq <= qty and up is not None:
            price = up
    if price is None and var["breaks"]:
        price = sorted(var["breaks"], key=lambda x: x[0] or 0)[0][1]
    return price


def pick_variation(prod, qty):
    """Cheapest in-stock, non-marketplace variation that can satisfy qty and MOQ.
    Reels only win when the extended price is lower. Digi-Reel is never picked: its price
    breaks match cut tape (sometimes a cent lower) but Digi-Key adds a reeling fee per line that
    the API pricing does not show. Returns (variation, order_qty, note)."""
    best = None
    for v in prod["variations"]:
        if v.get("marketplace"):
            continue
        if "digi-reel" in (v.get("package") or "").lower():
            continue
        moq = v.get("moq") or 1
        oq = max(qty, moq)
        stock = v.get("stock")
        if stock is not None and stock < oq:
            continue
        up = unit_price_at(v, oq)
        if up is None:
            continue
        ext = up * oq
        key = (ext, "reel" in (v.get("package") or "").lower())
        if best is None or key < best[0]:
            best = (key, v, oq, up)
    if not best:
        return None, qty, "no in-stock variation covers this quantity"
    _, v, oq, up = best
    note = "MOQ %d > need %d" % (oq, qty) if oq > qty else ""
    return dict(v, unit_price=up, ext_price=round(up * oq, 4)), oq, note


def kw_search(cfg, token, text, limit=10):
    st, js = call(cfg, "POST", "/products/v4/search/keyword", token,
                  body={"Keywords": text, "Limit": limit, "Offset": 0})
    if st != 200:
        fail("Keyword search failed (HTTP %s)." % st, hint_for(st), response=js)
    return js


def hint_for(st):
    return {401: "Token rejected: check client ID/secret and environment.",
            403: "Forbidden: the app may not have this API product enabled on developer.digikey.com.",
            404: "Not found (or wrong path/host).",
            429: "Rate limited (120/min, 1000/day for Product Information)."}.get(st)


def lookup(cfg, token, query):
    """Resolve a part-number string to a product summary dict or (None, candidates)."""
    st, js = call(cfg, "GET", "/products/v4/search/%s/productdetails" % urllib.parse.quote(query, safe=""), token)
    if st == 200 and isinstance(js, dict) and js.get("Product"):
        return summarize_product(js["Product"]), []
    res = kw_search(cfg, token, query)
    prods = (res.get("ExactMatches") or []) + (res.get("Products") or [])
    seen, uniq = set(), []
    for p in prods:
        k = p.get("ManufacturerProductNumber")
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    exact = [p for p in uniq if (p.get("ManufacturerProductNumber") or "").lower() == query.lower()]
    if len(exact) == 1 or (len(uniq) == 1):
        return summarize_product((exact or uniq)[0]), []
    return None, [summarize_product(p) for p in uniq[:5]]


# ----------------------------------------------------------------------------- BOM
COLS = {
    "qty": ["qty", "quantity", "qnty", "count", "qty per board"],
    "mpn": ["mpn", "manufacturer part number", "manufacturer part no", "mfr part number", "mfg part number",
            "manufacturer_part_number", "mfr pn", "mfg pn", "part number", "partnumber", "pn"],
    "dkpn": ["digikey part number", "digi-key part number", "digikey pn", "dk pn", "digikey", "digi-key",
             "digi-key part #", "dkpn"],
    "ref": ["designator", "reference", "references", "refdes", "ref des", "reference designator"],
    "mfr": ["manufacturer", "mfr", "mfg", "manufacturer name"],
    "desc": ["description", "value", "comment"],
}


def read_bom(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(f, dialect))
    # header = first row that has a qty-like and a part-like column
    hdr_i = None
    for i, r in enumerate(rows[:20]):
        names = [c.strip().lower() for c in r]
        if any(n in COLS["qty"] for n in names) and any(n in COLS["mpn"] + COLS["dkpn"] for n in names):
            hdr_i = i
            break
    if hdr_i is None:
        fail("Could not find a header row with a quantity and part-number column in %s." % path,
             "Pass --map qty=Col mpn=Col dkpn=Col ref=Col, or fix the headers.")
    return rows[hdr_i], rows[hdr_i + 1:]


def map_columns(header, overrides):
    names = [c.strip().lower() for c in header]
    m = {}
    for key, aliases in COLS.items():
        for a in aliases:  # earlier alias = higher priority
            if a in names:
                m[key] = names.index(a)
                break
    for ov in overrides or []:
        k, _, v = ov.partition("=")
        if v.strip().lower() in names:
            m[k] = names.index(v.strip().lower())
        else:
            fail("--map column %r not in header %s" % (v, header))
    return m


def bom_lines(path, overrides):
    header, rows = read_bom(path)
    m = map_columns(header, overrides)
    if "qty" not in m or ("mpn" not in m and "dkpn" not in m):
        fail("Missing required columns. Detected: %s" % {k: header[i] for k, i in m.items()},
             "Use --map to name them.")
    merged, order = {}, []
    for r in rows:
        if not any(c.strip() for c in r):
            continue
        g = lambda k: r[m[k]].strip() if k in m and m[k] < len(r) else ""
        mpn, dk = g("mpn"), g("dkpn")
        if not (mpn or dk):
            continue
        try:
            q = int(float(g("qty") or 0))
        except ValueError:
            q = 0
        key = (dk or mpn).lower()
        if key in merged:
            merged[key]["qty"] += q
            if g("ref"):
                merged[key]["refs"] = (merged[key]["refs"] + "," + g("ref")).strip(",")
        else:
            merged[key] = {"qty": q, "mpn": mpn, "dkpn": dk, "refs": g("ref"), "mfr": g("mfr"), "desc": g("desc")}
            order.append(key)
    return [merged[k] for k in order]


def order_qty(per_board, boards, overage_pct, extra):
    import math
    return int(math.ceil(round(per_board * boards * (1 + overage_pct / 100.0), 6))) + extra


# ----------------------------------------------------------------------------- commands
def cmd_config(a):
    c = read_json(CONF_FILE)
    for k in ("client_id", "redirect_uri", "account_id", "site", "language", "currency", "env"):
        v = getattr(a, k)
        if v:
            c[k] = v
    if a.prompt_secret:
        c["client_secret"] = getpass.getpass("Digi-Key client secret (input hidden): ").strip()
    if a.clear:
        for p in (CONF_FILE, TOKEN_FILE):
            if os.path.exists(p):
                os.remove(p)
        emit({"cleared": True})
    if any([a.client_id, a.redirect_uri, a.account_id, a.site, a.language, a.currency, a.env, a.prompt_secret]):
        write_private(CONF_FILE, c)
    cfg = load_cfg()
    emit({"config_file": CONF_FILE, "client_id_set": bool(cfg.get("client_id")),
          "client_secret_set": bool(cfg.get("client_secret")), "env": cfg["env"], "api_base": cfg["api_base"],
          "site": cfg["site"], "language": cfg["language"], "currency": cfg["currency"],
          "redirect_uri": cfg.get("redirect_uri"), "account_id": cfg.get("account_id"),
          "user_login_present": bool(read_json(TOKEN_FILE).get("user", {}).get("access_token"))})


def cmd_auth_check(a):
    cfg = load_cfg()
    tok = two_legged_token(cfg)
    st, js = call(cfg, "POST", "/products/v4/search/keyword", tok, body={"Keywords": "LM358", "Limit": 1})
    ok = st == 200
    emit({"token_ok": True, "product_search_ok": ok, "http_status": st, "env": cfg["env"],
          "sample_hit": (js.get("Products") or [{}])[0].get("ManufacturerProductNumber") if ok else None,
          "response": None if ok else js}, 0 if ok else 1)


def cmd_search(a):
    cfg = load_cfg()
    tok = two_legged_token(cfg)
    js = kw_search(cfg, tok, a.query, a.limit)
    prods = [summarize_product(p) for p in (js.get("Products") or [])]
    if a.in_stock:
        prods = [p for p in prods if (p["stock_total"] or 0) > 0]
    out = []
    for p in prods:
        v = None
        if a.qty:
            v, oq, note = pick_variation(p, a.qty)
        else:
            v = p["variations"][0] if p["variations"] else None
        row = {k: p[k] for k in ("mpn", "manufacturer", "description", "status", "stock_total", "datasheet")}
        row["best_variation"] = v
        out.append(row)
    emit({"query": a.query, "total_matches": js.get("ProductsCount"), "returned": len(out), "products": out})


def cmd_part(a):
    cfg = load_cfg()
    tok = two_legged_token(cfg)
    p, cands = lookup(cfg, tok, a.part_number)
    if not p:
        emit({"query": a.part_number, "found": False, "candidates": cands}, 1)
    if a.qty:
        v, oq, note = pick_variation(p, a.qty)
        p["recommended"] = {"variation": v, "order_qty": oq, "note": note}
    emit({"found": True, "part": p})


def cmd_bom_match(a):
    cfg = load_cfg()
    tok = two_legged_token(cfg)
    lines = bom_lines(a.bom, a.map)
    results, summary = [], {"matched": 0, "needs_review": 0, "not_found": 0}
    total = 0.0
    for i, ln in enumerate(lines, 1):
        need = order_qty(ln["qty"], a.boards, a.overage, a.extra)
        key = ln["dkpn"] or ln["mpn"]
        log("[%d/%d] %s" % (i, len(lines), key))
        p, cands = lookup(cfg, tok, key)
        if not p and ln["dkpn"] and ln["mpn"]:
            p, cands = lookup(cfg, tok, ln["mpn"])
        r = {"line": i, "bom_qty": ln["qty"], "order_need": need, "bom_mpn": ln["mpn"], "bom_dkpn": ln["dkpn"],
             "refs": ln["refs"]}
        flags = []
        if not p:
            r.update(status="not_found" if not cands else "ambiguous", candidates=cands)
            summary["not_found" if not cands else "needs_review"] += 1
            results.append(r)
            continue
        v, oq, note = pick_variation(p, need)
        r.update(mpn=p["mpn"], manufacturer=p["manufacturer"], description=p["description"],
                 part_status=p["status"], datasheet=p["datasheet"])
        if ln["mpn"] and p["mpn"] and ln["mpn"].lower() != p["mpn"].lower():
            flags.append("MPN differs from BOM (%s -> %s)" % (ln["mpn"], p["mpn"]))
        if p.get("discontinued") or p.get("eol") or (p["status"] and p["status"].lower() != "active"):
            flags.append("lifecycle: %s" % p["status"])
        if not v:
            flags.append(note)
            r.update(status="needs_review")
            summary["needs_review"] += 1
        else:
            if note:
                flags.append(note)
            r.update(dk_pn=v["dk_pn"], package=v["package"], order_qty=oq, unit_price=v["unit_price"],
                     ext_price=v["ext_price"], stock=v["stock"])
            total += v["ext_price"]
            r["status"] = "needs_review" if any(f.startswith(("MPN differs", "lifecycle")) for f in flags) else "matched"
            summary[r["status"]] += 1
        r["flags"] = flags
        results.append(r)
    out = {"bom": os.path.abspath(a.bom), "boards": a.boards, "overage_pct": a.overage, "extra_per_line": a.extra,
           "currency": cfg["currency"], "estimated_total": round(total, 2), "summary": summary, "lines": results}
    path = a.out or os.path.splitext(os.path.abspath(a.bom))[0] + ".digikey_match.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    out["match_file"] = path
    emit(out)


def load_match(path):
    m = read_json(path)
    if not m.get("lines"):
        fail("Not a bom-match output file: %s" % path)
    return m


def orderable(m, include_review):
    for ln in m["lines"]:
        if ln.get("dk_pn") and (ln["status"] == "matched" or (include_review and ln["status"] == "needs_review")):
            yield ln


def cmd_list_csv(a):
    m = load_match(a.match)
    path = a.out or os.path.splitext(a.match)[0] + ".digikey_order.csv"
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Quantity", "Digi-Key Part Number", "Manufacturer Part Number", "Customer Reference"])
        for ln in orderable(m, a.include_review):
            w.writerow([ln["order_qty"], ln["dk_pn"], ln.get("mpn", ""), ln.get("refs", "")[:100]])
            n += 1
    skipped = [{"line": l["line"], "mpn": l.get("bom_mpn") or l.get("mpn"), "status": l["status"], "flags": l.get("flags")}
               for l in m["lines"] if not (l.get("dk_pn") and (l["status"] == "matched" or (a.include_review and l["status"] == "needs_review")))]
    emit({"csv": path, "lines_written": n, "lines_skipped": skipped,
          "next": "Upload at digikey.com (Order Tools > Bulk Add / BOM Manager), or use `mylist` to create it in your account."})


def cmd_login(a):
    cfg = load_cfg()
    need_creds(cfg)
    if not cfg.get("redirect_uri"):
        fail("No redirect URI configured.", "Run `dk.py config --redirect-uri https://localhost` "
             "(must exactly match what is registered on developer.digikey.com, and be https).")
    q = urllib.parse.urlencode({"response_type": "code", "client_id": cfg["client_id"],
                                "redirect_uri": cfg["redirect_uri"], "state": "dk"})
    url = cfg["authorize_url"] + "?" + q
    if not a.url:
        emit({"step": 1, "open_this_url": url,
              "then": "Sign in and approve. The browser will land on your redirect URI (probably an error page). "
                      "Copy the FULL address-bar URL and run `dk.py login --url '<that url>'` within 1 minute."})
    code = urllib.parse.parse_qs(urllib.parse.urlparse(a.url).query).get("code", [None])[0]
    if not code:
        fail("No ?code= found in that URL.", "Restart with `dk.py login`; codes expire after 1 minute.")
    st, js, _ = http("POST", token_url(cfg), form={
        "code": code, "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"], "grant_type": "authorization_code"})
    if st != 200 or "access_token" not in js:
        fail("Code exchange failed (HTTP %s)." % st, "Codes last 1 minute and are single-use; redo step 1. "
             "Redirect URI must match the registration exactly (trailing slash included).", response=js)
    save_user_token(js)
    emit({"logged_in": True, "access_token_minutes": round(int(js.get("expires_in", 0)) / 60),
          "refresh_token_expires_in_s": js.get("refresh_token_expires_in")})


def cmd_mylist(a):
    cfg = load_cfg()
    m = load_match(a.match)
    lines = list(orderable(m, a.include_review))
    if not lines:
        fail("No orderable lines in the match file.")
    parts = []
    for ln in lines:
        p = {"RequestedPartNumber": ln["dk_pn"], "Quantities": [{"Quantity": ln["order_qty"]}]}
        if ln.get("manufacturer"):
            p["ManufacturerName"] = ln["manufacturer"]
        if ln.get("refs"):
            p["ReferenceDesignator"] = ln["refs"][:200]
            p["CustomerReference"] = ln["refs"][:100]
        parts.append(p)
    plan = {"list_name": a.name, "parts": len(parts), "total_estimate": m.get("estimated_total")}
    if not a.confirm:
        emit(dict(plan, dry_run=True, sample=parts[:3],
                  next="Re-run with --confirm after the user approves creating this list in their Digi-Key account."))
    tok = user_token(cfg)
    st, js = call(cfg, "GET", "/mylists/v1/lists/validate/" + urllib.parse.quote(a.name, safe=""), tok)
    if st == 200 and js is False:
        fail("A list named %r already exists." % a.name, "Pick another --name; this tool never modifies existing lists.")
    st, js = call(cfg, "POST", "/mylists/v1/lists", tok, body={"ListName": a.name, "Source": "other", "Tags": a.tag or []})
    if st not in (200, 201):
        fail("CreateList failed (HTTP %s)." % st, "401/403 usually means the app lacks the MyLists API or the login "
             "expired (run `dk.py login`)." , response=js)
    list_id = js if isinstance(js, str) else (js.get("Id") or js.get("ListId") or js.get("id")) if isinstance(js, dict) else js
    st2, js2 = call(cfg, "POST", "/mylists/v1/lists/%s/parts" % list_id, tok, body=parts)
    if st2 not in (200, 201, 204):
        fail("List %s was created but adding parts failed (HTTP %s)." % (list_id, st2),
             "Open the list on digikey.com to check what landed; do not blindly retry (would duplicate).",
             list_id=list_id, response=js2)
    emit(dict(plan, created=True, list_id=list_id, response=js2 if js2 else None,
              view="Digi-Key > MyLists in your account"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("config", help="show or set credentials/settings (secret via --prompt-secret)")
    for k in ("client-id", "redirect-uri", "account-id", "site", "language", "currency"):
        p.add_argument("--" + k)
    p.add_argument("--env", choices=["prod", "sandbox"])
    p.add_argument("--prompt-secret", action="store_true")
    p.add_argument("--clear", action="store_true", help="delete stored config and tokens")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("auth-check", help="get a 2-legged token and run one test search")
    p.set_defaults(fn=cmd_auth_check)

    p = sub.add_parser("search", help="keyword search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--qty", type=int, help="pick the best variation for this quantity")
    p.add_argument("--in-stock", action="store_true")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("part", help="details for one Digi-Key or manufacturer part number")
    p.add_argument("part_number")
    p.add_argument("--qty", type=int)
    p.set_defaults(fn=cmd_part)

    p = sub.add_parser("bom-match", help="resolve a BOM CSV to orderable Digi-Key parts")
    p.add_argument("bom")
    p.add_argument("--boards", type=int, default=1)
    p.add_argument("--overage", type=float, default=0.0, help="percent extra per line")
    p.add_argument("--extra", type=int, default=0, help="spare pieces added to every line")
    p.add_argument("--map", nargs="*", help="column overrides, e.g. qty=Qty mpn='Mfr Part'")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_bom_match)

    p = sub.add_parser("list-csv", help="write a Digi-Key bulk-upload CSV from a match file")
    p.add_argument("match")
    p.add_argument("--include-review", action="store_true")
    p.add_argument("--out")
    p.set_defaults(fn=cmd_list_csv)

    p = sub.add_parser("login", help="3-legged OAuth (needed for MyLists)")
    p.add_argument("--url", help="the redirected URL containing ?code=")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("mylist", help="create a MyList in the Digi-Key account (dry run unless --confirm)")
    p.add_argument("match")
    p.add_argument("--name", required=True)
    p.add_argument("--tag", action="append")
    p.add_argument("--include-review", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p.set_defaults(fn=cmd_mylist)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

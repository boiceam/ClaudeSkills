---
name: digikey_api
description: Look up electronic components on Digi-Key and turn a BOM into an order list, using the user's Digi-Key developer API credentials (Product Information API v4 and MyLists). Use this whenever the user wants to find a part, check Digi-Key stock, price, package, lifecycle or datasheet, find an MPN or Digi-Key part number, match or cost a BOM, add spares or overage, flag out-of-stock/obsolete/ambiguous lines, export a Digi-Key bulk-upload CSV, or create a MyList in their Digi-Key account so they can place the order themselves. Also use it when they mention Digi-Key, DigiKey, DK part numbers, "price this BOM", "can I buy this", or "make an order list", even if they do not say "API".
---

# Digi-Key API

Looks parts up and builds order lists. **It never places an order or spends money**; the
furthest it goes is creating a MyList (or a CSV) that the user opens on digikey.com and
checks out themselves.

Everything goes through one script (Python 3.8+ stdlib plus the system `curl`, which it uses for HTTPS so corporate TLS inspection such as Zscaler works; no installs). Each call prints
one JSON object; exit 0 ok, 1 error.

```bash
python3 <skill-dir>/scripts/dk.py <command> [options]
```

`<skill-dir>` is the folder containing this file.

## Commands

| Goal | Command |
|---|---|
| Show config status (never prints the secret) | `dk.py config` |
| Store credentials | `dk.py config --client-id ID --redirect-uri https://localhost` then `dk.py config --prompt-secret` |
| Verify credentials work | `dk.py auth-check` |
| Keyword search | `dk.py search "10k 0603 1%" --limit 5 --in-stock --qty 100` |
| One part (DK PN or MPN) | `dk.py part RC0603FR-0710KL --qty 250` |
| Match a BOM CSV | `dk.py bom-match bom.csv --boards 10 --overage 10 --extra 2` |
| Digi-Key bulk-upload CSV | `dk.py list-csv bom.digikey_match.json [--include-review]` |
| Log in for account writes (once) | `dk.py login`, then `dk.py login --url '<redirected url>'` |
| Create a MyList | `dk.py mylist bom.digikey_match.json --name "Rev B build" [--confirm]` |

## Setup (first time only)

On a new machine, follow `SETUP.md` (clone, symlink, register the Digi-Key app, store credentials).
In short:

The user registers an app at developer.digikey.com and enables the **Product Information**
and **MyLists** APIs on it. Then:

1. `dk.py config --client-id <id> --redirect-uri <https URI registered on the app>`
2. `dk.py config --prompt-secret` — the user types the secret into the hidden prompt. **Never
   ask for the secret in chat, never put it on a command line, never echo it.** Alternatively
   they can export `DIGIKEY_CLIENT_ID` / `DIGIKEY_CLIENT_SECRET` themselves.
3. `dk.py auth-check`. If it fails, read the `hint` (wrong environment: `--env sandbox`
   if the app is a sandbox app; API product not enabled; wrong secret).

Config and tokens are stored in `~/.config/digikey_api/` with mode 0600. `config --clear`
deletes them.

## Workflow: BOM to order list

1. **Match.** Ask for quantity of boards, and whether to add overage (percent) or spares
   (`--extra`). Then `bom-match`. The BOM must be a CSV with a quantity column and an MPN or
   Digi-Key PN column (headers are auto-detected; use `--map qty=Qty mpn='Mfr Part'` if not).
   Duplicate part numbers are merged. It writes `<bom>.digikey_match.json` and prints a
   summary. Calls are throttled (about 0.55 s each), so 100 lines take about a minute.
2. **Review with the user before building anything.** Report the totals and go through
   every line that is not `matched`: `not_found`, `ambiguous` (show candidates), and
   `needs_review` (flags: MPN differs from the BOM, not Active/EOL/NRND, out of stock, MOQ
   above need). Never silently substitute a part; a different MPN is the user's call. Show
   money with the currency printed in the output and say it is an estimate from list
   pricing at the quantity break (not their account pricing).
3. **Build the list.** `list-csv` always works and needs no login. `mylist` creates the
   list in their account: run it without `--confirm` first (dry run), show what it will
   create, and only add `--confirm` after the user says yes. It refuses a name that already
   exists and never edits existing lists. Excluded lines are listed; add
   `--include-review` only for lines the user has accepted.
4. Tell the user where to find it (MyLists on digikey.com, or the CSV path) and that they
   place the order themselves.

## Things to know

- **Two auth modes.** Part lookup uses an app-only token (automatic). MyLists needs the
  user's own sign-in (3-legged): `dk.py login` prints a URL; the user opens it, approves, and
  the browser lands on the redirect URI (often an error page); they paste that full URL back
  into `login --url`. The code lasts 1 minute and is single-use. Refresh is automatic after
  that; if it fails, repeat `login`.
- **Rate limits.** Product Information is 120 calls/min and 1000/day. A large BOM costs 1 to
  2 calls per line. The script warns when fewer than 50 daily calls remain; do not loop
  searches needlessly, and prefer `bom-match` over per-line `part` calls.
- **Variation choice.** The script picks the cheapest in-stock, non-marketplace variation
  (cut tape, tape and reel, etc.) at the order quantity, respecting minimum order quantity.
  Mention when a reel is chosen or when it bumped quantity up to the MOQ.
- **Search tips.** MPN searches are best. Parametric free text ("10k 0603 1%") returns
  several candidates; show the user the top few rather than picking for them.
- **Failures.** HTTP 401/403 = credentials, environment or API product not enabled;
  429 = rate limit (the script already retried). Do not retry `mylist --confirm` after a
  partial failure; it reports the `list_id` so the user can inspect the list first.
- See `references/api.md` for endpoints, headers and what is unverified.

## Verification status

The offline suite (`python3 tests/test_offline.py`) exercises token handling, search,
BOM merging and matching, CSV export, 3-legged login and MyList creation against a local fake
server. Verified against the live API on 2026-11-22: `auth-check` and `part`. **Not yet verified live**:
`bom-match` on a real BOM, `login` and `mylist`. Check the first real runs against digikey.com;
open items are in `references/api.md`.

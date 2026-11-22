# Setting up digikey_api on a new machine

The skill is one folder (`digikey_api/`) in the `ClaudeSkills` repo. Nothing secret is stored
in the repo; credentials live only in `~/.config/digikey_api/` on each machine.

## 1. Prerequisites
- Python 3.8 or newer (standard library only, nothing to `pip install`)
- `curl` (preinstalled on macOS and most Linux; used for all HTTPS so corporate TLS inspection
  such as Zscaler works)
- Claude Code (or the Claude desktop app's Code tab)
- A Digi-Key account

## 2. Get the skill
```bash
git clone git@github.com:boiceam/ClaudeSkills.git ~/Documents/Projects/Claude/Skills
mkdir -p ~/.claude/skills
ln -s ~/Documents/Projects/Claude/Skills/digikey_api ~/.claude/skills/digikey_api
```
Use any clone location; just point the symlink at it. Start a new Claude session and the
skill (`digikey_api`) appears in the skills list. Update later with `git pull`.

## 3. Register a Digi-Key app (once per Digi-Key account)
1. Sign in at https://developer.digikey.com and create an **Organization**, then a **Production App**.
2. Enable the **Product Information** API (part lookup) and the **MyLists** API (list creation) on the app.
3. Set the **OAuth Callback / Redirect URI** to `https://localhost` (it must be https and
   must match what you give the skill exactly, including any trailing slash).
4. Copy the **Client ID** and **Client Secret**. The same app can be used on several machines.

A Sandbox app works too (fake data); add `--env sandbox` in step 4.

## 4. Store the credentials on this machine
```bash
python3 ~/.claude/skills/digikey_api/scripts/dk.py config --client-id <CLIENT_ID> --redirect-uri https://localhost
python3 ~/.claude/skills/digikey_api/scripts/dk.py config --prompt-secret
```
The second command asks for the secret with hidden input. Do not put the secret on a command
line, in chat, or in the repo. (Alternative: export `DIGIKEY_CLIENT_ID` and
`DIGIKEY_CLIENT_SECRET` in your shell profile.) Files are written with mode 0600.

Optional settings: `--site US --language en --currency USD`, and `--account-id <id>` if
Digi-Key tells you an account id is required.

## 5. Verify
```bash
python3 ~/.claude/skills/digikey_api/scripts/dk.py auth-check
```
Expect `"token_ok": true, "product_search_ok": true`. Then try
`dk.py part RC0603FR-0710KL --qty 100`.

## 6. One-time login for MyLists (optional)
Creating a list in your Digi-Key account needs your own sign-in, separate from the app credentials.
```bash
python3 ~/.claude/skills/digikey_api/scripts/dk.py login
```
Open the printed URL, sign in and approve. The browser ends on `https://localhost/?code=...`
(an error page is normal). Within one minute run:
```bash
python3 ~/.claude/skills/digikey_api/scripts/dk.py login --url '<full URL from the address bar>'
```
Tokens refresh automatically after that. Without this step everything still works except
`mylist`; `list-csv` needs no login.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Digi-Key rejected the client credentials` | Wrong ID/secret, or a sandbox app with `--env prod` (or the reverse). |
| HTTP 403 on search or MyLists | That API product is not enabled on the app at developer.digikey.com. |
| `curl not found` | Install curl. |
| `SSL certificate problem` from curl | Your network's CA is not in the system trust store; install your company root CA. |
| `login` code exchange fails | The code is single-use and lasts 1 minute; redo `login`. Redirect URI must match exactly. |
| HTTP 429 | Limit is 120 calls/min and 1000/day; wait and retry. |

## Offline self-test (no account needed)
```bash
python3 ~/.claude/skills/digikey_api/tests/test_offline.py
```

## Remove
```bash
python3 ~/.claude/skills/digikey_api/scripts/dk.py config --clear   # deletes stored secret and tokens
rm ~/.claude/skills/digikey_api
```

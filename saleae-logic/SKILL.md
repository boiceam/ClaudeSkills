---
name: saleae-logic
description: Capture, decode and analyze signals with a Saleae Logic Pro 16 (also Logic Pro 8 / Logic 8) logic analyzer by driving the Logic 2 desktop app through its automation API. Use this whenever the user wants to capture digital or analog signals on a board, debug or decode a bus (I2C, SPI, UART/async serial, CAN, 1-Wire, I2S, etc.), trigger on an edge or pulse, measure a clock frequency, duty cycle, pulse width or timing, check whether a pin is toggling, look at boot/reset sequencing, or export a .sal capture or CSV. Also use it when the user mentions Saleae, Logic 2, Logic Pro, a logic analyzer, a .sal file, or says things like "why isn't my I2C device responding", "is the SPI clock running", or "what is on these pins" while one is on the bench, even if they do not name the instrument.
---

# Saleae Logic Pro 16

The Logic Pro 16 has 16 channels. Each can be a digital input, and each can also be sampled
as an analog input (+/-10 V, 12-bit, 5 MHz bandwidth) at the same time. Digital runs up to
500 MS/s on a few channels and about 100 MS/s on all 16; analog is up to 50 MS/s on a few
channels and drops as analog channels are added (about 6.25 MS/s with 16). It needs USB 3.0.

It is controlled through the **Logic 2 desktop app**, which must be running with its
automation server on (gRPC, port 10430). Everything goes through one bundled script. It needs
`uv` (it pins Python 3.10 and installs `logic2-automation` itself on first run) and prints one
JSON object per call.

```bash
uv run -q <skill-dir>/scripts/sl.py <command> [options]
```

`<skill-dir>` is the folder containing this file.

## Commands

| Goal | Command |
|---|---|
| Is Logic 2 reachable, what devices are attached | `sl.py info` |
| Launch Logic 2 with automation enabled | `sl.py app start` (`app status` to check) |
| Timed capture, save `.sal`, show edge/frequency stats | `sl.py capture -D 0-3 -r 10M -t 2 --stats` |
| Capture and decode a UART | `sl.py capture -D 0 -r 10M -t 3 --analyzer 'Async Serial\|label=uart\|Input Channel=0\|Bit Rate (Bits/s)=115200'` |
| Wait for an edge, then record | `sl.py capture -D 0-7 -r 50M --trigger falling --trigger-channel 3 -t 0.5 --timeout 30` |
| Capture analog too | `sl.py capture -D 0 -A 1 -r 10M --analog-rate 12.5M -t 1 --csv` |
| Re-open a saved capture and export/analyze | `sl.py load boot.sal --out boot_export` |
| Stats from an existing `digital.csv` (no Logic 2 needed) | `sl.py stats raw/digital.csv` |

Rates and times accept suffixes: `10M`, `500k`, `100n`. Channel lists accept ranges: `0-3,8`.
Output lands in `./saleae_captures/<timestamp>/` unless `--out` is given. Exit code 0 is
success, 1 an error (JSON has `error` and usually a `hint`), 2 a trigger timeout.
`--sim` allows Logic 2's built-in demo device, which is useful to smoke-test the setup without hardware.

## How to work with it

**Start with `info`.** If it says the automation server is unreachable, run `app start`.
That only works if Logic 2 is not already running; if it is, ask the user to quit it, or to
tick the automation server box at the bottom of Logic 2 Preferences. Never assume a capture
happened without a JSON result. Check `real_device_present`: if false and the user expects
hardware, it is a USB/cable/driver problem, not a software one.

**Physical setup comes first, and only the user can do it.** Ask or confirm: which pins go to
which channels, that the analyzer ground is connected to the target ground (a missing ground is
the most common cause of garbage captures), and the logic voltage of the target. The Pro series
threshold (`--threshold`) is 1.2, 1.8 or 3.3 V; pick the one at or below the target's logic
level (3.3 for a 3.3 V part, 1.8 for 1.8 V; 5 V parts work with 3.3). A wrong threshold gives
stuck-high/low or noisy decodes. Do not guess pin-to-channel mapping.

**Choose the sample rate deliberately.** Use at least 4x (ideally 10x) the fastest signal
frequency you must resolve; edge timing accuracy is one sample period. Higher rates cost RAM
and USB bandwidth and shrink the channel count you can use, so do not default to the maximum.
If `start_capture` fails, the rate/channel combination is not allowed; lower `--rate`, drop
channels, or drop analog channels. For long captures, prefer a trigger plus a short `-t`
window, or `--trim` for a rolling window, over a huge timed capture.

**Pick the cheapest tool that answers the question.**
- *Is it alive? How fast? What duty?* Use `--stats` on a short capture. It exports
  `digital.csv` and summarizes edge counts, frequency, period/high/low min-mean-max and
  duty cycle per channel. A channel reported with "no transitions" is stuck, floating, or not
  connected, which is itself the finding.
- *What is it saying?* Add an `--analyzer`. The decoded rows come back as `rows`, `columns`
  and a `preview` (default 20 rows; raise `--preview`), with the full table in the CSV path.
  Never read a large CSV or `.sal` into context; use the summary and `grep`/`head` on the file.
- *Did the timing violate a spec?* Capture with `--stats`, compare min/max pulse widths and
  periods against the datasheet. For one-off events, trigger on them rather than hunting.
- *Is the signal clean?* Digital capture cannot show ringing or slow edges; capture the same
  pin as analog (`-A`) and inspect the analog CSV, or tell the user to look at it in Logic 2.

**Analyzer names and settings must match the Logic 2 UI exactly** (spelling, case, spaces,
units). Keys and values are the labels shown in the analyzer's settings dialog. Known to
work: name `Async Serial` with `Input Channel`, `Bit Rate (Bits/s)`, `Bits per Frame`. For
other analyzers (I2C, SPI, CAN, 1-Wire, I2S, ...) the exact keys are not documented by Saleae;
if `add_analyzer` errors, see `references/debugging.md` for starting points and ask the user to
read the field labels off the Logic 2 settings dialog, then retry. Decode on a capture you
already took is not possible through the API (analyzers on a loaded `.sal` are not accessible),
so add analyzers at capture time, or re-capture.

**Timeouts and triggers.** A trigger capture blocks until the condition occurs. Always pass
`--timeout`, and if it exits 2, report that no trigger was seen and suggest threshold, polarity
or channel as the cause before retrying.

**Report like an engineer.** State the setup (channels, rate, threshold, duration), what was
measured with units, and what it implies. Keep the `.sal` path so the user can open it in
Logic 2 for visual inspection. Distinguish measured facts from inference (for example "SCL
runs at 398 kHz" versus "so the part is in fast mode"). If a result contradicts the user's
expectation, check ground, threshold and channel mapping before concluding the DUT is faulty.

## Limits

- Logic 2 must stay running; the API cannot drive a headless instrument and supports one
  capture at a time. Python is limited to 3.8 to 3.10 (the script handles this via `uv`).
- `capture` exports data only after the capture ends. There is no live streaming; for
  continuous monitoring, loop short captures.
- Analog and digital share the same 16 physical channels; enabling analog on a channel does
  not stop its digital capture, but it lowers the maximum sample rates.
- Verified on 2026-11-18 against Logic 2 2.4.50 using the built-in simulator (`--sim`): `app start`,
  `info`, timed and triggered `capture`, `--stats`, Async Serial decode, `load` and `stats`.
  Not yet verified with a physical Saleae attached; see `references/debugging.md` for what to
  confirm on the first real run.
- Logic 2 refuses `add_analyzer` while a capture is running, so `sl.py` adds analyzers after
  the capture ends; the decode still completes and exports.
- On macOS Logic 2 installs as `/Applications/Saleae Logic.app`.

---
name: joulescope
description: Measure and control power on an electronics project with a Joulescope (JS110, JS220, JS320) DC energy analyzer over USB. Use this whenever the user wants to measure current, voltage, power, energy, or charge of a board or device under test; check sleep/idle/active current; estimate battery life from a real measurement; profile power over time; capture a current waveform or .jls recording; power-cycle, switch on, or switch off a target through the Joulescope; wait for a device to boot or go to sleep based on its current draw; or drive/read the Joulescope GPIO pins. Also use it when the user mentions a Joulescope, JLS files, or asks "how much current is my board drawing" while one is on the bench, even if they do not name the instrument.
---

# Joulescope

A Joulescope sits in series between a power supply and a device under test (DUT). It
measures current (nA to amps, autoranging) and voltage at up to 1 Msps, and it can open
the current path, which makes it a software-controlled power switch for the DUT.

Everything goes through one bundled script. It needs `uv` (dependencies install
themselves on first run) and prints a single JSON object per call.

```bash
uv run -q <skill-dir>/scripts/js.py <command> [options]
```

`<skill-dir>` is the folder containing this file. All values are SI units: A, V, W, J, C, s.

## Commands

| Goal | Command |
|---|---|
| What is connected, is the DUT powered | `js.py info` |
| Average / min / max current, voltage, power, plus energy and charge | `js.py measure -t 5` |
| Same, plus a time series to plot or inspect | `js.py measure -t 30 -f 10 --series out.csv` |
| Switch DUT power | `js.py power on` / `off` / `status` |
| Power-cycle, then measure the boot | `js.py power cycle --off-time 1 --measure 5` |
| Block until the DUT sleeps (or wakes) | `js.py wait --signal current --below 50e-6 --hold 1 --timeout 60` |
| Waveform capture for detail or the Joulescope UI | `js.py record boot.jls -t 5 -f 100000` |
| Analyze a recording, or a window of it | `js.py jls boot.jls --start 0.2 --end 0.4 --csv boot.csv` |
| GPIO | `js.py gpo 0 1`, `js.py gpi` |
| Anything else (raw driver topics) | `js.py get s/i/range/mode`, `js.py set s/v/range/select="2 V"` |

With more than one Joulescope attached, put `-d <serial-or-model>` before the command.

Exit code 0 is success, 1 is an error (JSON has an `error` key), 2 means `wait` timed
out without the condition being met.

## How to work with it

**Start with `info`.** It confirms the instrument is reachable and reports `output`
(`on`/`off`), i.e. whether current can flow to the DUT. If `output` is `off`, every
current reading is 0 and the DUT is unpowered; that is a state to fix or ask about, not a
measurement.

**Pick the tool by time scale.**
- `measure` uses statistics computed inside the instrument from every sample. Min and max
  are true sample extremes regardless of `-f`, and energy/charge are exact. This is the
  right default: it is cheap, produces tiny output, and runs for hours if needed.
  `-f` only sets how finely the `--series` CSV is sliced (JS220/JS320: roughly 1 Hz to
  1 kHz; JS110 is fixed at 2 Hz).
- `record` saves raw samples. Use it when shape matters: inrush, radio bursts, wake-up
  transients, anything shorter than about 10 ms. Files grow at about
  12 bytes x sample rate per second for the default three signals (100 kHz for 10 s is
  about 12 MB; 1 MHz is ten times that), so choose the lowest rate that resolves the
  event and keep durations short. Then use `jls` to get statistics for the whole file
  or a `--start/--end` window, and `--csv` for a downsampled avg/min/max envelope.
  Do not read a .jls or a large CSV into context; summarize with `jls` first.
- `wait` replaces sleep-and-poll loops: boot finished, entered deep sleep, charger
  started. It returns the elapsed time, which is itself a useful measurement.

**Report with engineering units.** Convert to mA / µA / mW in what you tell the user. For
battery life, `capacity_mAh / (average current in mA)` gives hours; say what duty cycle
the measurement window covered, because a window that misses the periodic wake-up will
overstate battery life badly. For duty-cycled devices measure across several full cycles.

**State persists between calls.** Range, output, and GPIO settings stay as set for as
long as the instrument has USB power, so `power on` in one call and `measure` in the next
works. A JS220/JS320 that was just plugged in starts with the output **off**.

**Sanity-check the readings.** Voltage near 0 with the output on usually means the supply
is off or the leads are on the wrong side. A negative current means IN and OUT are swapped.
Current pinned at 0 with normal voltage means the output is off or the DUT is not connected.
A voltage that drifts around (for example wandering between 9 and 15 V) with only
nanoamps of current means the terminals are floating: nothing is wired to the instrument.

## Power control: what to know before switching

- `power off` opens the Joulescope's current path. The DUT loses its supply through the
  Joulescope, but anything else attached to it (USB cable, debugger, serial adapter) can
  still back-power it through I/O pins. If a "powered off" board still seems alive, that
  is why.
- Switching power is a real-world action on the user's hardware. Doing it as part of a
  task the user asked for (power-cycle, measure boot) is expected. Do not turn a DUT off
  as a side effect of some other task, and after a temporary `off`, restore it.
- The script always opens the instrument in the driver's `restore` mode. This matters if
  you ever write your own code: the driver's default open mode pushes firmware defaults,
  and on the JS220/JS320 the default current range is **off**, so a careless
  `driver.open(device)` or the stock `python -m pyjoulescope_driver record` without
  `--open restore` can cut or alter power to the DUT.
- Only one program can hold the instrument. If a command reports it could not open the
  device, the Joulescope UI or another script has it; ask the user to close it.

## GPIO

`gpo 0|1 0|1` drives the two outputs and `gpi` reads the inputs (4 on JS220/JS320, 2 on
JS110). On the JS220/JS320 the logic level comes from an external Vref pin by default, so
outputs do nothing until Vref is supplied or you select the internal 3.3 V reference with
`js.py set c/gpio/vref=1`. Check the target's I/O voltage before doing that.

## Going beyond the script

For custom capture logic (triggers on GPI, synchronized multi-instrument runs, streaming
processing), write Python against `pyjoulescope_driver` and reuse the patterns in
`scripts/js.py`. Read `references/driver.md` first: it has the topic names, statistics
message layout, model differences, and the traps found while building this skill.

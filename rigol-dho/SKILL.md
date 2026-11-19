---
name: rigol-dho
description: Control a RIGOL DHO800/DHO900 series oscilloscope (DHO924S, DHO914S, DHO924, DHO814, DHO804 and siblings) over Ethernet with SCPI. Use this whenever the user wants to look at a signal on their scope; measure frequency, amplitude, rise time, duty cycle, ripple, or timing between channels; take a scope screenshot; capture or download a waveform; set up channels, timebase, or trigger; catch a one-off event with a single-shot capture; or drive the built-in signal generator on S models. Also use it when the user mentions their RIGOL, their scope, or the oscilloscope while debugging a circuit, asks "what does the signal on channel 2 look like", or wants scope evidence attached to a report, even if they do not name the model.
---

# RIGOL DHO800 / DHO900 oscilloscope

The scope accepts SCPI text commands on a network socket. One bundled script wraps the
common jobs and prints a single JSON object per call. It uses only the Python standard
library.

```bash
python3 <skill-dir>/scripts/dho.py <command> [options]
```

`<skill-dir>` is the folder containing this file. Values are SI units: V, s, Hz.

## First contact

The script needs the scope's IP address once. The user finds it on the scope under
Utility > IO; ask for it rather than scanning the network.

```bash
python3 dho.py connect 192.168.1.50   # checks *IDN? and saves the address
```

After that every command finds the scope on its own. `--host` or `$DHO_HOST` override the
saved address.

## Commands

| Goal | Command |
|---|---|
| What the scope is set to right now | `dho.py status` |
| A picture of the screen | `dho.py screenshot shot.png` |
| Automatic measurements | `dho.py measure VPP FREQ RTIM --source 1` |
| Timing between two channels | `dho.py measure RRD RRPH --source 1 --source2 2` |
| Channel setup | `dho.py channel 1 --on --scale 0.5 --offset 0 --coupling DC --probe 10` |
| Horizontal setup | `dho.py timebase --scale 1e-3 --offset 0` |
| Edge trigger | `dho.py trigger --source 1 --slope POS --level 1.65 --sweep NORM` |
| Run control | `dho.py run`, `stop`, `force`, `clear`, `autoset` |
| Catch one event | `dho.py single --wait 10` |
| On-screen waveform as data (about 1000 points) | `dho.py waveform --source 1 --csv ch1.csv` |
| Full-memory waveform | `dho.py waveform --source 1 --mode RAW --stop --csv ch1.csv` |
| Memory depth, averaging | `dho.py acquire --depth 1M --type AVER --averages 16` |
| Signal generator (S models) | `dho.py afg --function SQU --frequency 1000 --amplitude 3.3 --offset 1.65 --on` |
| Anything else | `dho.py scpi ":CHAN1:LAB:CONT?"` |

Exit code 0 is success, 1 is an error (JSON has `error`), 2 means `single --wait` saw no
trigger. If the scope rejected a command, the JSON carries `scpi_errors`; treat that as
"the setting did not take" and check `references/scpi.md` for the valid values.

## How to work with it

**Look before you change.** Start with `status` and usually a `screenshot`, then Read the
PNG. The image shows what the user sees, including whether the trace is clipped, tiny,
or untriggered, which numbers alone will not tell you.

**Measurements are only as good as the setup.** The scope measures what is on screen, so
a `null` or implausible value almost always means the display is wrong for the signal:
- fewer than one or two full cycles visible, so frequency and period fail: slow the timebase;
- trace clipped off the top or bottom, so amplitudes are wrong: increase V/div or adjust offset;
- trace only a fraction of a division tall, so resolution is poor: decrease V/div;
- trigger status `AUTO` with a rolling display, so timing values wander: set the trigger
  source and level to the signal.
Aim for the signal to span 4 to 6 vertical divisions (the screen has 8) and 2 to 5 cycles
horizontally (10 divisions), then measure. Report the setup alongside the numbers.

**`measure` adds items to the scope's screen.** Each queried item appears in a Result
panel on the right of the display and stays there, covering part of the trace in later
screenshots. Add `--clear` to remove the panel afterwards; it removes every item, including
any the user set up, so skip it if `screenshot` showed they already had measurements on.

**Probe ratio matters.** `probe_ratio` in `status` must match the physical probe (1x or
10x switch). A mismatch makes every voltage wrong by exactly 10. If voltages look off by
that factor, ask the user which setting the probe is on rather than changing it blindly.

**Prefer `measure` and `screenshot` over downloading waveforms.** They are fast and small.
Download a waveform when you need to compute something the scope cannot, or the user
wants the data. `waveform` always prints statistics; write samples to `--csv` and never
dump them into context. NORM mode returns the 1000 points on screen. RAW returns
the acquisition memory (up to 50M points), needs the scope stopped, and transfers about
200k points per second (1M points takes about 6 s, 50M would take minutes): use `--start/--end` to take a slice or `--decimate` to thin it, and prefer a
modest `acquire --depth` such as 100k or 1M before the capture.

**Single-shot capture** for events like power-up, a glitch, or one bus transaction: set
the channel, timebase and trigger (sweep NORM, level inside the signal swing), run
`single --wait <s>`, cause the event, then `screenshot`, `measure`, or `waveform --mode
RAW`. If nothing triggers, the trigger level or source is wrong far more often than the
signal is absent.

**Changing the scope changes the user's bench.** Reading (`status`, `measure`,
`screenshot`, NORM `waveform`) is harmless. Changing scales or trigger alters what the
user sees; that is expected when they asked you to measure something, but say what you
changed. `autoset` rewrites most settings, so use it only when the user asked or the
display is hopeless, and mention it. `stop` and `single` freeze the display: put the scope
back in `run` when you are done unless the user wanted the capture held.

**The signal generator drives real hardware.** `afg --on` puts a voltage on whatever is
connected to the generator output. Confirm the amplitude and offset suit the circuit
(for example 3.3 V logic: amplitude 3.3, offset 1.65) before turning it on, and never
enable it as a side effect. Amplitude is peak-to-peak into a high-impedance load.

## Working with the Joulescope skill

The two instruments answer different questions: the Joulescope gives accurate current,
power and energy; the scope shows fast voltage behavior on up to four nodes. A common
pattern is to arm the scope (`single`), power-cycle the board through the Joulescope, and
read both.

## More detail

`references/scpi.md` has the full measurement item list, valid values for the settings
above, the waveform conversion formula, and commands the script does not wrap (digital
channels, math, cursors, saving to the scope's storage). Use it with `dho.py scpi`.

---
name: fnirsi-dps150
description: Control and log a FNIRSI DPS-150 programmable DC bench power supply (30 V / 5 A / 150 W) over its USB-C serial port. Use whenever the user wants to set voltage or current limit, switch the output on/off, read output V/I/P, set OVP/OCP/OPP/OTP/LVP protection, store presets, sweep voltage, or capture a CSV of what a device under test draws. Also use when they mention the DPS-150, FNIRSI, or a USB bench supply on /dev/cu.usbmodem*, even if they do not say "skill".
---

# FNIRSI DPS-150 over USB

`scripts/dps150.py` is a self-contained driver and CLI (Python + pyserial). Protocol details and sources are in `references/protocol.md`; read it only if you need to extend the driver or debug framing.

Run everything with `uv run` so pyserial is provided automatically:

```bash
S="$HOME/.claude/skills/fnirsi-dps150/scripts/dps150.py"     # user-level install; use the project path if installed per-project
uv run "$S" ports                  # find the device (USB VID:PID 2E3C:5740)
uv run "$S" info                   # model, HW/FW version, device V/I limits
uv run "$S" status                 # full snapshot (add --json to parse)
```

Without `uv`: `pip install pyserial`, then `python3 "$S" ...`. The port is autodetected; pass `--port /dev/cu.usbmodemXXXX` to override. Use the `cu.*` node on macOS, not `tty.*`.

## Safety rules (the output drives a real load)

The supply can destroy a device under test (DUT) in milliseconds. Follow this order and do not skip steps:

1. **Never turn the output on without the user's say-so for that run.** Reading (`info`, `status`, `monitor`, `log`) and setting values are safe; `on`, `sweep` and anything that enables output are not. If the user has not stated the target voltage and current limit for the DUT, ask. Do not guess a voltage.
2. **Set the current limit first, then the voltage, then enable.** `set` and `on --v --i` already do this and verify by read-back (the device does not echo float writes).
3. **Start low.** For an unknown load use a low current limit (for example 0.1 A) and the lowest voltage that works; raise from there.
4. **Set OVP/OCP slightly above the intended operating point** before powering a sensitive DUT (`protect ovp 3.6`).
5. **Leave the output in the state the user expects.** `sweep` turns output off at the end and restores the previous voltage/current setpoints (unless `--keep-on`). `protect` changes persist on the device; tell the user what you changed and offer to restore defaults (OVP 30 V, OCP 5.1 A, OPP 150 W, OTP 80 C, LVP 5 V as seen on one unit). If anything fails mid-run, run `off` and report the state.
6. **Never send command byte `0xC0`.** It puts the unit into firmware-upgrade (DFU) mode and the port disappears until it is re-plugged. The driver intentionally has no code path for it; do not add one.
7. This is community-reverse-engineered, unvalidated by the vendor. After any write, trust the read-back, not the command.

## Commands

| Goal | Command |
|---|---|
| Set 3.3 V, 0.5 A limit (output unchanged) | `uv run "$S" set --v 3.3 --i 0.5` |
| Output on (optionally set first) | `uv run "$S" on` / `on --v 3.3 --i 0.5` |
| Output off | `uv run "$S" off` |
| Live readout | `uv run "$S" monitor --duration 30` |
| Protection | `protect ovp 3.6`, `protect ocp 0.6`, `protect opp 10`, `protect otp 70`, `protect lvp 3.0` |
| Preset M1..M6 | `preset 1 3.3 0.5` |
| Ah/Wh counter | `meter start` / `meter stop` |
| Record CSV for N seconds | `log --duration 60 --out run1.csv` |
| Faster recording (poll full state) | `log --duration 60 --poll-hz 5 --out run1.csv` |
| Voltage sweep with logging | `sweep --vstart 0 --vstop 5 --vstep 0.5 --ilimit 0.1 --dwell 1.0 --out iv.csv` |

Add `--json` before the subcommand for machine-readable output (`uv run "$S" --json status`). Add `-v` to print raw frames to stderr when debugging. Exit code: 0 ok, 1 refused/failed verification, 2 cannot open device.

## Data capture

`log` writes CSV with columns `t_s, timestamp, vin, vset, iset, vout, iout, pout, temp, mode, output_on, protection, ah, wh` and prints a JSON summary (min/max/mean of V, I, P, temperature, integrated Wh, and the device's own Ah/Wh counters). It never changes output state, so start the load/DUT behaviour you want to observe yourself, or run it alongside `on`.

- Default rate is the device's own push stream, about **2 Hz**. `--poll-hz` requests the full state at a higher rate; the real ceiling is not documented, so check the achieved row count in the summary instead of assuming it.
- This is a power-supply readout (about 2 Hz, modest resolution), not a data-acquisition instrument. For fast transients or microamp sleep currents, say so and suggest a Joulescope (a `joulescope` skill exists) rather than over-claiming.
- After capture, analyze or plot the CSV with pandas/matplotlib. Use the `dataviz` skill for charts.

## Typical flows

**Bench-power a 3.3 V board and record 60 s of current:**
confirm DUT limits with the user, then `protect ovp 3.6`, `protect ocp 0.5`, `set --v 3.3 --i 0.3`, `on`, `log --duration 60 --out boot.csv`, `off`.

**I-V curve of an LED/diode:** `sweep` with a small `--ilimit` that protects the part; CC mode in the CSV (`mode` column) marks where the limit took over.

## Troubleshooting

- *"No DPS-150 found"*: charge-only USB cable, unit not powered (needs 5-30 V DC input or USB-C PD), or another program holds the port (FNIRSI Windows app, a Web Serial browser tab). Run `ports`.
- *Opens but no answer*: power-cycle the unit; try `--dtr`; run with `-v` to see whether RX frames arrive at all. The official app asserts RTS, which the driver does by default.
- *Readback mismatch*: the value was clamped or rejected by the device; report the read-back values to the user.
- Writes are paced at 50 ms; reading status right after a write needs about 0.3 s, which the CLI already waits.

## Verified on real hardware

Tested on one unit (HW V1.0, FW V1.1, macOS, `/dev/cu.usbmodem*`): `ports`, `info`, `status`, `protect`, `set`, `on`/`off` (through `sweep`), `log` and `sweep` into an LED + series resistor all behaved as documented. Not yet tested on hardware: `preset`, `meter`, `monitor`, `--poll-hz`, brightness/volume.

**The maximum voltage is limited by the supply's input, not always 30 V.** On USB-C PD at about 20 V in, the unit reported a 19.4 V maximum and the driver (which reads this limit from the device) refuses higher setpoints. For up to 30 V the unit needs a higher DC input. Check `info` before planning a voltage.

## Testing without hardware

`tests/fake_dps150.py` emulates the unit on a pseudo-terminal with a resistive load; `uv run tests/test_sim.py` runs the whole CLI against it. Passing the simulator proves the framing and logic against the documented protocol, not against real hardware: on first use with a real unit, run `info` and `status` first and compare against the front panel.

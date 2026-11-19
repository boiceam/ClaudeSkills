# Debugging recipes for the Logic Pro 16

Read this when planning a capture for a specific bus or symptom. All commands use
`sl.py` from `scripts/`; thresholds are 1.2 / 1.8 / 3.3 V.

## Quick liveness check ("is this pin doing anything?")
```bash
sl.py capture -D 0-7 -r 10M -t 1 --threshold 3.3 --stats
```
Read `digital_stats`: edges and frequency per channel. Zero edges = constant level; check the
level in the `.sal`, then ground, threshold and wiring.

## Bus recipes
Sample rate rule of thumb: >= 10x the bus clock/baud (UART: 10x baud is plenty).

| Bus | Channels | Rate | Notes |
|---|---|---|---|
| UART | TX or RX on one channel (decode each separately) | 10x baud, e.g. 1M for 115200 | Wrong baud = framing errors; try the common rates, or measure the shortest pulse with `--stats` and use 1/min_pulse as the baud |
| I2C | SDA, SCL | 10x SCL (4M for 400 kHz) | A missing ACK on the address byte means wrong address, no pull-ups, or unpowered part. 7-bit address shown by the decoder is not shifted |
| SPI | CLK, MOSI, MISO, CS | 10x CLK | Wrong mode (CPOL/CPHA) gives plausible-looking but wrong bytes; try the other mode first |
| CAN | CANH or RX of the transceiver | 10x bitrate | Probe at the controller side (RX/TX), not the differential bus, unless using analog |
| 1-Wire / DHT / IR | data | 1M to 10M | Use `--stats` pulse widths; timing is the protocol |

## Reset and boot sequencing
Trigger on the reset or power-good line and record the rest:
```bash
sl.py capture -D 0-9 -r 20M --trigger rising --trigger-channel 0 -t 2 --timeout 60 --stats
```
Ask the user to power-cycle the board after the command starts. Compare edge timestamps
(from the `.sal` in Logic 2 or the CSV) against the datasheet power-up sequence.

## Suspected glitches or noise
Add a glitch filter only to hide known noise: `--glitch 3:50n`. To catch glitches, trigger on a
short pulse: `--trigger pulse-low --trigger-channel 3 --max-pulse 200n`, with the sample rate
high enough to see it (500M on up to 4 channels).

## Signal integrity
Capture as analog: `-D 0 -A 0 -r 100M --analog-rate 50M -t 0.01 --csv`, then examine
`raw/analog.csv` (downsample large files with `--analog-downsample N`). Analog bandwidth is
5 MHz, so fast edges look slower than they are; do not conclude a driver is weak from rise
time alone near that limit.

## Analyzer setting keys
Saleae does not publish these. Known good: `Async Serial` -> `Input Channel`,
`Bit Rate (Bits/s)`, `Bits per Frame`. For others, copy the labels from the analyzer's
settings dialog in Logic 2 (e.g. likely `I2C` with `SDA` and `SCL`, `SPI` with `MOSI`, `MISO`,
`Clock`, `Enable`), pass channel values as the channel number, and enumerated choices as the
exact dropdown text. These guesses are unverified until run once on real hardware; when one
works, record it here.

## First-run checklist (not yet verified on hardware)
1. `sl.py app start`, then `sl.py info` shows a `LOGIC_PRO_16` with `simulation: false`.
2. `sl.py capture --sim -D 0-3 -r 10M -t 1 --stats` works with the demo device.
3. A real capture with a known signal (e.g. a 1 kHz square wave from a dev board) reports
   about 1000 Hz in `--stats`.
4. Confirm one analyzer end to end (Async Serial) and note the settings that worked.

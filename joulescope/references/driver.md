# Joulescope driver reference

Verified on a JS220 (firmware 1.3.0, FPGA 1.3.3); JS110 and JS320 notes come from the
package source only. Derived from `pyjoulescope_driver` 2.5.7, `joulescope` 1.7.3 and
`pyjls` 0.19.2 (November 2026). All three need Python >= 3.12.

## Packages

| Package | Role |
|---|---|
| `pyjoulescope_driver` | Current low-level driver. Publish/subscribe/query on topics. Use this. |
| `joulescope` | Older, higher-level API (`scan_require_one`, `device.read`, `statistics_get`) now a wrapper over the driver. Fine for quick numpy reads, but `scan(config=None)` plus `open()` applies "auto" config: current range auto, voltage 15 V. |
| `pyjls` | Reads/writes `.jls` recordings. |

Stock CLIs, useful for discovery with a device attached:

```bash
python -m pyjoulescope_driver scan
python -m pyjoulescope_driver values      # every topic and its present value
python -m pyjoulescope_driver metadata    # every topic with type, options, default
```

## Minimal driver pattern

```python
from pyjoulescope_driver import Driver

with Driver() as d:
    dev = d.find_one_device(None, brand='joulescope')   # DevicePath (a str): 'u/js220/001234'
    d.open(dev, 'restore')          # NOT the default mode, see below
    d.publish(f'{dev}/s/i/range/mode', 'auto')
    value = d.query(f'{dev}/s/i/range/mode')
    d.subscribe(f'{dev}/s/stats/value', 'pub', lambda topic, v: ...)  # callback on driver thread
    ...
    d.close(dev)
```

- `dev.model` is `js110`, `js220` or `js320`; `dev.serial_number` is the serial.
- Device spec strings accepted by `find_devices` / `find_one_device`: model (`js220`),
  serial (`001234`), `model-serial`, or full path, comma separated.
- Subscriber callbacks run on the driver thread. Hand data to a `queue.Queue`; do not
  call back into the driver with blocking timeouts from inside a callback.
- Subscribe to `@/!remove` to learn that a device was unplugged (value is the path).

### Open modes

| Mode | Effect |
|---|---|
| `'defaults'` (also `None`, the default) | Pushes the host's retained value or the firmware default for every writable topic. JS220/JS320 default `s/i/range/mode` is 0 = off, so the DUT loses power. |
| `'restore'` | Pushes nothing and adopts the instrument's present state. Use this for anything on a live DUT. |

## Topics (prefix each with the device path)

JS220 and JS320:

| Topic | Values | Notes |
|---|---|---|
| `s/i/range/mode` | `off` (0), `auto` (4), `manual` (5) | `off` opens the current path: this is the power switch |
| `s/i/range/select` | `10 A`, `180 mA`, `18 mA`, `1.8 mA`, `180 µA`, `18 µA` | used when mode is manual |
| `s/v/range/mode` | `auto` (0), `manual` (1) | |
| `s/v/range/select` | `15 V` (0), `2 V` (1) | |
| `c/gpio/vref` | `external` (0), `3v3` (1) | GPIO logic level source; default external |
| `s/fuse/N/engaged` | 0 / 1 | N = 0, 1 (user fuses, off by default), 30, 31 (built-in protection). An engaged fuse opens the current path; publish 0 to clear |
| `s/stats/scnt` | samples per statistics block at 1 Msps | 100000 = 10 Hz; default 500000; max 1000000 (1 Hz) |
| `s/stats/ctrl` | 0 / 1 | enable statistics stream |
| `s/stats/value` | subscribe | statistics message, below |
| `s/i/ctrl`, `s/v/ctrl`, `s/p/ctrl` | 0 / 1 | enable sample streams; data on `s/i/!data` etc. |
| `h/fs` | output sample rate in Hz | 1, 2, 5 steps up to 1000000. JS320 has no 500 kHz |
| `s/gpo/+/!set`, `s/gpo/+/!clr` | bit mask | set or clear outputs (bit 0 = GPO0) |
| `s/gpi/+/!req` then `s/gpi/+/!value` | | `d.publish_and_wait(req, 0, value_topic, timeout=1.0)` returns the input bits |
| `c/fw/version`, `s/fpga/version`, `c/hw/version` | u32 | `pyjoulescope_driver.program.version_to_str` |

JS110 differences:

| Topic | Notes |
|---|---|
| `s/i/range/select` | `auto`, `off`, or a fixed range. This is the power switch on the JS110; `s/i/range/mode` is the range-switch filter type instead |
| `s/v/range/select` | `15 V` or `5 V` |
| `s/sstats/value` | instrument statistics, fixed 2 Hz, no std, always on |
| `s/stats/*` | host-computed statistics; needs `s/i/ctrl`, `s/v/ctrl`, `s/p/ctrl` on and `scnt` relative to 2 Msps |
| `s/gpo/0/value`, `s/gpo/1/value` | 0 / 1 |
| `s/extio/voltage` | GPIO logic level in mV (1800 to 5000) |
| `h/fs` | up to 2000000 |

`publish` accepts the option name (`'auto'`) or the number; `query` returns the number.
To see names, types, defaults and options for every topic on the attached instrument:
`python -m pyjoulescope_driver metadata --open restore --out meta.json`. Settings written
to the instrument persist after the program closes it, until USB power is removed.

## Statistics message

```python
value['time']['samples']['value']        # [start_sample_id, end_sample_id]
value['time']['sample_freq']['value']    # sample-id rate: 2e6 (JS110/JS220), 16e6 (JS320); absent -> assume 2e6
value['signals']['current']['avg' | 'std' | 'min' | 'max']['value']    # also 'voltage', 'power'
value['accumulators']['charge']['value']   # running total, coulombs
value['accumulators']['energy']['value']   # running total, joules
```

Block duration is `(end - start) / sample_freq`. The accumulators run from when the
instrument started, so take differences, or integrate `avg * dt` per block as `js.py` does.

## Signals and sample rates

Record signal names: `current`, `voltage`, `power`, `current_range`, `gpi[0]`..`gpi[3]`,
`trigger_in` (short forms `i v p r 0 1 2 3 T`). On a JS220, `current_range` cannot be
combined with seven or more other streams.

`pyjoulescope_driver.record.Record(driver, device_path, signals)` then
`.open(filename)` / `.close()` writes a JLS v2 file from the live streams; set `h/fs`
before opening it.

## Reading JLS files

```python
from pyjls import Reader
with Reader(path) as r:
    for signal_id, s in r.signals.items():   # id 0 is the annotation signal; skip it
        s.name, s.units, s.sample_rate, s.length
    r.fsr_statistics(signal_id, start_sample, increment, count)  # count x 4: mean, std, min, max
    r.fsr(signal_id, start_sample, length)                       # raw samples (numpy)
```

`fsr_statistics` uses the file's built-in summaries, so it is fast on multi-GB files.
Prefer it over `fsr` unless individual samples are needed.

## Documentation

- Driver: https://joulescope-driver.readthedocs.io/en/latest/
- `joulescope` package: https://joulescope.readthedocs.io/
- User's guides (wiring, GPIO electrical limits, ratings): https://www.joulescope.com/pages/downloads

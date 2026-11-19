#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyjoulescope_driver>=2.5.4",
#     "pyjls>=0.19.1",
#     "numpy>=2.1",
# ]
# ///
"""Joulescope helper for agents: measure, monitor, record, and switch power.

Run with uv so dependencies resolve automatically:

    uv run js.py info
    uv run js.py measure --duration 5
    uv run js.py power cycle --off-time 1

Every command prints one JSON object on stdout (errors too, with "error").
Exit codes: 0 ok, 1 error, 2 condition not met (wait timeout).

The device is always opened in 'restore' mode.  The driver's default
'defaults' mode pushes firmware defaults to the instrument, and the JS220 /
JS320 default current range is OFF, which would cut power to the target.
"""

import argparse
import json
import os
import queue
import sys
import time
import warnings

warnings.simplefilter('ignore')  # the packages emit SyntaxWarnings on import

from pyjoulescope_driver import Driver  # noqa: E402
from pyjoulescope_driver.device_filter import DeviceFilterError  # noqa: E402

BRAND = 'joulescope'
STATS_TIMEOUT = 5.0


class JsError(Exception):
    pass


def emit(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    return code


# --------------------------------------------------------------------------
# device helpers
# --------------------------------------------------------------------------

def find_one(d, spec):
    try:
        return d.find_one_device(spec, brand=BRAND)
    except DeviceFilterError as ex:
        found = [str(p) for p in d.find_devices(None, brand=BRAND)]
        if not found:
            raise JsError('No Joulescope found. Check the USB cable, and close the '
                          'Joulescope UI if it is running (only one program can '
                          'open the instrument).') from ex
        raise JsError(f'{ex}. Connected: {found}. Pass --device with a serial '
                      'number or path.') from ex


def open_device(d, spec):
    dev = find_one(d, spec)
    try:
        d.open(dev, 'restore')
    except Exception as ex:
        raise JsError(f'Could not open {dev}: {ex}. Another program (Joulescope UI '
                      'or another script) probably has it open.') from ex
    return dev


def is_js110(dev):
    return dev.model == 'js110'


def _q(d, topic, default=None):
    try:
        return d.query(topic)
    except Exception:
        return default


_I_MODE = {0: 'off', 4: 'auto', 5: 'manual'}
_I_SELECT = {0: 'off', 129: '10 A', 130: '180 mA', 132: '18 mA', 136: '1.8 mA',
             144: '180 µA', 160: '18 µA'}
_V_MODE = {0: 'auto', 1: 'manual'}
_V_SELECT = {0: '15 V', 1: '2 V'}
_VREF = {0: 'external', 1: '3v3'}


def power_get(d, dev):
    """Return 'on' or 'off' for the current path through the instrument."""
    if is_js110(dev):
        v = _q(d, f'{dev}/s/i/range/select')
        if isinstance(v, str):
            return 'off' if v.lower() == 'off' else 'on'
        # 0x80 auto, 0 off, bit flags for fixed ranges
        return 'off' if v in (0, None) else 'on'
    v = _q(d, f'{dev}/s/i/range/mode')
    if isinstance(v, str):
        return 'off' if v.lower() == 'off' else 'on'
    return 'off' if v == 0 else 'on'


def power_set(d, dev, on):
    if is_js110(dev):
        d.publish(f'{dev}/s/i/range/select', 'auto' if on else 'off')
    else:
        d.publish(f'{dev}/s/i/range/mode', 'auto' if on else 'off')


def state(d, dev):
    s = {
        'device': str(dev),
        'model': dev.model,
        'serial_number': dev.serial_number,
        'output': power_get(d, dev),
    }
    if is_js110(dev):
        s['i_range_select'] = _q(d, f'{dev}/s/i/range/select')
        s['v_range_select'] = _q(d, f'{dev}/s/v/range/select')
    else:
        def named(topic, names):
            v = _q(d, f'{dev}/{topic}')
            return names.get(v, v)
        s['i_range_mode'] = named('s/i/range/mode', _I_MODE)
        s['i_range_select'] = named('s/i/range/select', _I_SELECT)
        s['v_range_mode'] = named('s/v/range/mode', _V_MODE)
        s['v_range_select'] = named('s/v/range/select', _V_SELECT)
        s['gpio_vref'] = named('c/gpio/vref', _VREF)
        fuses = [k for k in (0, 1, 30, 31) if _q(d, f'{dev}/s/fuse/{k}/engaged')]
        if fuses:
            s['fuses_engaged'] = fuses
        try:
            from pyjoulescope_driver.program import version_to_str
            s['firmware'] = version_to_str(d.query(f'{dev}/c/fw/version'))
            s['fpga'] = version_to_str(d.query(f'{dev}/s/fpga/version'))
        except Exception:
            pass
    return s


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

class Stats:
    """Stream on-instrument statistics blocks into a queue.

    JS220/JS320: any rate from 1 Hz to 1 kHz or so (s/stats/scnt).
    JS110: fixed 2 Hz sensor statistics (no standard deviation).
    """

    def __init__(self, d, dev, frequency):
        self.d, self.dev = d, dev
        self.q = queue.Queue()
        self.removed = False
        if is_js110(dev):
            self.frequency = 2.0
            self.topic = f'{dev}/s/sstats/value'
            self.ctrl = None
        else:
            scnt = max(1, int(round(1_000_000 / frequency)))
            self.frequency = 1_000_000 / scnt
            self.topic = f'{dev}/s/stats/value'
            self.ctrl = f'{dev}/s/stats/ctrl'
            self._scnt_prev = _q(d, f'{dev}/s/stats/scnt')
            d.publish(f'{dev}/s/stats/scnt', scnt)

    def _on_value(self, topic, value):
        self.q.put(value)

    def _on_remove(self, topic, value):
        if value == self.dev:
            self.removed = True

    def __enter__(self):
        self.d.subscribe('@/!remove', 'pub', self._on_remove)
        self.d.subscribe(self.topic, 'pub', self._on_value)
        if self.ctrl:
            self.d.publish(self.ctrl, 1)
        return self

    def __exit__(self, *exc):
        try:
            if self.ctrl and not self.removed:
                self.d.publish(self.ctrl, 0)
                if self._scnt_prev:  # leave the instrument as we found it
                    self.d.publish(f'{self.dev}/s/stats/scnt', self._scnt_prev)
            self.d.unsubscribe(self.topic, self._on_value)
            self.d.unsubscribe('@/!remove', self._on_remove)
        except Exception:
            pass

    def get(self):
        """Return the next block as a flat dict."""
        deadline = time.monotonic() + STATS_TIMEOUT
        while True:
            if self.removed:
                raise JsError(f'{self.dev} was unplugged')
            try:
                v = self.q.get(timeout=0.1)
                break
            except queue.Empty:
                if time.monotonic() > deadline:
                    raise JsError('No statistics received from the instrument '
                                  f'within {STATS_TIMEOUT} s') from None
        t = v['time']
        fs = t.get('sample_freq', {}).get('value', 2_000_000)
        s0, s1 = t['samples']['value']
        sig = v['signals']

        def g(name, field):
            try:
                return float(sig[name][field]['value'])
            except (KeyError, TypeError):
                return None

        return {
            'dt': (s1 - s0) / fs,
            'i_avg': g('current', 'avg'), 'i_min': g('current', 'min'),
            'i_max': g('current', 'max'), 'i_std': g('current', 'std'),
            'v_avg': g('voltage', 'avg'), 'v_min': g('voltage', 'min'),
            'v_max': g('voltage', 'max'),
            'p_avg': g('power', 'avg'), 'p_min': g('power', 'min'),
            'p_max': g('power', 'max'),
        }


def summarize(blocks):
    dur = sum(b['dt'] for b in blocks)
    if not blocks or dur <= 0:
        raise JsError('no data collected')

    def wavg(k):
        return sum(b[k] * b['dt'] for b in blocks) / dur

    energy = sum(b['p_avg'] * b['dt'] for b in blocks)
    charge = sum(b['i_avg'] * b['dt'] for b in blocks)
    return {
        'duration_s': round(dur, 6),
        'blocks': len(blocks),
        'current_A': {'avg': wavg('i_avg'), 'min': min(b['i_min'] for b in blocks),
                      'max': max(b['i_max'] for b in blocks)},
        'voltage_V': {'avg': wavg('v_avg'), 'min': min(b['v_min'] for b in blocks),
                      'max': max(b['v_max'] for b in blocks)},
        'power_W': {'avg': wavg('p_avg'), 'min': min(b['p_min'] for b in blocks),
                    'max': max(b['p_max'] for b in blocks)},
        'energy_J': energy,
        'charge_C': charge,
        'energy_mWh': energy / 3.6,
        'charge_mAh': charge / 3.6,
    }


def collect(st, duration, series=None, t0=0.0):
    blocks, t = [], t0
    st.get()  # discard the first block: it may span the configuration change
    while sum(b['dt'] for b in blocks) < duration - 1e-9:
        b = st.get()
        blocks.append(b)
        t += b['dt']
        if series is not None:
            series.write(f"{t:.6f},{b['i_avg']:.9g},{b['i_min']:.9g},{b['i_max']:.9g},"
                         f"{b['v_avg']:.6g},{b['p_avg']:.9g}\n")
    return blocks


SERIES_HEADER = 'time_s,current_avg_A,current_min_A,current_max_A,voltage_avg_V,power_avg_W\n'


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_info(args):
    with Driver() as d:
        paths = d.find_devices(args.device, brand=BRAND)
        devices = []
        for dev in paths:
            try:
                d.open(dev, 'restore')
            except Exception as ex:
                devices.append({'device': str(dev), 'model': dev.model,
                                'serial_number': dev.serial_number,
                                'error': f'could not open: {ex}'})
                continue
            try:
                devices.append(state(d, dev))
            finally:
                d.close(dev)
    return emit({'devices': devices, 'count': len(devices)})


def cmd_measure(args):
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            out = {'device': str(dev), 'output': power_get(d, dev)}
            series = None
            if args.series:
                series = open(args.series, 'w')
                series.write(SERIES_HEADER)
            try:
                with Stats(d, dev, args.frequency) as st:
                    blocks = collect(st, args.duration, series)
            finally:
                if series:
                    series.close()
            out.update(summarize(blocks))
            out['statistics_frequency_Hz'] = st.frequency
            if args.series:
                out['series_csv'] = os.path.abspath(args.series)
            if out['output'] == 'off':
                out['warning'] = ('Current path is OFF: the target is unpowered and '
                                  'current reads 0. Run "power on" first.')
        finally:
            d.close(dev)
    return emit(out)


def cmd_power(args):
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            out = {'device': str(dev), 'before': power_get(d, dev)}
            if args.action == 'status':
                out['output'] = out.pop('before')
                return emit(out)
            if args.action == 'on':
                power_set(d, dev, True)
            elif args.action == 'off':
                power_set(d, dev, False)
            elif args.action == 'cycle':
                power_set(d, dev, False)
                time.sleep(args.off_time)
                power_set(d, dev, True)
                out['off_time_s'] = args.off_time
            time.sleep(0.05)
            out['output'] = power_get(d, dev)
            if args.measure:
                with Stats(d, dev, args.frequency) as st:
                    out['after'] = summarize(collect(st, args.measure))
        finally:
            d.close(dev)
    return emit(out)


def cmd_wait(args):
    key = {'current': 'i_avg', 'voltage': 'v_avg', 'power': 'p_avg'}[args.signal]
    if (args.below is None) == (args.above is None):
        raise JsError('give exactly one of --below or --above')
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            elapsed = held = 0.0
            last = None
            met = False
            with Stats(d, dev, args.frequency) as st:
                st.get()
                while elapsed < args.timeout:
                    b = st.get()
                    elapsed += b['dt']
                    last = b[key]
                    ok = last < args.below if args.below is not None else last > args.above
                    held = held + b['dt'] if ok else 0.0
                    if held >= args.hold - 1e-9:
                        met = True
                        break
            out = {'device': str(dev), 'met': met, 'signal': args.signal,
                   'condition': f'< {args.below}' if args.below is not None else f'> {args.above}',
                   'hold_s': args.hold, 'elapsed_s': round(elapsed, 4), 'last_value': last}
        finally:
            d.close(dev)
    return emit(out, 0 if met else 2)


def cmd_record(args):
    from pyjoulescope_driver.record import Record
    filename = args.filename
    if not filename.endswith('.jls'):
        filename += '.jls'
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            output = power_get(d, dev)
            fs_max = 2_000_000 if is_js110(dev) else 1_000_000
            fs = min(int(args.frequency), fs_max)
            d.publish(f'{dev}/h/fs', fs)
            wr = Record(d, dev, args.signals)
            removed = []
            fn = lambda topic, value: removed.append(value) if value == dev else None  # noqa: E731
            d.subscribe('@/!remove', 'pub', fn)
            wr.open(filename, user_data=[[0, args.note]] if args.note else None)
            try:
                t_stop = time.monotonic() + args.duration
                while time.monotonic() < t_stop and not removed:
                    time.sleep(0.01)
            except KeyboardInterrupt:
                pass
            finally:
                wr.close()
                d.unsubscribe('@/!remove', fn)
            if removed:
                raise JsError(f'{dev} was unplugged during the recording')
        finally:
            d.close(dev)
    out = {'device': str(dev), 'file': os.path.abspath(filename),
           'size_MB': round(os.path.getsize(filename) / 1e6, 3),
           'sampling_frequency_Hz': fs, 'output': output}
    if output == 'off':
        out['warning'] = 'Current path was OFF during the recording.'
    out.update(jls_summary(filename))
    return emit(out)


def jls_summary(filename, start=None, end=None, csv=None, points=1000):
    import numpy as np
    from pyjls import Reader
    out = {'signals': {}}
    columns = {}
    with Reader(filename) as r:
        for sid, s in r.signals.items():
            if sid == 0 or not s.length or not s.sample_rate:
                continue  # signal 0 holds global annotations
            fs = s.sample_rate
            k0 = 0 if start is None else max(0, int(start * fs))
            k1 = s.length if end is None else min(s.length, int(end * fs))
            n = k1 - k0
            if n <= 0:
                continue
            info = {'units': s.units, 'sample_rate_Hz': fs, 'samples': n,
                    'duration_s': n / fs}
            try:
                mean, std, vmin, vmax = [float(x) for x in r.fsr_statistics(sid, k0, n, 1)[0]]
                info.update(avg=mean, std=std, min=vmin, max=vmax)
                if s.name == 'current':
                    info['charge_C'] = mean * n / fs
                elif s.name == 'power':
                    info['energy_J'] = mean * n / fs
            except Exception as ex:  # such as u1/u4 digital signals
                info['note'] = f'no statistics: {ex}'
            else:
                if csv:
                    inc = max(1, n // points)
                    cnt = n // inc
                    data = r.fsr_statistics(sid, k0, inc, cnt)
                    columns[s.name] = (fs, inc, k0, np.asarray(data))
            out['signals'][s.name] = info
    sig = out['signals']
    if 'current' in sig and 'voltage' in sig and 'power' not in sig and 'avg' in sig['current']:
        out['note'] = ('power was not recorded; avg(V)*avg(I) only approximates it. '
                       'Record with --signals current,voltage,power for exact energy.')
        out['power_avg_W_approx'] = sig['current']['avg'] * sig['voltage']['avg']
    if csv and columns:
        names = list(columns)
        rows = min(len(c[3]) for c in columns.values())
        fs, inc, k0, _ = columns[names[0]]
        with open(csv, 'w') as f:
            f.write('time_s,' + ','.join(f'{n}_avg,{n}_min,{n}_max' for n in names) + '\n')
            for i in range(rows):
                t = (k0 + (i + 0.5) * inc) / fs
                cells = []
                for n in names:
                    m = columns[n][3][i]
                    cells += [f'{m[0]:.9g}', f'{m[2]:.9g}', f'{m[3]:.9g}']
                f.write(f'{t:.6f},' + ','.join(cells) + '\n')
        out['csv'] = os.path.abspath(csv)
        out['csv_rows'] = rows
    return out


def cmd_jls(args):
    out = {'file': os.path.abspath(args.filename)}
    out.update(jls_summary(args.filename, args.start, args.end, args.csv, args.points))
    return emit(out)


def gpi_read(d, dev):
    return d.publish_and_wait(f'{dev}/s/gpi/+/!req', 0, f'{dev}/s/gpi/+/!value', timeout=1.0)


def cmd_gpo(args):
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            if is_js110(dev):
                d.publish(f'{dev}/s/gpo/{args.index}/value', args.value)
            else:
                d.publish(f"{dev}/s/gpo/+/{'!set' if args.value else '!clr'}", 1 << args.index)
            out = {'device': str(dev), f'gpo{args.index}': args.value}
        finally:
            d.close(dev)
    return emit(out)


def cmd_gpi(args):
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            v = int(gpi_read(d, dev))
            n = 2 if is_js110(dev) else 4
            out = {'device': str(dev), 'value': v}
            out.update({f'gpi{k}': (v >> k) & 1 for k in range(n)})
        finally:
            d.close(dev)
    return emit(out)


def _parse_value(value):
    try:
        return int(value, 0)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def cmd_set(args):
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            done = {}
            for pair in args.pairs:
                if '=' not in pair:
                    raise JsError(f'expected topic=value, got "{pair}"')
                topic, value = pair.split('=', 1)
                d.publish(f'{dev}/{topic}', _parse_value(value))
                done[topic] = _q(d, f'{dev}/{topic}', value)
        finally:
            d.close(dev)
    return emit({'device': str(dev), 'set': done})


def cmd_get(args):
    with Driver() as d:
        dev = open_device(d, args.device)
        try:
            out = {t: _q(d, f'{dev}/{t}', 'ERROR: query failed') for t in args.topics}
        finally:
            d.close(dev)
    return emit({'device': str(dev), 'values': out})


# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--device', '-d', help='Serial number, model (js220) or path. '
                                          'Only needed with more than one Joulescope.')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('info', help='List connected Joulescopes and their state.').set_defaults(fn=cmd_info)

    s = sub.add_parser('measure', help='Average current/voltage/power, energy and charge over a duration.')
    s.add_argument('--duration', '-t', type=float, default=1.0, help='Seconds (default 1).')
    s.add_argument('--frequency', '-f', type=float, default=10.0,
                   help='Statistics blocks per second (default 10). Sets the series resolution; '
                        'min/max always see every 1 Msps sample.')
    s.add_argument('--series', help='Also write one CSV row per statistics block to this file.')
    s.set_defaults(fn=cmd_measure)

    s = sub.add_parser('power', help='Switch the current path to the target on or off.')
    s.add_argument('action', choices=['on', 'off', 'cycle', 'status'])
    s.add_argument('--off-time', type=float, default=1.0, help='Seconds off for cycle (default 1).')
    s.add_argument('--measure', type=float, help='After switching, measure for this many seconds.')
    s.add_argument('--frequency', '-f', type=float, default=10.0)
    s.set_defaults(fn=cmd_power)

    s = sub.add_parser('wait', help='Block until a signal stays below/above a threshold.')
    s.add_argument('--signal', choices=['current', 'voltage', 'power'], default='current')
    s.add_argument('--below', type=float, help='SI units (A, V, W).')
    s.add_argument('--above', type=float, help='SI units (A, V, W).')
    s.add_argument('--hold', type=float, default=0.5, help='Seconds the condition must hold (default 0.5).')
    s.add_argument('--timeout', type=float, default=30.0, help='Seconds before giving up (exit code 2).')
    s.add_argument('--frequency', '-f', type=float, default=20.0)
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser('record', help='Record full-rate samples to a JLS file (opens in the Joulescope UI).')
    s.add_argument('filename')
    s.add_argument('--duration', '-t', type=float, default=5.0, help='Seconds (default 5).')
    s.add_argument('--frequency', '-f', type=float, default=100_000,
                   help='Sample rate in Hz (default 100000; max 1000000, JS110 2000000). '
                        '1, 2, 5 steps per decade.')
    s.add_argument('--signals', default='current,voltage,power',
                   help='Comma list: current,voltage,power,current_range,gpi[0..3],trigger_in.')
    s.add_argument('--note', help='Note stored in the file.')
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser('jls', help='Summarize a JLS recording, optionally export a downsampled CSV.')
    s.add_argument('filename')
    s.add_argument('--start', type=float, help='Window start in seconds from the file start.')
    s.add_argument('--end', type=float, help='Window end in seconds from the file start.')
    s.add_argument('--csv', help='Write avg/min/max per bucket to this CSV.')
    s.add_argument('--points', type=int, default=1000, help='CSV rows (default 1000).')
    s.set_defaults(fn=cmd_jls)

    s = sub.add_parser('gpo', help='Drive a general purpose output.')
    s.add_argument('index', type=int, choices=[0, 1])
    s.add_argument('value', type=int, choices=[0, 1])
    s.set_defaults(fn=cmd_gpo)

    sub.add_parser('gpi', help='Read the general purpose inputs.').set_defaults(fn=cmd_gpi)

    s = sub.add_parser('set', help='Publish raw driver topics: topic=value ...')
    s.add_argument('pairs', nargs='+')
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser('get', help='Query raw driver topics.')
    s.add_argument('topics', nargs='+')
    s.set_defaults(fn=cmd_get)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except JsError as ex:
        return emit({'error': str(ex)}, 1)
    except Exception as ex:  # keep stdout machine readable
        return emit({'error': f'{type(ex).__name__}: {ex}'}, 1)


if __name__ == '__main__':
    sys.exit(main())

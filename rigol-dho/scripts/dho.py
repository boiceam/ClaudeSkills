#!/usr/bin/env python3
"""RIGOL DHO800/DHO900 oscilloscope helper for agents (SCPI over LAN).

Standard library only, so plain python3 is enough:

    python3 dho.py connect 192.168.1.50     # test and remember the address
    python3 dho.py status
    python3 dho.py measure VPP FREQ --source CHAN1
    python3 dho.py screenshot shot.png

Every command prints one JSON object on stdout (errors too, with "error").
Exit codes: 0 ok, 1 error, 2 condition not met (single --wait timed out).

The address comes from --host, then $DHO_HOST, then the file written by
"connect" (~/.config/rigol-dho/host).
"""

import argparse
import json
import math
import os
import socket
import struct
import sys
import time

PORT = 5555
CONFIG = os.path.expanduser('~/.config/rigol-dho/host')
INVALID = 9.0e37  # the scope returns 9.9E37 for a measurement it cannot make
ANALOG = ['CHAN1', 'CHAN2', 'CHAN3', 'CHAN4']


class DhoError(Exception):
    pass


def emit(obj, code=0):
    print(json.dumps(obj, indent=2))
    return code


class Scope:
    def __init__(self, host, port=PORT, timeout=5.0):
        self.host, self.port = host, port
        try:
            self.s = socket.create_connection((host, port), timeout=timeout)
        except OSError as ex:
            raise DhoError(f'Cannot reach the scope at {host}:{port} ({ex}). Check the '
                           'network cable and the address under Utility > IO on the scope.') from ex
        self.s.settimeout(timeout)
        self.buf = b''

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- transport ---------------------------------------------------------
    def write(self, cmd):
        self.s.sendall(cmd.encode() + b'\n')

    def _fill(self):
        try:
            chunk = self.s.recv(1 << 16)
        except socket.timeout:
            raise DhoError('Timed out waiting for the scope to answer. The command may be '
                           'unsupported on this model/firmware, or the scope is busy.') from None
        if not chunk:
            raise DhoError('The scope closed the connection.')
        self.buf += chunk

    def _take(self, n):
        while len(self.buf) < n:
            self._fill()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _line(self):
        while b'\n' not in self.buf:
            self._fill()
        line, self.buf = self.buf.split(b'\n', 1)
        return line.decode(errors='replace').strip()

    def query(self, cmd):
        self.write(cmd)
        return self._line()

    def query_block(self, cmd, timeout=None):
        """Query an IEEE 488.2 definite-length block: #<n><len digits><data>\\n."""
        old = self.s.gettimeout()
        if timeout:
            self.s.settimeout(timeout)
        try:
            self.write(cmd)
            head = self._take(1)
            if head != b'#':
                rest = self._line()
                raise DhoError(f'Expected a binary block from "{cmd}", got: {(head.decode(errors="replace") + rest)[:120]}')
            ndigits = int(self._take(1))
            length = int(self._take(ndigits))
            data = self._take(length)
            # trailing terminator
            if self.buf[:1] == b'\n':
                self.buf = self.buf[1:]
            else:
                try:
                    self.s.settimeout(0.2)
                    self._take(1)
                except DhoError:
                    pass
            return data
        finally:
            self.s.settimeout(old)

    def qfloat(self, cmd):
        try:
            v = float(self.query(cmd))
        except ValueError:
            return None
        return None if abs(v) > INVALID or math.isnan(v) else v

    def errors(self):
        """Drain the SCPI error queue; return a list of error strings."""
        out = []
        for _ in range(20):
            e = self.query(':SYST:ERR?')
            if e.startswith('0,') or e.startswith('+0,') or e == '0':
                break
            out.append(e)
        return out

    def set(self, *cmds):
        for c in cmds:
            self.write(c)
        self.query('*OPC?')


def resolve_host(args):
    host = args.host or os.environ.get('DHO_HOST')
    if not host and os.path.exists(CONFIG):
        host = open(CONFIG).read().strip()
    if not host:
        raise DhoError('No scope address known. Run "connect <ip>" once (the address is under '
                       'Utility > IO on the scope), or pass --host.')
    return host


def open_scope(args, timeout=5.0):
    return Scope(resolve_host(args), args.port, timeout)


def finish(sc, out):
    errs = sc.errors()
    if errs:
        out['scpi_errors'] = errs
    return emit(out, 1 if errs and out.get('strict') else 0)


def chan(name):
    """Normalize 1 / ch1 / CHAN1 / channel1 / D3 / MATH1 to the SCPI name."""
    s = str(name).upper()
    if s.isdigit():
        return f'CHAN{s}'
    for p in ('CHANNEL', 'CHAN', 'CH'):
        if s.startswith(p) and s[len(p):].isdigit():
            return f'CHAN{s[len(p):]}'
    return s


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_connect(args):
    with Scope(args.address, args.port) as sc:
        idn = sc.query('*IDN?')
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, 'w') as f:
        f.write(args.address + '\n')
    return emit({'host': args.address, 'idn': idn, 'saved_to': CONFIG})


def read_state(sc):
    idn = sc.query('*IDN?')
    parts = [p.strip() for p in idn.split(',')]
    st = {
        'idn': idn,
        'model': parts[1] if len(parts) > 1 else None,
        'trigger_status': sc.query(':TRIG:STAT?'),
        'timebase_s_per_div': sc.qfloat(':TIM:MAIN:SCAL?'),
        'timebase_offset_s': sc.qfloat(':TIM:MAIN:OFFS?'),
        'sample_rate_Sa_s': sc.qfloat(':ACQ:SRAT?'),
        'memory_depth_pts': sc.qfloat(':ACQ:MDEP?'),
        'acquire_type': sc.query(':ACQ:TYPE?'),
        'channels': {},
    }
    nchan = 2 if (st['model'] or '').rstrip('S')[-1:] == '2' else 4
    for n in range(1, nchan + 1):
        on = sc.query(f':CHAN{n}:DISP?') in ('1', 'ON')
        ch = {'on': on}
        if on:
            ch.update({
                'scale_V_per_div': sc.qfloat(f':CHAN{n}:SCAL?'),
                'offset_V': sc.qfloat(f':CHAN{n}:OFFS?'),
                'coupling': sc.query(f':CHAN{n}:COUP?'),
                'probe_ratio': sc.qfloat(f':CHAN{n}:PROB?'),
                'bw_limit': sc.query(f':CHAN{n}:BWL?'),
            })
        st['channels'][f'CHAN{n}'] = ch
    mode = sc.query(':TRIG:MODE?')
    trig = {'mode': mode, 'sweep': sc.query(':TRIG:SWE?')}
    if mode.upper().startswith('EDGE'):
        trig.update({'source': sc.query(':TRIG:EDGE:SOUR?'),
                     'slope': sc.query(':TRIG:EDGE:SLOP?'),
                     'level_V': sc.qfloat(':TRIG:EDGE:LEV?')})
    st['trigger'] = trig
    if (st['model'] or '').endswith('S'):
        st['afg'] = {
            'output': sc.query(':SOUR:OUTP:STAT?') in ('1', 'ON'),
            'function': sc.query(':SOUR:FUNC?'),
            'frequency_Hz': sc.qfloat(':SOUR:FREQ?'),
            'amplitude_Vpp': sc.qfloat(':SOUR:VOLT:AMPL?'),
            'offset_V': sc.qfloat(':SOUR:VOLT:OFFS?'),
        }
    return st


def cmd_status(args):
    with open_scope(args) as sc:
        out = {'host': sc.host}
        out.update(read_state(sc))
        return finish(sc, out)


def cmd_simple(scpi):
    def fn(args):
        with open_scope(args) as sc:
            sc.set(scpi)
            if scpi == ':AUT':
                time.sleep(3)  # autoset takes a few seconds
            return finish(sc, {'sent': scpi, 'trigger_status': sc.query(':TRIG:STAT?')})
    return fn


def cmd_single(args):
    with open_scope(args) as sc:
        sc.set(':SING')
        out = {'sent': ':SING'}
        code = 0
        if args.wait is not None:
            t0 = time.monotonic()
            time.sleep(0.2)
            stat = sc.query(':TRIG:STAT?')
            while stat != 'STOP' and time.monotonic() - t0 < args.wait:
                time.sleep(0.05)
                stat = sc.query(':TRIG:STAT?')
            out['triggered'] = stat == 'STOP'
            out['elapsed_s'] = round(time.monotonic() - t0, 3)
            if stat != 'STOP':
                code = 2
                out['note'] = ('No trigger within the timeout; the scope is still armed. '
                               'Check trigger source/level, or use "force".')
        out['trigger_status'] = sc.query(':TRIG:STAT?')
        errs = sc.errors()
        if errs:
            out['scpi_errors'] = errs
        return emit(out, code)


def _apply(sc, pairs):
    sent = []
    for cmd, value in pairs:
        if value is not None:
            sent.append(f'{cmd} {value}')
    if sent:
        sc.set(*sent)
    return sent


def cmd_channel(args):
    n = chan(args.channel)
    with open_scope(args) as sc:
        onoff = 'ON' if args.on else 'OFF' if args.off else None
        sent = _apply(sc, [
            (f':{n}:DISP', onoff),
            (f':{n}:PROB', args.probe),          # before scale: scale range depends on it
            (f':{n}:COUP', args.coupling),
            (f':{n}:BWL', args.bwlimit),
            (f':{n}:SCAL', args.scale),
            (f':{n}:OFFS', args.offset),
            (f':{n}:INV', None if args.invert is None else args.invert.upper()),
        ])
        out = {'sent': sent, n: {
            'on': sc.query(f':{n}:DISP?') in ('1', 'ON'),
            'scale_V_per_div': sc.qfloat(f':{n}:SCAL?'),
            'offset_V': sc.qfloat(f':{n}:OFFS?'),
            'coupling': sc.query(f':{n}:COUP?'),
            'probe_ratio': sc.qfloat(f':{n}:PROB?'),
            'bw_limit': sc.query(f':{n}:BWL?'),
        }}
        return finish(sc, out)


def cmd_timebase(args):
    with open_scope(args) as sc:
        sent = _apply(sc, [(':TIM:MAIN:SCAL', args.scale), (':TIM:MAIN:OFFS', args.offset)])
        return finish(sc, {'sent': sent,
                           'timebase_s_per_div': sc.qfloat(':TIM:MAIN:SCAL?'),
                           'timebase_offset_s': sc.qfloat(':TIM:MAIN:OFFS?'),
                           'sample_rate_Sa_s': sc.qfloat(':ACQ:SRAT?')})


def cmd_trigger(args):
    with open_scope(args) as sc:
        pairs = []
        if any(v is not None for v in (args.source, args.slope, args.level)):
            pairs.append((':TRIG:MODE', 'EDGE'))
        pairs += [(':TRIG:EDGE:SOUR', None if args.source is None else chan(args.source)),
                  (':TRIG:EDGE:SLOP', args.slope),
                  (':TRIG:EDGE:LEV', args.level),
                  (':TRIG:SWE', args.sweep)]
        sent = _apply(sc, pairs)
        mode = sc.query(':TRIG:MODE?')
        out = {'sent': sent, 'mode': mode, 'sweep': sc.query(':TRIG:SWE?'),
               'trigger_status': sc.query(':TRIG:STAT?')}
        if mode.upper().startswith('EDGE'):
            out.update({'source': sc.query(':TRIG:EDGE:SOUR?'),
                        'slope': sc.query(':TRIG:EDGE:SLOP?'),
                        'level_V': sc.qfloat(':TRIG:EDGE:LEV?')})
        return finish(sc, out)


def cmd_acquire(args):
    with open_scope(args) as sc:
        sent = _apply(sc, [(':ACQ:TYPE', args.type), (':ACQ:AVER', args.averages),
                           (':ACQ:MDEP', args.depth)])
        return finish(sc, {'sent': sent, 'acquire_type': sc.query(':ACQ:TYPE?'),
                           'memory_depth_pts': sc.qfloat(':ACQ:MDEP?'),
                           'sample_rate_Sa_s': sc.qfloat(':ACQ:SRAT?')})


DEFAULT_ITEMS = ['VPP', 'VMAX', 'VMIN', 'VAVG', 'VRMS', 'FREQ', 'PER']


def cmd_measure(args):
    items = [i.upper() for i in (args.items or DEFAULT_ITEMS)]
    src = chan(args.source)
    srcs = src + (f',{chan(args.source2)}' if args.source2 else '')
    with open_scope(args) as sc:
        out = {'source': srcs, 'trigger_status': sc.query(':TRIG:STAT?'), 'values': {}}
        for item in items:
            v = sc.qfloat(f':MEAS:ITEM? {item},{srcs}')
            for _ in range(10):  # a new item, or a fresh deep capture, takes a moment to compute
                if v is not None:
                    break
                time.sleep(0.3)
                v = sc.qfloat(f':MEAS:ITEM? {item},{srcs}')
            out['values'][item] = v
        if args.clear:
            sc.set(':MEAS:CLE')
        if any(v is None for v in out['values'].values()):
            out['note'] = ('null = the scope could not make that measurement (no signal, '
                           'clipped, channel off, or less than one full cycle on screen).')
        return finish(sc, out)


def cmd_screenshot(args):
    fmt = os.path.splitext(args.filename)[1].lstrip('.').upper() or 'PNG'
    fmt = {'JPEG': 'JPG'}.get(fmt, fmt)
    if fmt not in ('PNG', 'BMP', 'JPG'):
        raise DhoError('Use a .png, .bmp or .jpg filename.')
    with open_scope(args) as sc:
        data = sc.query_block(f':DISP:DATA? {fmt}', timeout=20)
        with open(args.filename, 'wb') as f:
            f.write(data)
        return finish(sc, {'file': os.path.abspath(args.filename), 'bytes': len(data), 'format': fmt})


def cmd_waveform(args):
    src = chan(args.source)
    mode = args.mode.upper()
    with open_scope(args, timeout=10) as sc:
        out = {'source': src, 'mode': mode}
        if mode == 'RAW':
            stat = sc.query(':TRIG:STAT?')
            if stat != 'STOP':
                if not args.stop:
                    raise DhoError(f'RAW reads the acquisition memory and needs the scope stopped '
                                   f'(now {stat}). Stop it first ("stop" or "single --wait"), '
                                   'or pass --stop.')
                sc.set(':STOP')
                out['stopped_by_this_command'] = True
        sc.set(f':WAV:SOUR {src}', f':WAV:MODE {mode}', ':WAV:FORM WORD')
        pre = sc.query(':WAV:PRE?').split(',')
        if len(pre) < 10:
            raise DhoError(f'Unexpected preamble: {pre}')
        total = int(float(pre[2]))
        xinc, xorg, xref = float(pre[4]), float(pre[5]), float(pre[6])
        yinc, yorg, yref = float(pre[7]), float(pre[8]), float(pre[9])
        first = max(1, args.start or 1)
        last = min(total, args.end or total)
        if last < first:
            raise DhoError(f'Empty range: {first}..{last} of {total} points')
        chunk = max(1, min(args.chunk, 100_000))  # the scope returns at most 100k points per read
        raw = []
        pos = first
        while pos <= last:
            stop = min(last, pos + chunk - 1)
            sc.set(f':WAV:STAR {pos}', f':WAV:STOP {stop}')
            data = sc.query_block(':WAV:DATA?', timeout=30)
            n = len(data) // 2
            if n == 0:
                break
            raw.extend(struct.unpack(f'<{n}H', data[:2 * n]))
            pos += n
        if not raw:
            raise DhoError('The scope returned no waveform data (is the channel on?).')
        step = max(1, args.decimate)
        volts = [(r - yorg - yref) * yinc for r in raw[::step]]
        dt = xinc * step
        t0 = xorg + (first - 1 - xref) * xinc
        vmin, vmax = min(volts), max(volts)
        mean = sum(volts) / len(volts)
        out.update({
            'points_in_memory': total, 'points_read': len(raw), 'points_written': len(volts),
            'sample_interval_s': dt, 't_start_s': t0, 't_end_s': t0 + dt * (len(volts) - 1),
            'min_V': vmin, 'max_V': vmax, 'mean_V': mean, 'vpp_V': vmax - vmin,
            'rms_V': math.sqrt(sum(v * v for v in volts) / len(volts)),
        })
        if raw and (min(raw) == 0 or max(raw) == 65535):
            out['warning'] = 'Samples hit the ADC limit: the signal is clipped. Increase V/div.'
        if args.csv:
            with open(args.csv, 'w') as f:
                f.write('time_s,voltage_V\n')
                for i, v in enumerate(volts):
                    f.write(f'{t0 + i * dt:.9g},{v:.6g}\n')
            out['csv'] = os.path.abspath(args.csv)
        return finish(sc, out)


def cmd_afg(args):
    with open_scope(args) as sc:
        func = args.function.upper() if args.function else None
        sent = _apply(sc, [
            (':SOUR:FUNC', func),
            (':SOUR:FREQ', args.frequency),
            (':SOUR:VOLT:AMPL', args.amplitude),
            (':SOUR:VOLT:OFFS', args.offset),
            (':SOUR:FUNC:SQU:DUTY', args.duty),
            (':SOUR:FUNC:RAMP:SYMM', args.symmetry),
            (':SOUR:OUTP:STAT', 'ON' if args.on else 'OFF' if args.off else None),
        ])
        return finish(sc, {'sent': sent,
                           'output': sc.query(':SOUR:OUTP:STAT?') in ('1', 'ON'),
                           'function': sc.query(':SOUR:FUNC?'),
                           'frequency_Hz': sc.qfloat(':SOUR:FREQ?'),
                           'amplitude_Vpp': sc.qfloat(':SOUR:VOLT:AMPL?'),
                           'offset_V': sc.qfloat(':SOUR:VOLT:OFFS?')})


def cmd_scpi(args):
    with open_scope(args) as sc:
        results = []
        for c in args.commands:
            if '?' in c:
                results.append({'command': c, 'reply': sc.query(c)})
            else:
                sc.set(c)
                results.append({'command': c})
        return finish(sc, {'results': results})


# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', help='Scope IP address or hostname.')
    p.add_argument('--port', type=int, default=PORT, help=f'SCPI socket port (default {PORT}).')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('connect', help='Test an address and remember it.')
    s.add_argument('address')
    s.set_defaults(fn=cmd_connect)

    sub.add_parser('status', help='Identity plus channel, timebase, trigger and AFG setup.').set_defaults(fn=cmd_status)
    sub.add_parser('run', help='Start acquiring.').set_defaults(fn=cmd_simple(':RUN'))
    sub.add_parser('stop', help='Stop acquiring.').set_defaults(fn=cmd_simple(':STOP'))
    sub.add_parser('force', help='Force a trigger.').set_defaults(fn=cmd_simple(':TFOR'))
    sub.add_parser('autoset', help='Let the scope pick scales (changes many settings).').set_defaults(fn=cmd_simple(':AUT'))
    sub.add_parser('clear', help='Clear the displayed waveforms.').set_defaults(fn=cmd_simple(':CLE'))

    s = sub.add_parser('single', help='Arm a single-shot capture.')
    s.add_argument('--wait', type=float, help='Wait up to this many seconds for the trigger (exit 2 if none).')
    s.set_defaults(fn=cmd_single)

    s = sub.add_parser('channel', help='Show or change one analog channel.')
    s.add_argument('channel', help='1-4')
    g = s.add_mutually_exclusive_group()
    g.add_argument('--on', action='store_true')
    g.add_argument('--off', action='store_true')
    s.add_argument('--scale', type=float, help='Volts per division.')
    s.add_argument('--offset', type=float, help='Volts.')
    s.add_argument('--coupling', choices=['DC', 'AC', 'GND'])
    s.add_argument('--probe', type=float, help='Probe ratio, e.g. 1 or 10.')
    s.add_argument('--bwlimit', choices=['OFF', '20M'])
    s.add_argument('--invert', choices=['on', 'off'])
    s.set_defaults(fn=cmd_channel)

    s = sub.add_parser('timebase', help='Show or change the horizontal scale.')
    s.add_argument('--scale', type=float, help='Seconds per division.')
    s.add_argument('--offset', type=float, help='Seconds.')
    s.set_defaults(fn=cmd_timebase)

    s = sub.add_parser('trigger', help='Show or change the edge trigger.')
    s.add_argument('--source', help='1-4, EXT, or D0-D15.')
    s.add_argument('--slope', choices=['POS', 'NEG', 'RFAL'])
    s.add_argument('--level', type=float, help='Volts.')
    s.add_argument('--sweep', choices=['AUTO', 'NORM', 'SING'])
    s.set_defaults(fn=cmd_trigger)

    s = sub.add_parser('acquire', help='Show or change memory depth and acquisition type.')
    s.add_argument('--depth', help='AUTO, 1k, 10k, 100k, 1M, 5M, 10M, 25M, 50M.')
    s.add_argument('--type', choices=['NORM', 'PEAK', 'AVER', 'ULTR'])
    s.add_argument('--averages', type=int, help='Power of 2, 2 to 65536 (type AVER).')
    s.set_defaults(fn=cmd_acquire)

    s = sub.add_parser('measure', help='Read automatic measurements.')
    s.add_argument('items', nargs='*', help=f'Default: {" ".join(DEFAULT_ITEMS)}. See references/scpi.md for all.')
    s.add_argument('--source', default='CHAN1', help='1-4, MATH1-4, D0-D15.')
    s.add_argument('--source2', help='Second source for delay/phase items.')
    s.add_argument('--clear', action='store_true',
                   help='Afterwards remove ALL items from the on-screen Result panel.')
    s.set_defaults(fn=cmd_measure)

    s = sub.add_parser('screenshot', help='Save the screen as .png, .bmp or .jpg.')
    s.add_argument('filename')
    s.set_defaults(fn=cmd_screenshot)

    s = sub.add_parser('waveform', help='Download waveform samples; prints statistics, optional CSV.')
    s.add_argument('--source', default='CHAN1', help='1-4 or MATH1-4.')
    s.add_argument('--mode', default='NORM', choices=['NORM', 'RAW', 'norm', 'raw'],
                   help='NORM: the ~1000 points on screen. RAW: full memory (scope must be stopped).')
    s.add_argument('--stop', action='store_true', help='With RAW: stop the scope first if it is running.')
    s.add_argument('--start', type=int, help='First memory point (1-based).')
    s.add_argument('--end', type=int, help='Last memory point.')
    s.add_argument('--decimate', type=int, default=1, help='Keep every Nth sample.')
    s.add_argument('--chunk', type=int, default=100_000, help='Points per read (default and maximum 100000).')
    s.add_argument('--csv', help='Write time_s,voltage_V rows to this file.')
    s.set_defaults(fn=cmd_waveform)

    s = sub.add_parser('afg', help='Built-in signal generator (S models only).')
    g = s.add_mutually_exclusive_group()
    g.add_argument('--on', action='store_true')
    g.add_argument('--off', action='store_true')
    s.add_argument('--function', choices=['SIN', 'SQU', 'RAMP', 'DC', 'NOIS', 'ARB',
                                          'sin', 'squ', 'ramp', 'dc', 'nois', 'arb'])
    s.add_argument('--frequency', type=float, help='Hz.')
    s.add_argument('--amplitude', type=float, help='Volts peak-to-peak.')
    s.add_argument('--offset', type=float, help='Volts.')
    s.add_argument('--duty', type=float, help='Square duty cycle, percent.')
    s.add_argument('--symmetry', type=float, help='Ramp symmetry, percent.')
    s.set_defaults(fn=cmd_afg)

    s = sub.add_parser('scpi', help='Send raw SCPI. Commands containing ? return a reply.')
    s.add_argument('commands', nargs='+')
    s.set_defaults(fn=cmd_scpi)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except DhoError as ex:
        return emit({'error': str(ex)}, 1)
    except Exception as ex:  # keep stdout machine readable
        return emit({'error': f'{type(ex).__name__}: {ex}'}, 1)


if __name__ == '__main__':
    sys.exit(main())

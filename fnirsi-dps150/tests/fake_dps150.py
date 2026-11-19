#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyserial>=3.5"]
# ///
"""Software DPS-150 on a pseudo-terminal, for testing dps150.py without hardware.

Implements the protocol as documented in references/protocol.md (no echo on float writes,
echo on output enable, ~2 Hz pushes, 0xFF full dump) with a simple resistive load.
It validates frame checksums the same way a real unit is assumed to.

    python3 fake_dps150.py [--load-ohms 10]     # prints the pty path, e.g. /dev/ttys004
"""
import argparse, os, struct, sys, threading, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from dps150 import checksum  # noqa: E402


def f(x): return struct.pack("<f", x)


class Fake:
    def __init__(self, load_ohms):
        self.R = load_ohms
        self.vset, self.iset = 5.0, 1.0
        self.out_on = False
        self.prot = {0xD1: 31.0, 0xD2: 5.1, 0xD3: 150.0, 0xD4: 80.0, 0xD5: 4.0}
        self.presets = [(5.0, 1.0)] * 6
        self.bright, self.vol = 5, 3
        self.session = False
        self.ah = self.wh = 0.0
        self.t_last = time.time()
        self.lock = threading.Lock()

    def measure(self):
        if not self.out_on:
            return 0.0, 0.0, 0.0, 1
        i = self.vset / self.R
        if i > self.iset:
            return self.iset * self.R, self.iset, self.iset ** 2 * self.R, 0     # CC
        return self.vset, i, self.vset * i, 1                                  # CV

    def integrate(self):
        now = time.time(); dt = now - self.t_last; self.t_last = now
        v, i, p, _ = self.measure()
        self.ah += i * dt / 3600; self.wh += p * dt / 3600

    def dump(self):
        v, i, p, mode = self.measure()
        d = f(12.0) + f(self.vset) + f(self.iset) + f(v) + f(i) + f(p) + f(31.5)
        for pv, pi in self.presets: d += f(pv) + f(pi)
        for r in (0xD1, 0xD2, 0xD3, 0xD4, 0xD5): d += f(self.prot[r])
        d += bytes([self.bright, self.vol, 0]) + f(self.ah) + f(self.wh)
        d += bytes([1 if self.out_on else 0, 0, mode, 0]) + f(30.0) + f(5.1)
        d += f(31.0) + f(5.1) + f(150.0) + f(80.0) + f(4.0)
        assert len(d) == 139, len(d)
        return d

    def reply(self, reg, data):
        return bytes([0xF0, 0xA1, reg, len(data)]) + data + bytes([checksum(reg, data)])

    def handle(self, cmd, reg, data):
        out = []
        with self.lock:
            self.integrate()
            if cmd == 0xC1:
                self.session = bool(data[0]) if data else False
            elif cmd == 0xB0:
                pass
            elif cmd == 0xA1:
                if reg == 0xFF: out.append(self.reply(0xFF, self.dump()))
                elif reg == 0xDE: out.append(self.reply(reg, b"DPS-150"))
                elif reg == 0xDF: out.append(self.reply(reg, b"V1.2"))
                elif reg == 0xE0: out.append(self.reply(reg, b"V1.0"))
            elif cmd == 0xB1:
                if reg == 0xC1: self.vset = struct.unpack("<f", data)[0]
                elif reg == 0xC2: self.iset = struct.unpack("<f", data)[0]
                elif reg in self.prot: self.prot[reg] = struct.unpack("<f", data)[0]
                elif 0xC5 <= reg <= 0xD0:
                    n, which = divmod(reg - 0xC5, 2)
                    pv, pi = self.presets[n]
                    val = struct.unpack("<f", data)[0]
                    self.presets[n] = (val, pi) if which == 0 else (pv, val)
                elif reg == 0xD6: self.bright = data[0]
                elif reg == 0xD7: self.vol = data[0]
                elif reg == 0xDB:
                    self.out_on = bool(data[0]); out.append(self.reply(0xDB, data))   # only echoed write
        return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--load-ohms", type=float, default=10.0)
    a = ap.parse_args()
    master, slave = os.openpty()
    print(os.ttyname(slave), flush=True)
    dev = Fake(a.load_ohms)

    def pusher():
        while True:
            time.sleep(0.5)
            with dev.lock:
                if not dev.session: continue
                dev.integrate()
                v, i, p, _ = dev.measure()
                os.write(master, dev.reply(0xC3, f(v) + f(i) + f(p)))
                os.write(master, dev.reply(0xC4, f(31.5)))
                os.write(master, dev.reply(0xC0, f(12.0)))
    threading.Thread(target=pusher, daemon=True).start()

    buf = bytearray()
    while True:
        try:
            chunk = os.read(master, 256)
        except OSError:
            time.sleep(0.05); continue
        buf.extend(chunk)
        while True:
            while buf and buf[0] != 0xF1: del buf[0]
            if len(buf) < 5: break
            cmd, reg, ln = buf[1], buf[2], buf[3]
            if len(buf) < 5 + ln: break
            data = bytes(buf[4:4 + ln])
            if buf[4 + ln] != checksum(reg, data):
                # official app sends a bad checksum on 0xB0 and the unit accepts it: tolerate that one
                if cmd != 0xB0:
                    print("BAD CHECKSUM", buf[:5 + ln].hex(), file=sys.stderr, flush=True)
                    del buf[0]; continue
            for r in dev.handle(cmd, reg, data): os.write(master, r)
            del buf[:5 + ln]


if __name__ == "__main__":
    main()

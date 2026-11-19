# FNIRSI DPS-150 serial protocol

Reverse engineered by third parties from the official Windows app and device captures. Not vendor-documented. Cross-checked across the sources below; where they disagreed, the note says how the driver resolves it.

## Transport
- USB CDC virtual COM port, VID:PID `2E3C:5740`, shows as "AT32 USB serial device".
- 8N1. cho45 opens at 115200 with hardware flow control; the official app asserts RTS without flow control, DTR low. A philipp-iot capture opens at 9600 and sends baud index 1. Both work because the link is USB CDC, so the nominal baud is cosmetic. Driver: 115200, RTS high, DTR low (`--dtr` to flip), no flow control, then sends baud index 5.
- Baud index (1-based): 1=9600, 2=19200, 3=38400, 4=57600, 5=115200.

## Frame
`HDR | CMD | REG | LEN | DATA[LEN] | CHK`
- HDR: `F1` host to device, `F0` device to host.
- CMD: `A1` read / response, `B1` write, `B0` set baud, `C1` session on/off, `C0` **enter DFU, never send**.
- CHK = (REG + LEN + sum(DATA)) & 0xFF. HDR and CMD are excluded. (One source names REG "CMD" and CMD "START"; same bytes.)
- Floats are IEEE-754 float32 little-endian. Read requests carry one `00` data byte (length-0 also accepted).
- A buffer can hold several frames; frame size is 5 + LEN. The official app's baud frame has a bad checksum and the unit still accepts it, so do not assume strict validation on the device side.

## Session
1. `F1 C1 00 01 01 02` open session; wait ~200 ms
2. `F1 B0 00 01 05 06` baud index 5
3. `F1 A1 DE 01 00 DF` model, `... DF ...` hardware, `... E0 ...` firmware
4. `F1 A1 FF 01 00 00` full state dump
5. Close: `F1 C1 00 01 00 01`

Keep about 50 ms between writes. No host keep-alive has been documented. After connect the unit pushes telemetry about every 500-600 ms.

## Registers
| Reg | Dir | Type | Meaning |
|---|---|---|---|
| C0 | R push | f32 | input voltage V |
| C1 | R/W | f32 | voltage set-point V |
| C2 | R/W | f32 | current limit A |
| C3 | R push | 3x f32 | output V, I, P |
| C4 | R push | f32 | temperature (C, unit assumed) |
| C5-D0 | R/W | f32 | presets M1..M6 as (V, I) pairs; V = C5+2(n-1), I = V+1 |
| D1 / D2 / D3 / D4 / D5 | R/W | f32 | OVP V / OCP A / OPP W / OTP C / LVP V |
| D6 / D7 | R/W | u8 | brightness / volume (valid ranges untested) |
| D8 | W | u8 | metering 0 stop, 1 start (dump polarity reported inverted, unverified) |
| D9 / DA | R push | f32 | Ah / Wh, pushed only while output is on |
| DB | R/W | u8 | output enable; the only write that is echoed |
| DC | R push | u8 | protection: 0 OK, 1 OVP, 2 OCP, 3 OPP, 4 OTP, 5 LVP, 6 REP |
| DD | R push | u8 | mode 0 = CC, 1 = CV |
| DE / DF / E0 | R | ascii | model / hardware / firmware |
| E1 | R | u8 | ready/device address, sources disagree on meaning |
| E2 / E3 | R push | f32 | max voltage / max current |
| FF | R | 139 B | full dump |

Float writes are not echoed; read back with `FF` to verify.

## Full dump (reg FF) offsets
0 vin, 4 vset, 8 iset, 12 vout, 16 iout, 20 pout, 24 temp, 28-75 presets M1..M6 (V,I), 76 OVP, 80 OCP, 84 OPP, 88 OTP, 92 LVP, 96 brightness (u8), 97 volume (u8), 98 metering (u8), 99 Ah, 103 Wh, 107 output on (u8), 108 protection (u8), 109 mode (u8), 110 unknown, 111 max V, 115 max I, 119-135 protection ceilings (partly confirmed).

philipp-iot's reference inferred a different tail layout (5 presets, max V/I/P at 76-84); that was marked "inferred, TBD" there and conflicts with cho45, kaushikray2 and nasheed-x, which agree with each other. The driver uses the latter.

## Nameplate
30 V, 5.1 A reported, 150 W. Input 5-30 V DC.

## Sources
- cho45, WebSerial reference implementation: https://github.com/cho45/fnirsi-dps-150
- nasheed-x, PROTOCOL.md + Python API/MCP: https://github.com/nasheed-x/dps150_api
- kaushikray2, Python library + GUI (HW V1.0, FW V1.2 verified): https://github.com/kaushikray2/fnirsi-dps150
- philipp-iot, Kaitai spec and captures: https://philipp-iot.github.io/fnirsi_protocol/
- Others: https://github.com/KochC/DPS-150-python-library, https://github.com/Puyodead1/fnirsi-psu, https://pypi.org/project/fnirsi-dps150/

Confirmed on real hardware (HW V1.0, FW V1.1): session handshake, 0xFF dump offsets 0-109, protection writes with read-back, output enable, ~2 Hz C3 push stream. The reported max V (offset 111 / reg E2) tracks the input voltage (19.4 V with ~19.6 V in), so it is an input-limited ceiling, not the 30 V nameplate.

Open questions to settle on real hardware: temperature units, brightness range, metering polarity, achievable poll rate for `FF`, whether PC-side commands are accepted while the front panel is in menus.

# DHO800 / DHO900 SCPI reference

Taken from RIGOL's "DHO800/DHO900 Programming Guide" and checked on a DHO924S with
firmware 00.01.03. Items marked (unverified) were not exercised on the instrument.

Full guide (about 420 pages):
https://www.batronix.com/files/Rigol/Oszilloskope/DHO900/Manual/DHO800900_ProgrammingGuide_EN.pdf

## Connection

- LAN raw socket, TCP port 5555 (the guide does not print it; confirmed on the instrument). Commands end with `\n`; replies end with `\n`.
- The scope also has a web page at `http://<ip>/` with a SCPI panel, and speaks VXI-11 /
  LXI (`TCPIP::<ip>::INSTR` with a VISA library).
- USB: a USB-TMC device on the rear USB DEVICE port. Needs `pyvisa` plus a backend
  (`pyvisa-py` and `pyusb`); the bundled script does not do USB.
- `:LAN:VISA?` returns the VISA address, `:LAN:IPADdress?` the IP.
- Commands have long and short forms (`:TIMebase:MAIN:SCALe` = `:TIM:MAIN:SCAL`); the
  capital letters are the short form. Queries append `?`.
- `*OPC?` returns 1 when pending operations finish. `:SYSTem:ERRor?` pops one entry from
  the error queue; `0,"No error"` means empty. A query the scope does not understand gets
  no reply at all, so it shows up as a timeout, then as an entry in the error queue.

## Run control

`:RUN`, `:STOP`, `:SINGle`, `:TFORce` (force trigger), `:AUToset`, `:CLEar`.
`:TRIGger:STATus?` returns `TD` (triggered), `WAIT`, `RUN`, `AUTO`, or `STOP`.

## Channels (`:CHANnel<n>`, n = 1 to 4)

| Command | Values |
|---|---|
| `:DISPlay` | `ON`, `OFF` |
| `:SCALe` | V/div. 500 µV to 10 V at 1x probe; scales with probe ratio |
| `:OFFSet` | V |
| `:COUPling` | `DC`, `AC`, `GND` |
| `:PROBe` | 0.001 to 50000 in 1-2-5 steps, plus 15, 150, 1500, 15000 |
| `:BWLimit` | `OFF`, `20M` |
| `:INVert` | `ON`, `OFF` |
| `:UNITs` | `VOLTage`, `WATT`, `AMPere`, `UNKNown` |
| `:LABel:SHOW`, `:LABel:CONTent` | on/off, text |

Screen: 8 vertical divisions, 10 horizontal.

## Timebase and acquisition

| Command | Values |
|---|---|
| `:TIMebase:MAIN:SCALe` | s/div |
| `:TIMebase:MAIN:OFFSet` | s |
| `:TIMebase:MODE` | `MAIN`, `XY`, `ROLL` |
| `:ACQuire:TYPE` | `NORMal`, `PEAK`, `AVERages`, `ULTRa` |
| `:ACQuire:AVERages` | 2 to 65536, powers of 2 |
| `:ACQuire:MDEPth` | `AUTO`, `1k`, `10k`, `100k`, `1M`, `5M`, `10M`, `25M`, `50M` |
| `:ACQuire:SRATe?` | sample rate, Sa/s |

Memory depth limits on the DHO900: 50M with one channel on, 25M with two, 10M with three
or four. DHO800: 25M / 10M / 5M for the same cases.

## Trigger

| Command | Values |
|---|---|
| `:TRIGger:MODE` | `EDGE`, `PULSe`, `SLOPe`, `VIDeo`, `PATTern`, `DURation`, `TIMeout`, `RUNT`, `WINDow`, `DELay`, `SETup`, `NEDGe`, `RS232`, `IIC`, `SPI`, `CAN`, `LIN` |
| `:TRIGger:SWEep` | `AUTO`, `NORMal`, `SINGle` |
| `:TRIGger:EDGE:SOURce` | `CHANnel1`..`4`, `EXT`, `D0`..`D15` (DHO900) |
| `:TRIGger:EDGE:SLOPe` | `POSitive`, `NEGative`, `RFALl` (either) |
| `:TRIGger:EDGE:LEVel` | V |
| `:TRIGger:COUPling` | `DC`, `AC`, `LFReject`, `HFReject` |
| `:TRIGger:HOLDoff` | s |

Other trigger types have their own subtree (`:TRIGger:PULSe:...` etc.); see the guide.

## Measurements

`:MEASure:ITEM? <item>,<src>[,<src2>]` returns the current value in scientific notation.
An unmeasurable item returns `9.9000E+37` (the script maps it to null). The first query
of an item, or a query right after a deep single-shot capture, can return that value for
up to a second or two while the scope computes it, so retry before believing it. Every
queried item is added to the on-screen Result panel; `:MEASure:CLEar` removes them all.

Sources: `CHANnel1`..`4`, `MATH1`..`4`; `D0`..`D15` only for the time-based items.

| Group | Items |
|---|---|
| Voltage | `VMAX`, `VMIN`, `VPP`, `VTOP`, `VBASe`, `VAMP`, `VAVG`, `VRMS`, `ACRMs`, `PVRMs` (per-period RMS), `OVERshoot`, `PREShoot`, `VUPPer`, `VMID`, `VLOWer`, `VARiance`, `MARea` (area), `MPARea` (period area) |
| Time | `PERiod`, `FREQuency`, `RTIMe`, `FTIMe`, `PWIDth`, `NWIDth`, `PDUTy`, `NDUTy`, `TVMAX`, `TVMIN`, `PSLewrate`, `NSLewrate` |
| Counts | `PPULses`, `NPULses`, `PEDGes`, `NEDGes` |
| Two-source | `RRDelay`, `RFDelay`, `FRDelay`, `FFDelay`, `RRPHase`, `RFPHase`, `FRPHase`, `FFPHase` (r = rising, f = falling; first letter is source 1) |

Duty cycles come back as a ratio (0.5 = 50 %). Rise/fall thresholds default
to 10 % / 90 % and are set with `:MEASure:SETup:MAX` / `MID` / `MIN`.

Statistics: `:MEASure:STATistic:ITEM <item>,<src>` enables it, then
`:MEASure:STATistic:ITEM? <type>,<item>,<src>` with type `MAXimum`, `MINimum`, `CURRent`,
`AVERages`, `DEViation`, `CNT` (unverified list). `:MEASure:STATistic:RESet` restarts.
`:MEASure:CLEar` removes on-screen measurement items.

Hardware frequency counter: `:MEASure:COUNter:ENABle ON`, `:MEASure:COUNter:SOURce`,
`:MEASure:COUNter:VALue?`.

## Screenshot

`:DISPlay:DATA? PNG` (or `BMP`, `JPG`) returns a definite-length block:
`#` + one digit N + N digits of byte count + image bytes + `\n`.
`:SAVE:IMAGe:DATA?` returns the same using the format set by `:SAVE:IMAGe:FORMat`.

## Waveform download

```
:WAVeform:SOURce CHANnel1        (also MATH1..4, D0..D15)
:WAVeform:MODE NORMal            (screen, up to 1000 points) | RAW (memory, scope stopped) | MAXimum
:WAVeform:FORMat WORD            (16 bit) | BYTE (8 bit) | ASCii (volts, comma separated)
:WAVeform:STARt 1
:WAVeform:STOP 1000
:WAVeform:PREamble?   -> format,type,points,count,xincrement,xorigin,xreference,yincrement,yorigin,yreference
:WAVeform:DATA?       -> definite-length block, as for the screenshot
```

Conversion:

```
volts   = (raw - yorigin - yreference) * yincrement
time[i] = xorigin + (i - xreference) * xincrement
```

- The ADC is 12 bit, so use WORD; BYTE throws away resolution. WORD samples are
  little-endian unsigned 16 bit, with yreference 32768.
- MATH sources only support NORMal mode.
- For RAW, read in chunks by moving `STARt`/`STOP`; consecutive blocks are contiguous. The
  guide does not state a maximum per read; the instrument returns at most 100000 points
  per `:WAVeform:DATA?` whatever range is requested, so step in chunks of 100000.
  Throughput over LAN is about 200k points per second.
- Do not operate the scope while a RAW read is in progress.

## Signal generator (`:SOURce`, DHO914S and DHO924S only)

| Command | Values |
|---|---|
| `:SOURce:OUTPut:STATe` | `ON`, `OFF` |
| `:SOURce:FUNCtion` | `SINusoid`, `SQUare`, `RAMP`, `DC`, `NOISe`, `ARB` |
| `:SOURce:FREQuency` | Hz. Sine to 25 MHz, square to 15 MHz, ramp to 150 kHz, arb to 10 MHz; minimum 2 mHz |
| `:SOURce:VOLTage:AMPLitude` | Vpp, 2 mV to 10 V (to 5 V above 10 MHz); default 6 V |
| `:SOURce:VOLTage:OFFSet` | V |
| `:SOURce:PHASe` | 0 to 360 degrees |
| `:SOURce:FUNCtion:SQUare:DUTY` | percent |
| `:SOURce:FUNCtion:RAMP:SYMMetry` | percent |
| `:SOURce:MOD:...` | AM / FM / PM modulation |

## Not wrapped by the script

Use `dho.py scpi` with the guide for: digital channels (`:LA:...`, DHO900 with the logic
probe), math and FFT (`:MATH<n>:...`), cursors (`:CURSor:...`), protocol decode
(`:BUS<n>:...`), reference waveforms (`:REFerence:...`), pass/fail masks (`:MASK:...`),
waveform recording (`:RECord:...`), the DVM (`:DVM:...`), and saving to the scope's
internal or USB storage (`:SAVE:IMAGe <path>`, `:SAVE:WAVeform <path>`, `:SAVE:SETup <path>`).

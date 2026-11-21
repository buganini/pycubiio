# pycubiio

A Python library, CLI and GUI for the original Cubiio laser engraver (1st gen, 2017, by Muherz, Bluetooth model).

**The API and CLI follow [pklaus/brother_ql](https://github.com/pklaus/brother_ql)**, by way of [buganini/pyniim](https://github.com/buganini/pyniim): `CubiioRaster`, `convert()`, `backends.helpers.discover()` / `send()` and the `discover` / `info` / `print` / `convert` / `send` / `analyze` commands work the same way, adapted to engraving.

## Install

Requires Python 3.11+.

```bash
python3 -m venv env
```

```bash
env/bin/pip install -e .
```

Dependencies: `bleak` (BLE), `Pillow` (images), `click` (CLI), `QPUIQ` (PUI) and `PySide6-Essentials` (GUI backend).

## Before you start

- **Switch the Cubiio on**: long-press the power button until the indicator turns white. The BLE module advertises without it, but the main board ignores every command until then.
- Do not pair it in the OS Bluetooth settings, and disconnect the phone app (the BLE module accepts one connection).
- On macOS the terminal app needs Bluetooth permission (System Settings → Privacy & Security → Bluetooth); without it the process is killed on the first scan.
- **Laser safety**: wear laser safety glasses, mount the Cubiio on a tripod (its accelerometer stops the job when moved), put the target 155 ± 5 mm in front of it, and stay with the machine.

## How engraving works

The Cubiio only engraves files on its SD card; there is no streaming mode. `send()` / `print` upload each page over BLE as `pycubiio.bmp` (the SD card shows it as `APP/PYCUBIIO.BMP`, overwritten every time), then engrave it and wait until the Cubiio is idle again.

- Work area 100 × 100 mm at 10 px/mm (254 dpi): a label `40x30` needs a 400 × 300 px image; the maximum is `100x100` (1000 × 1000 px).
- Pages are 8-bit grayscale, the same BMP the official app uploads. `--threshold` or `--dither` reduce them to black and white first.
- Upload speed is about 1.9 KB/s (the BLE MTU is fixed at 23 and the HM-10 → MCU UART is the limit): 40 × 30 mm ≈ 120 KB ≈ 1 min; 100 × 100 mm ≈ 1 MB ≈ 9 min.
- `--power` (default 75 %) and `--speed` (default 100 %) use the official app's conversion; lower speed engraves deeper. 30 % power left no visible mark in testing.
- Every job needs the Cubiio's 6-digit unlock code (`--password` / `PYCUBIIO_PASSWORD`).

## CLI

```bash
# Find the Cubiio (switch it on first)
pycubiio discover

# Engrave an image (400 x 300 px for a 40 x 30 mm area)
pycubiio -p 'ble://<device-id>' --password 123456 print -l 40x30 label.png

# Trace the outline at low power without engraving
pycubiio -p 'ble://<device-id>' --password 123456 preview -l 40x30 label.png

# Convert offline, inspect, then send
pycubiio convert -l 40x30 label.png -o label.cubiio
pycubiio analyze label.cubiio --output-dir previews
pycubiio -p 'ble://<device-id>' --password 123456 send label.cubiio

# Device helpers
pycubiio -p 'ble://<device-id>' status
pycubiio -p 'ble://<device-id>' files
pycubiio -p 'ble://<device-id>' abort
pycubiio info models
pycubiio info labels
```

`-p` also accepts the advertised name (`cubiio_0535`). Environment variables: `PYCUBIIO_BACKEND`, `PYCUBIIO_MODEL`, `PYCUBIIO_PRINTER`, `PYCUBIIO_PASSWORD`, `PYCUBIIO_LABEL`. `print` and `send` accept `--json`. A `.cubiio` file is a version-1 JSON job (model, label, power, speed, Base64 grayscale pages); `convert --bmp` writes the BMP the Cubiio receives instead.

## Python API

```python
from pycubiio import Printer

with Printer("ble://<device-id>", password="123456") as printer:
    result = printer.print_image("label.png", label="40x30", power=75, speed=100)
```

Convert and send separately, as with brother_ql:

```python
from pycubiio.raster import CubiioRaster
from pycubiio.conversion import convert
from pycubiio.backends.helpers import discover, send

devices = discover(backend_identifier="bleak")

raster = CubiioRaster("cubiio")
instructions = convert(raster, images=["label.png"], label="40x30", power=75, speed=100)

result = send(
    instructions,
    printer_identifier=devices[0]["identifier"],
    backend_identifier="bleak",
    blocking=True,
    password="123456",
)
```

`convert()` returns an `EngraveJob`, not raw bytes. `label=None` engraves the image at its own size. `send()` returns brother_ql's result fields plus `pages_printed`:

| Field | Meaning |
| --- | --- |
| `instructions_sent` | Every page was uploaded |
| `outcome` | `unknown`, `sent`, `printed` or `error` |
| `printer_state` | Last decoded `RES` status |
| `did_print` | At least one page finished engraving |
| `ready_for_next_job` | The Cubiio was idle afterwards |
| `pages_printed` | Pages finished |

Failures raise `pycubiio.PrintError` with the partial `result`. `blocking=False` is rejected. `AsyncPrinter` and `discover_async()` / `send_async()` are the asyncio versions; pass `progress=callback(stage, done, total)` to `send()` to follow the upload and engraving.

The low-level `pycubiio.Cubiio` class exposes the raw commands (`firmware()`, `status()`, `list_files()`, `upload()`, `preview()`, `engrave()`, `abort()`, …).

## GUI

```bash
env/bin/pycubiio-gui
```

A single-screen PUI app: scan and connect, pick a file already on the SD card, enter the password, set power/speed, then Preview or Engrave.

## Development

```bash
env/bin/pip install -e '.[dev]'
```

```bash
env/bin/python -m pytest -q
```

The tests use a simulated Cubiio and never touch real hardware. `tools/` has raw BLE helpers (`ble_scan.py`, `ble_gatt.py`, `cubiio_cli.py`).

## Hardware

- MCU: ATmega2560 with the Arduino stk500v2 bootloader (USB ID `2341:0042`, Arduino Mega 2560)
- Bluetooth: HM-10 (manufacturer data `0x4D48` "HM"; service `FFE0`, characteristic `FFE1`, MTU 23), a transparent serial link to the MCU

## Protocol

Reverse-engineered from the official Cubiio Remote Android app 1.3.3. Each command is ASCII terminated by `\n`, written to `FFE1` in 20-byte chunks; the Cubiio answers every command with one line terminated by `\r\n`.

| Command | Response | Meaning |
| --- | --- | --- |
| `FW` | `v1.11e` | Firmware version |
| `RES` | bit string | Status: bit 0 busy, 1 laser on, 2 engraving (vs preview), 13 unlocked, 15 aborted |
| `RET` | `26.76` | Temperature °C |
| `CPW` + 6 chars | `ok` | Unlock (required before any laser output; cleared after each job) |
| `RFF`, then `NXF`… | `ok`, file names…, `NO_MORE` | SD card listing |
| `_F=<path>` | `ok` | Select file |
| `RSE` | `ok` | Reset parameters |
| `_P4=` / `_P6=` | `ok` | BMP speed `(1 − speed/100) × 20`, power `(110 − power) × 2` |
| `_P1=` / `_P3=` | `ok` | G-code speed `speed/100`, power `power/100` |
| `PVB` / `PVG` | `ok` | Preview (outline) BMP / G-code |
| `EGB` / `EGG` | `ok` | Engrave BMP / G-code |
| `LASER_IND` | `ok` | Low-power aiming laser |
| `A` | `warning1` (stopped) / `warning3` (nothing running) | Abort |
| `REPG` / `RECWD` | percent / seconds | Progress and elapsed time while engraving |
| `_TF=<name>:<size>` | `TF_READY` | Upload: 1500-byte slices, each acknowledged with its byte count (resend on mismatch), then `ok` |

`.TXT` files on the SD card are engraved with `PVG`/`EGG`; the app writes them in a binary `CUBIIOSIGN` vector-path format. pycubiio only uploads BMP pages.

## Vendor files

The official APK, firmware and updaters are owned by Muherz and are not licensed for redistribution, so they are not included in this repo (`vendor/` is in `.gitignore`). Use the fetch script to download them from the original vendor site <https://cubiio1.muherz.com/download.html> and verify their SHA256:

```bash
scripts/fetch_vendor.sh
```

To fetch only some files, pass path keywords (e.g. skip the updaters):

```bash
scripts/fetch_vendor.sh apk firmware
```

Files that already exist with a matching hash are skipped. On a hash mismatch the script reports an error and discards the download.

## Updating the firmware with avrdude

The official updater is not needed. It is just a wrapper that runs avrdude:

```
avrdude -p atmega2560 -c wiring -b 115200 -D -U flash:w:<hex>:i
```

### 1. Install avrdude and fetch the firmware

```bash
brew install avrdude
```

```bash
scripts/fetch_vendor.sh firmware
```

### 2. Connect and find the serial port

Connect the Cubiio to its power supply (USB alone cannot power it), then connect it to the computer with a USB cable:

```bash
ls /dev/cu.usbmodem*
```

The examples below use `/dev/cu.usbmodem11401`; replace it with your actual port.

### 3. Back up the current firmware and EEPROM

```bash
avrdude -p atmega2560 -c wiring -b 115200 -P /dev/cu.usbmodem11401 \
  -U flash:r:vendor/cubiio_1/firmware/backup_flash.hex:i \
  -U eeprom:r:vendor/cubiio_1/firmware/backup_eeprom.hex:i
```

### 4. Flash the new firmware

```bash
unzip -o vendor/cubiio_1/firmware/v1_11e.zip -d vendor/cubiio_1/firmware/
```

```bash
avrdude -p atmega2560 -c wiring -b 115200 -P /dev/cu.usbmodem11401 -D \
  -U flash:w:vendor/cubiio_1/firmware/v1_11e/v1_11e/v1_11e.hex:i
```

avrdude verifies the flash automatically afterwards; `verified` means success.

### Notes

- Do not unplug the USB cable, cut the power, or let the computer sleep while flashing.
- `-D` skips the chip erase (the bootloader erases each page as it writes), same as the official updater.
- The hex also contains the bootloader section (from `0x3E000`). The bootloader does not overwrite itself, and the official updater flashes this same file.
- To restore, use `backup_flash.hex` in step 4 instead. To restore the EEPROM, use `-U eeprom:w:vendor/cubiio_1/firmware/backup_eeprom.hex:i`.

## License

The vendor files are copyright Muherz. Code and documentation in this repo are the author's own work.

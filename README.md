# pycubiio

Tools and notes for the original Cubiio laser engraver (1st gen, 2017, by Muherz, Bluetooth model).

## Hardware

- MCU: ATmega2560 with the Arduino stk500v2 bootloader (USB ID `2341:0042`, Arduino Mega 2560)
- Bluetooth: BLE module (probably HM-10; the firmware contains AT commands such as `AT+ADDR?` and `AT+POWE3`)

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

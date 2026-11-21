"""Scan for BLE devices and print name, address, RSSI, services and manufacturer data."""

import argparse
import asyncio

from bleak import BleakScanner


async def main(timeout: float, name_filter: str | None) -> None:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    rows = sorted(found.values(), key=lambda da: -(da[1].rssi or -999))
    for dev, adv in rows:
        name = adv.local_name or dev.name or "-"
        if name_filter and name_filter.lower() not in name.lower():
            continue
        print(f"{adv.rssi:>4} dBm  {dev.address}  {name}")
        for uuid in adv.service_uuids:
            print(f"           service  {uuid}")
        for cid, data in adv.manufacturer_data.items():
            print(f"           mfr      0x{cid:04x} {data.hex()}")
        for uuid, data in adv.service_data.items():
            print(f"           svcdata  {uuid} {data.hex()}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-t", "--timeout", type=float, default=10.0)
    ap.add_argument("-n", "--name", help="only show devices whose name contains this (case-insensitive)")
    args = ap.parse_args()
    asyncio.run(main(args.timeout, args.name))

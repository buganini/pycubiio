"""Connect to a BLE device by name and dump its GATT services, characteristics and readable values."""

import argparse
import asyncio

from bleak import BleakClient, BleakScanner


async def main(name: str, timeout: float) -> None:
    dev = await BleakScanner.find_device_by_name(name, timeout=timeout)
    if dev is None:
        raise SystemExit(f"device {name!r} not found")
    print(f"connecting {dev.name} {dev.address}")
    async with BleakClient(dev) as client:
        print(f"mtu {client.mtu_size}")
        for svc in client.services:
            print(f"service {svc.uuid}  {svc.description}")
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                line = f"  char {ch.uuid}  handle={ch.handle}  [{props}]  {ch.description}"
                if "read" in ch.properties:
                    try:
                        val = await client.read_gatt_char(ch)
                        line += f"  = {val.hex()} {val!r}"
                    except Exception as e:
                        line += f"  read error: {e}"
                print(line)
                for d in ch.descriptors:
                    print(f"    desc {d.uuid}  handle={d.handle}  {d.description}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name")
    ap.add_argument("-t", "--timeout", type=float, default=20.0)
    args = ap.parse_args()
    asyncio.run(main(args.name, args.timeout))

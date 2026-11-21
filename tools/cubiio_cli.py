"""Send text commands to an original Cubiio over BLE (HM-10, FFE0/FFE1) and print the responses.

Protocol (from the official Android app): each command is ASCII terminated by "\n",
written to FFE1 in 20-byte chunks; the Cubiio answers each command with one line
terminated by "\r\n" via notifications on FFE1.

Usage:
  cubiio_cli.py cubiio_0535 FW RES        # send commands in order, one response each
  cubiio_cli.py cubiio_0535 -i            # interactive prompt
"""

import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner

CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHUNK = 20
CHUNK_DELAY = 0.01


class Cubiio:
    def __init__(self, client: BleakClient, verbose: bool = False):
        self.client = client
        self.verbose = verbose
        self.buf = b""
        self.lines: asyncio.Queue[str] = asyncio.Queue()

    def _on_notify(self, _char, data: bytearray) -> None:
        if self.verbose:
            print(f"  [rx] {bytes(data)!r}")
        self.buf += bytes(data)
        while b"\r\n" in self.buf:
            line, self.buf = self.buf.split(b"\r\n", 1)
            self.lines.put_nowait(line.decode(errors="replace"))

    async def start(self) -> None:
        await self.client.start_notify(CHAR, self._on_notify)

    async def write(self, data: bytes) -> None:
        if self.verbose:
            print(f"  [tx] {data!r}")
        for i in range(0, len(data), CHUNK):
            await self.client.write_gatt_char(CHAR, data[i : i + CHUNK], response=False)
            await asyncio.sleep(CHUNK_DELAY)

    async def command(self, cmd: str, timeout: float = 5.0) -> str | None:
        await self.write(cmd.encode() + b"\n")
        try:
            return await asyncio.wait_for(self.lines.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def drain(self) -> list[str]:
        out = []
        while not self.lines.empty():
            out.append(self.lines.get_nowait())
        return out


async def main(name: str, cmds: list[str], interactive: bool, timeout: float, verbose: bool) -> None:
    dev = await BleakScanner.find_device_by_name(name, timeout=30)
    if dev is None:
        raise SystemExit(f"device {name!r} not found")
    async with BleakClient(dev) as client:
        cub = Cubiio(client, verbose)
        await cub.start()
        print(f"connected {dev.name}")
        for cmd in cmds:
            if cmd.startswith("@sleep="):  # pseudo-command: wait N seconds
                secs = float(cmd.split("=", 1)[1])
                print(f"  (sleep {secs}s)")
                await asyncio.sleep(secs)
                continue
            if cmd.startswith("@poll="):  # pseudo-command: poll RES every 1s until idle or N seconds
                limit = float(cmd.split("=", 1)[1])
                loop = asyncio.get_running_loop()
                end = loop.time() + limit
                while loop.time() < end:
                    st = await cub.command("RES", timeout)
                    print(f"  RES {st}")
                    if st is not None and st.zfill(32)[-1] == "0":
                        break
                    await asyncio.sleep(1)
                continue
            resp = await cub.command(cmd, timeout)
            print(f"> {cmd}\n< {resp if resp is not None else '(timeout)'}")
        if interactive:
            loop = asyncio.get_running_loop()
            while True:
                for extra in cub.drain():
                    print(f"< {extra}")
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                cmd = line.strip()
                if not cmd:
                    continue
                resp = await cub.command(cmd, timeout)
                print(f"< {resp if resp is not None else '(timeout)'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name")
    ap.add_argument("cmds", nargs="*")
    ap.add_argument("-i", "--interactive", action="store_true")
    ap.add_argument("-t", "--timeout", type=float, default=5.0)
    ap.add_argument("-v", "--verbose", action="store_true", help="print raw BLE tx/rx bytes")
    a = ap.parse_args()
    asyncio.run(main(a.name, a.cmds, a.interactive, a.timeout, a.verbose))

"""BLE protocol for the original Cubiio laser engraver (1st gen, firmware v1.11x).

The Cubiio uses an HM-10 module (service FFE0, characteristic FFE1) as a transparent
serial link to its ATmega2560. Commands are ASCII lines terminated by "\\n", written in
20-byte chunks; every command is answered with exactly one line terminated by "\\r\\n".

Protocol reverse-engineered from the official "Cubiio Remote" Android app v1.3.3.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .errors import CubiioError

SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHUNK = 20
CHUNK_DELAY = 0.005  # app uses 7 ms; uploads are UART-bound at ~1.9 KB/s below this
FILE_SLICE = 1500  # app: FILE_SLICE_SIZE


@dataclass(frozen=True)
class Status:
    """Decoded `RES` bitfield (bit 0 = least significant / rightmost character)."""

    raw: str

    def bit(self, n: int) -> bool:
        s = self.raw.zfill(32)
        return s[-1 - n] == "1"

    @property
    def busy(self) -> bool:  # previewing / engraving / indicator laser on
        return self.bit(0)

    @property
    def laser_on(self) -> bool:  # observed to toggle while the beam is emitting
        return self.bit(1)

    @property
    def engraving(self) -> bool:  # set during EGB/EGG, clear during preview
        return self.bit(2)

    @property
    def unlocked(self) -> bool:  # set after a correct CPW, cleared after a job
        return self.bit(13)

    @property
    def aborted(self) -> bool:
        return self.bit(15)

    @property
    def hall_homing(self) -> bool:
        return self.bit(16)

    def describe(self) -> str:
        if not self.busy:
            return "idle"
        if self.engraving:
            return "engraving"
        return "previewing"


def file_kind(name: str) -> str:
    n = name.lower()
    if n.endswith(".bmp"):
        return "bmp"
    if n.endswith(".txt"):
        return "gcode"
    return "unknown"


@dataclass(frozen=True)
class Found:
    device: BLEDevice
    name: str
    rssi: int

    @property
    def address(self) -> str:
        return self.device.address


async def scan(timeout: float = 8.0) -> list[Found]:
    """Scan for Cubiios: devices advertising the HM-10 service FFE0 or named "cubiio*".
    Sorted by signal strength. Advertisements are sparse, so scan for several seconds."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        if SERVICE_UUID in adv.service_uuids or name.lower().startswith("cubiio"):
            out.append(Found(dev, name or dev.address, adv.rssi))
    return sorted(out, key=lambda f: -f.rssi)


def _fmt(x: float) -> str:
    # Match Java's Float.toString for the values the app sends (e.g. "160.0", "0.3")
    return repr(float(x))


class Cubiio:
    def __init__(self, client: BleakClient, verbose: bool = False):
        self.client = client
        self.verbose = verbose
        self._buf = b""
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._lock = asyncio.Lock()

    # ---- connection -------------------------------------------------------

    @classmethod
    async def connect(cls, target: str | BLEDevice, timeout: float = 30.0, verbose: bool = False) -> "Cubiio":
        """Connect to a scanned BLEDevice, or find one by name first."""
        if isinstance(target, str):
            dev = await BleakScanner.find_device_by_name(target, timeout=timeout)
            if dev is None:
                raise CubiioError(f"device {target!r} not found")
        else:
            dev = target
        client = BleakClient(dev)
        await client.connect()
        cub = cls(client, verbose)
        await client.start_notify(CHAR_UUID, cub._on_notify)
        return cub

    async def disconnect(self) -> None:
        await self.client.disconnect()

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected

    async def __aenter__(self) -> "Cubiio":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    # ---- transport --------------------------------------------------------

    def _on_notify(self, _char, data: bytearray) -> None:
        if self.verbose:
            print(f"  [rx] {bytes(data)!r}")
        self._buf += bytes(data)
        while b"\r\n" in self._buf:
            line, self._buf = self._buf.split(b"\r\n", 1)
            self._lines.put_nowait(line.decode(errors="replace"))

    async def _write(self, data: bytes) -> None:
        if self.verbose:
            print(f"  [tx] {data!r}")
        for i in range(0, len(data), CHUNK):
            await self.client.write_gatt_char(CHAR_UUID, data[i : i + CHUNK], response=False)
            await asyncio.sleep(CHUNK_DELAY)

    async def _readline(self, what: str, timeout: float) -> str:
        try:
            return await asyncio.wait_for(self._lines.get(), timeout)
        except asyncio.TimeoutError:
            raise CubiioError(f"no response to {what} (is the Cubiio switched on?)") from None

    def _drain(self) -> None:
        while not self._lines.empty():  # drop stale lines
            self._lines.get_nowait()

    async def command(self, cmd: str, timeout: float = 5.0) -> str:
        """Send one command and return its one-line response."""
        async with self._lock:
            self._drain()
            await self._write(cmd.encode() + b"\n")
            return await self._readline(repr(cmd), timeout)

    async def upload(self, name: str, data: bytes, progress=None, retries: int = 5) -> None:
        """Write a file to the SD card (the app uses 8.3 names such as "LOGO.bmp").

        Protocol: "_TF=<name>:<size>" -> "TF_READY"; then 1500-byte slices, each
        acknowledged with the number of bytes received (resent on mismatch);
        finally "ok". `progress(sent, total)` is called after each slice.
        """
        async with self._lock:
            self._drain()
            await self._write(f"_TF={name}:{len(data)}\n".encode())
            resp = await self._readline("_TF", 10.0)
            if resp != "TF_READY":
                raise CubiioError(f"upload of {name!r} refused: {resp!r}")
            delay = CHUNK_DELAY
            sent = 0
            while sent < len(data):
                piece = data[sent : sent + FILE_SLICE]
                for attempt in range(retries + 1):
                    await self._write_paced(piece, delay)
                    resp = await self._readline("file slice", 15.0)
                    if resp == str(len(piece)):
                        break
                    if "error" in resp or "warning" in resp:
                        raise CubiioError(f"upload failed at byte {sent}: {resp!r}")
                    delay += 0.001  # same back-off as the app
                else:
                    raise CubiioError(f"upload failed at byte {sent}: slice not acknowledged")
                sent += len(piece)
                if progress:
                    progress(sent, len(data))
            resp = await self._readline("end of upload", 15.0)
            if resp != "ok":
                raise CubiioError(f"upload of {name!r} not confirmed: {resp!r}")

    async def _write_paced(self, data: bytes, delay: float) -> None:
        for i in range(0, len(data), CHUNK):
            await self.client.write_gatt_char(CHAR_UUID, data[i : i + CHUNK], response=False)
            await asyncio.sleep(delay)

    async def _ok(self, cmd: str) -> None:
        resp = await self.command(cmd)
        if resp != "ok":
            raise CubiioError(f"{cmd!r} -> {resp!r}")

    # ---- queries ----------------------------------------------------------

    async def firmware(self) -> str:
        return await self.command("FW")

    async def status(self) -> Status:
        return Status(await self.command("RES"))

    async def temperature(self) -> float:
        return float(await self.command("RET"))

    async def stored_password(self) -> str | None:
        """Password stored on the device ("N/A" -> None when none is set)."""
        pw = await self.command("USERPW")
        return None if pw == "N/A" else pw

    async def progress(self) -> float | None:
        """Job progress in percent while engraving, else None."""
        resp = await self.command("REPG")
        try:
            return float(resp)
        except ValueError:
            return None

    async def elapsed(self) -> int | None:
        """Job time in seconds while engraving, else None."""
        resp = await self.command("RECWD")
        try:
            return int(float(resp))
        except ValueError:
            return None

    async def list_files(self) -> list[str]:
        """Files on the SD card (the firmware lists .BMP/.TXT under APP/ and the root)."""
        await self._ok("RFF")
        files: list[str] = []
        while True:
            name = await self.command("NXF")
            if name == "NO_MORE":
                return files
            if name in files:  # list wrapped around
                return files
            files.append(name)

    # ---- job control ------------------------------------------------------

    async def unlock(self, password: str) -> None:
        await self._ok("CPW" + password[:6].ljust(6))

    async def select_file(self, name: str) -> None:
        await self._ok("_F=" + name)

    async def reset(self) -> None:
        await self._ok("RSE")

    async def set_params(self, kind: str, power: float, speed: float) -> None:
        """power/speed in percent, converted the same way as the official app."""
        if kind == "bmp":
            await self._ok("_P4=" + _fmt((1.0 - speed / 100.0) * 20.0))
            await self._ok("_P6=" + _fmt((110.0 - power) * 2.0))
        elif kind == "gcode":
            await self._ok("_P3=" + _fmt(power / 100.0))
            await self._ok("_P1=" + _fmt(speed / 100.0))
        else:
            raise CubiioError(f"unsupported file kind {kind!r}")

    async def preview(self, name: str, password: str) -> None:
        kind = file_kind(name)
        await self.select_file(name)
        await self.unlock(password)
        await self._ok({"bmp": "PVB", "gcode": "PVG"}[kind])

    async def engrave(self, name: str, password: str, power: float, speed: float) -> None:
        kind = file_kind(name)
        await self.reset()
        await self.unlock(password)
        await self.set_params(kind, power, speed)
        await self.select_file(name)
        await self._ok({"bmp": "EGB", "gcode": "EGG"}[kind])

    async def indicator(self, password: str) -> None:
        """Low-power aiming laser; stop with abort()."""
        await self.unlock(password)
        await self._ok("LASER_IND")

    async def abort(self) -> str:
        """Stop the current job. Returns the raw response ("warning1" if a job was stopped)."""
        return await self.command("A")

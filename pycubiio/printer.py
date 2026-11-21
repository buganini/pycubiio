import asyncio
import logging

from .backends.bleak import open_device, parse_identifier
from .conversion import convert
from .errors import CubiioError, PrintError
from .job import DEFAULT_POWER, DEFAULT_SPEED, FILE_NAME, EngraveJob
from .models import ModelsManager
from .raster import CubiioRaster

logger = logging.getLogger(__name__)
POLL_INTERVAL = 1.0


def require_blocking(blocking):
    if blocking is not True:
        raise ValueError("blocking=False is not implemented; completion must be awaited safely")


def ensure_sync_context():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("An event loop is running; use AsyncPrinter or the async helper")


def new_result():
    # brother_ql.send() result fields, plus pages_printed (as in pyniim)
    return dict(
        instructions_sent=False,
        outcome="unknown",
        printer_state=None,
        did_print=False,
        ready_for_next_job=False,
        pages_printed=0,
    )


def find_path(files, name):
    """Locate an uploaded file in the SD listing (the firmware stores names upper-case)."""
    target = name.upper()
    for path in files:
        if path.upper() == target or path.upper().rsplit("/", 1)[-1] == target:
            return path
    raise CubiioError(f"uploaded file {name!r} not found on SD card: {files}")


class AsyncPrinter:
    def __init__(
        self,
        identifier,
        model="cubiio",
        *,
        password=None,
        power=None,
        speed=None,
        timeout=3600.0,
        device=None,
    ):
        """identifier: ble://<device-id-or-name>[?password=...&power=...&speed=...]
        password: the Cubiio's 6-digit unlock code (needed before any laser output).
        power/speed: session defaults in percent for jobs that do not set them.
        Explicit arguments override the identifier's query options.
        timeout: maximum seconds to wait for one page to finish engraving."""
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        _, options = parse_identifier(identifier)
        for key, value in (("power", power), ("speed", speed)):
            if value is not None and not 0 <= value <= 100:
                raise ValueError(f"{key} must be between 0 and 100 %")
        self.model = ModelsManager().get_model_by_identifier(model)
        self.identifier = identifier
        self.password = str(password) if password else options.get("password")
        self.power = options.get("power", DEFAULT_POWER) if power is None else float(power)
        self.speed = options.get("speed", DEFAULT_SPEED) if speed is None else float(speed)
        self.timeout = timeout
        self.device = device  # an already-connected pycubiio.Cubiio, for tests or reuse
        self.owns_device = device is None
        self.info = None
        self.lock = asyncio.Lock()

    async def __aenter__(self):
        if self.device is None:
            self.device = await open_device(self.identifier)
        try:
            self.info = dict(firmware=await self.device.firmware())
            st = await self.device.status()
            if st.busy:
                raise CubiioError(f"Cubiio is busy ({st.describe()})")
            return self
        except BaseException:
            await self._close()
            raise

    async def __aexit__(self, *args):
        await self._close()

    async def _close(self):
        if self.owns_device and self.device is not None:
            await self.device.disconnect()
            self.device = None

    async def status(self):
        async with self.lock:
            st = await self.device.status()
            return dict(
                model=self.model.identifier,
                firmware=self.info["firmware"],
                state=st.describe(),
                raw=st.raw,
                temperature=await self.device.temperature(),
            )

    async def _upload(self, page, progress=None):
        """Upload one page as FILE_NAME (overwriting it); returns its SD path. Hold self.lock."""
        data = page.bmp()
        logger.debug("uploading %s (%d bytes)", FILE_NAME, len(data))
        await self.device.upload(FILE_NAME, data, progress)
        return find_path(await self.device.list_files(), FILE_NAME)

    def _require_password(self):
        if not self.password:
            raise ValueError(
                "The Cubiio needs its 6-digit unlock code: pass password=... "
                "or use ble://<id>?password=..."
            )
        return self.password

    def settings(self, job):
        """Effective (power, speed): job value, else session/identifier value, else default."""
        power = self.power if job.power is None else job.power
        speed = self.speed if job.speed is None else job.speed
        return power, speed

    async def preview(self, job, progress=None):
        """Upload the first page and trace its outline at low power."""
        job.validate()
        password = self._require_password()
        async with self.lock:
            path = await self._upload(job.pages[0], progress)
            await self.device.preview(path, password)
            await self._wait()

    async def send(self, instructions, *, blocking=True, progress=None):
        """Upload and engrave every page; returns a brother_ql-style result dict.
        progress(stage, done, total) is called with stage "upload" or "engrave"."""
        require_blocking(blocking)
        if not isinstance(instructions, EngraveJob):
            raise TypeError("instructions must be an EngraveJob from pycubiio.conversion.convert")
        instructions.validate()
        password = self._require_password()
        power, speed = self.settings(instructions)
        logger.debug("engraving %d page(s) at power %g%%, speed %g%%", len(instructions.pages), power, speed)
        result = new_result()
        pages = instructions.pages
        try:
            async with self.lock:
                for index, page in enumerate(pages):
                    path = await self._upload(
                        page,
                        (lambda d, t: progress("upload", index * t + d, len(pages) * t))
                        if progress
                        else None,
                    )
                    if index == len(pages) - 1:
                        result["instructions_sent"] = True
                    result["outcome"] = "sent"
                    await self.device.engrave(path, password, power, speed)
                    st = await self._wait(
                        (lambda p: progress("engrave", index * 100 + p, len(pages) * 100))
                        if progress
                        else None
                    )
                    result["printer_state"] = dict(raw=st.raw, state=st.describe())
                    if st.aborted:
                        result["outcome"] = "error"
                        result["ready_for_next_job"] = True
                        raise PrintError(f"Engraving of page {index + 1} was aborted", result)
                    result["did_print"] = True
                    result["pages_printed"] = index + 1
            result["outcome"] = "printed"
            result["ready_for_next_job"] = True
            return result
        except PrintError:
            raise
        except (CubiioError, OSError, TimeoutError) as exc:
            result["outcome"] = "error"
            raise PrintError(str(exc), result) from exc

    async def print_image(self, image, *, label=None, **options):
        job = convert(CubiioRaster(self.model.identifier), [image], label, **options)
        return await self.send(job)

    async def abort(self):
        return await self.device.abort()

    async def _wait(self, progress=None):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        await asyncio.sleep(POLL_INTERVAL)
        while True:
            st = await self.device.status()
            if not st.busy:
                return st
            if progress and st.engraving:
                p = await self.device.progress()
                if p is not None:
                    progress(p)
            if loop.time() > deadline:
                await self.device.abort()
                raise TimeoutError("Engraving did not finish in time; aborted")
            await asyncio.sleep(POLL_INTERVAL)


class Printer:
    """Synchronous session with one event loop for the entire BLE lifetime."""

    def __init__(self, identifier, model="cubiio", **options):
        self.printer = AsyncPrinter(identifier, model, **options)
        self.runner = None

    def __enter__(self):
        ensure_sync_context()
        if self.runner is not None:
            raise RuntimeError("Session is already open")
        self.runner = asyncio.Runner()
        try:
            self.runner.run(self.printer.__aenter__())
        except BaseException:
            self.runner.close()
            self.runner = None
            raise
        return self

    def __exit__(self, *args):
        try:
            self.runner.run(self.printer.__aexit__(*args))
        finally:
            self.runner.close()
            self.runner = None

    def _run(self, coro):
        ensure_sync_context()
        if self.runner is None:
            raise RuntimeError("Use Printer as a context manager")
        return self.runner.run(coro)

    def print_image(self, image, *, label=None, **options):
        return self._run(self.printer.print_image(image, label=label, **options))

    def send(self, instructions, *, blocking=True, progress=None):
        return self._run(self.printer.send(instructions, blocking=blocking, progress=progress))

    def preview(self, job, progress=None):
        return self._run(self.printer.preview(job, progress))

    def status(self):
        return self._run(self.printer.status())

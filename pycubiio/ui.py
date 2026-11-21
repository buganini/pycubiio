"""Single-screen PUI (PySide6) app: scan for and connect to a Cubiio, pick a file on its
SD card, enter the password, set power/speed, then preview or engrave.

Run:  python -m pycubiio.ui     (or the `pycubiio` command after installing)
"""

from __future__ import annotations

import asyncio
import threading

from PUI.PySide6 import *

from .cubiio import Cubiio, CubiioError, Found, file_kind, scan

POLL_INTERVAL = 1.0
SCAN_SECONDS = 8.0


class CubiioApp(Application):
    def __init__(self):
        super().__init__(title="pycubiio")
        self.cub: Cubiio | None = None
        self.found: dict[str, Found] = {}  # address -> scan result
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    def setup(self):
        s = self.state = State()
        s.devices = []  # [(label, address)]
        s.device = ""  # selected address
        s.scanning = False
        s.connecting = False
        s.connected = False
        s.files = []
        s.selected = ""
        s.password = ""
        s.power = "100"
        s.speed = "100"
        s.job = ""  # "", "preview", "engrave"
        s.progress = 0.0
        s.message = ""
        self.spawn(self._scan())

    # ---- background -------------------------------------------------------

    def spawn(self, coro):
        async def wrapper():
            try:
                await coro
            except CubiioError as e:
                self.state.message = f"Error: {e}"
            except Exception as e:
                self.state.message = f"Error: {type(e).__name__}: {e}"
            finally:
                if self.cub and not self.cub.is_connected:
                    self.cub = None
                    self.state.connected = False
                    self.state.job = ""

        asyncio.run_coroutine_threadsafe(wrapper(), self.loop)

    async def _scan(self):
        s = self.state
        s.scanning = True
        s.message = "Scanning for Cubiio… (switch it on: long-press until the light is white)"
        try:
            results = await scan(SCAN_SECONDS)
        finally:
            s.scanning = False
        self.found = {f.address: f for f in results}
        s.devices = [(f"{f.name} ({f.rssi} dBm)", f.address) for f in results]
        if s.device not in self.found:
            s.device = results[0].address if results else ""
        s.message = f"Found {len(results)} Cubiio." if results else "No Cubiio found. Scan again."

    async def _connect(self, address):
        s = self.state
        found = self.found[address]
        s.connecting = True
        s.message = f"Connecting to {found.name}…"
        try:
            self.cub = await Cubiio.connect(found.device)
            s.message = "Reading SD card…"
            files = [f for f in await self.cub.list_files() if file_kind(f) != "unknown"]
        finally:
            s.connecting = False
        s.files = files
        if s.selected not in files:
            s.selected = files[0] if files else ""
        s.connected = True
        s.message = f"Connected to {found.name}, {len(files)} file(s)."
        st = await self.cub.status()
        if st.busy:  # job already running on the machine
            s.job = "engrave" if st.engraving else "preview"
            await self._watch()

    async def _disconnect(self):
        cub, self.cub = self.cub, None
        s = self.state
        s.connected = False
        s.job = ""
        s.files = []
        s.selected = ""
        if cub:
            await cub.disconnect()
        s.message = "Disconnected."

    async def _run(self, job, start):
        s = self.state
        s.job = job
        s.progress = 0
        s.message = f"{'Previewing' if job == 'preview' else 'Engraving'} {s.selected}…"
        try:
            await start
        except BaseException:
            s.job = ""
            raise
        await self._watch()

    async def _watch(self):
        s = self.state
        await asyncio.sleep(POLL_INTERVAL)
        while True:
            if not self.cub:
                return
            st = await self.cub.status()
            if not st.busy:
                break
            if st.engraving:
                p = await self.cub.progress()
                if p is not None:
                    s.progress = p
            await asyncio.sleep(POLL_INTERVAL)
        job, s.job = s.job, ""
        if st.aborted:
            s.message = "Stopped."
        elif job == "engrave":
            s.progress = 100
            s.message = "Engraving finished."
        else:
            s.message = "Preview finished."

    # ---- UI actions -------------------------------------------------------

    def on_scan(self, e):
        self.spawn(self._scan())

    def on_connect(self, e):
        if self.state.connected:
            self.spawn(self._disconnect())
        elif self.state.device:
            self.spawn(self._connect(self.state.device))

    def on_preview(self, e):
        s = self.state
        self.spawn(self._run("preview", self.cub.preview(s.selected, s.password)))

    def on_engrave(self, e):
        s = self.state
        try:
            power, speed = float(s.power), float(s.speed)
        except ValueError:
            s.message = "Error: power and speed must be numbers"
            return
        if not (0 <= power <= 100 and 0 <= speed <= 100):
            s.message = "Error: power and speed must be 0–100"
            return
        self.spawn(self._run("engrave", self.cub.engrave(s.selected, s.password, power, speed)))

    def on_stop(self, e):
        self.spawn(self.cub.abort())

    # ---- view -------------------------------------------------------------

    def content(self):
        s = self.state
        busy_link = s.scanning or s.connecting
        idle = s.connected and not s.job
        ready = idle and bool(s.selected) and bool(s.password)

        with Window(title="pycubiio", size=(460, 300)):
            with VBox():
                with HBox():
                    Label("Device")
                    with ComboBox(text_model=s("device"), disabled=busy_link or s.connected).layout(weight=1):
                        for label, address in s.devices:
                            ComboBoxItem(label, address)
                    Button("Scanning…" if s.scanning else "Scan",
                           disabled=busy_link or s.connected).click(self.on_scan)
                    Button("Disconnect" if s.connected else ("Connecting…" if s.connecting else "Connect"),
                           disabled=busy_link or bool(s.job) or not (s.connected or s.device)).click(self.on_connect)

                Divider()
                with HBox():
                    Label("File")
                    with ComboBox(text_model=s("selected"), disabled=not idle).layout(weight=1):
                        for f in s.files:
                            ComboBoxItem(f, f)

                with HBox():
                    Label("Password")
                    MaskedField(s("password"), placeholder="6 digits").layout(weight=1)

                with HBox():
                    Label("Power %")
                    TextField(s("power")).layout(weight=1)
                    Label("Speed %")
                    TextField(s("speed")).layout(weight=1)

                with HBox():
                    Button("Preview", disabled=not ready).click(self.on_preview)
                    Button("Engrave", disabled=not ready).click(self.on_engrave)
                    Spacer()
                    Button("Stop", disabled=not s.job).click(self.on_stop)

                ProgressBar(progress=s.progress, maximum=100)
                Label(s.message)
                Spacer()


def main():
    CubiioApp().run()


if __name__ == "__main__":
    main()

import asyncio
import struct

import pytest
from PIL import Image

from pycubiio import AsyncPrinter, Cubiio, EngraveJob, PrintError
from pycubiio.backends.bleak import parse_identifier
from pycubiio.conversion import convert
from pycubiio.cubiio import CHAR_UUID, FILE_SLICE, Status
from pycubiio.job import FILE_NAME, decode_bmp, encode_bmp
from pycubiio.raster import CubiioRaster


def gradient(w, h):
    im = Image.new("L", (w, h))
    im.putdata([(x * 255 // max(w - 1, 1)) for y in range(h) for x in range(w)])
    return im


# ---- BMP -----------------------------------------------------------------


def test_bmp_matches_app_layout():
    w, h = 5, 3  # width not a multiple of 4 -> 3 bytes of row padding
    gray = bytes(range(15))
    bmp = encode_bmp(w, h, gray)
    assert bmp[:2] == b"BM"
    size, _, _, offset = struct.unpack_from("<IHHI", bmp, 2)
    assert offset == 14 + 40 + 1024 and size == len(bmp)
    hdr, width, height, planes, bpp = struct.unpack_from("<IiiHH", bmp, 14)
    assert (hdr, width, height, planes, bpp) == (40, 5, -3, 1, 8)  # top-down, 8-bit
    assert bmp[54:58] == b"\0\0\0\0" and bmp[54 + 4 * 255 : 54 + 4 * 256] == b"\xff\xff\xff\0"
    assert bmp[offset : offset + 8] == bytes([0, 1, 2, 3, 4, 0, 0, 0])
    assert len(bmp) == offset + 8 * 3
    assert decode_bmp(bmp) == (w, h, gray)


# ---- conversion ------------------------------------------------------------


def test_convert_label_exact_size_and_rotate():
    raster = CubiioRaster()
    job = convert(raster, [gradient(400, 300)], "40x30")
    assert job.pages[0].width == 400 and job.power is None and job.speed is None
    job = convert(raster, [gradient(300, 400)], "40x30")  # auto-rotated
    assert (job.pages[0].width, job.pages[0].height) == (400, 300)
    with pytest.raises(ValueError, match="requires"):
        convert(raster, [gradient(100, 100)], "40x30")


def test_convert_without_label_uses_image_size():
    job = convert(CubiioRaster(), [gradient(250, 120)])
    assert job.label == "25x12"
    with pytest.raises(ValueError, match="work area"):
        convert(CubiioRaster(), [gradient(1001, 10)])


def test_convert_threshold_and_transparency():
    im = Image.new("RGBA", (10, 1), (0, 0, 0, 0))  # transparent -> white
    im.putpixel((0, 0), (0, 0, 0, 255))
    job = convert(CubiioRaster(), [im], threshold=70)
    assert set(job.pages[0].gray) == {0, 255} and job.pages[0].gray[0] == 0
    assert job.pages[0].gray[1] == 255


def test_job_roundtrip(tmp_path):
    job = convert(CubiioRaster(), [gradient(30, 20)], power=40, speed=60)
    job.save(tmp_path / "j.cubiio")
    assert EngraveJob.load(tmp_path / "j.cubiio") == job


def test_job_validation():
    job = convert(CubiioRaster(), [gradient(10, 10)])
    with pytest.raises(ValueError, match="Power"):
        EngraveJob(job.model, job.label, job.pages, power=101).validate()


# ---- device layer with a fake HM-10 -----------------------------------------


class FakeClient:
    """Simulates the Cubiio firmware behind the HM-10 serial link."""

    def __init__(self, files=("APP/TP.BMP",), drop_first_slice=False):
        self.is_connected = True
        self.callback = None
        self.buf = b""
        self.log = []
        self.files = list(files)
        self.upload = None
        self.drop_first_slice = drop_first_slice
        self.busy_polls = 0
        self.listing = None

    async def start_notify(self, char, callback):
        self.callback = callback

    async def disconnect(self):
        self.is_connected = False

    def reply(self, text):
        data = (text + "\r\n").encode()
        for i in range(0, len(data), 20):  # notifications arrive fragmented
            self.callback(None, bytearray(data[i : i + 20]))

    async def write_gatt_char(self, char, data, response=False):
        assert char == CHAR_UUID and len(data) <= 20 and response is False
        if self.upload is not None:
            self.upload["got"] += data
            name, size, got = self.upload["name"], self.upload["size"], self.upload["got"]
            expected = min(FILE_SLICE, size - self.upload["done"])
            if len(got) == expected:
                if self.drop_first_slice:
                    self.drop_first_slice = False
                    self.reply(str(expected - 7))
                else:
                    self.upload["done"] += expected
                    self.reply(str(expected))
                self.upload["got"] = b""
                if self.upload["done"] == size:
                    self.files.append("APP/" + name.upper())
                    self.log.append(("upload", name, size))
                    self.upload = None
                    self.reply("ok")
            return
        self.buf += bytes(data)
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self.handle(line.decode())

    def handle(self, cmd):
        self.log.append(cmd)
        if cmd.startswith("_TF="):
            name, size = cmd[4:].rsplit(":", 1)
            self.upload = dict(name=name, size=int(size), done=0, got=b"")
            self.reply("TF_READY")
        elif cmd == "FW":
            self.reply("v1.11e")
        elif cmd == "RES":
            if self.busy_polls:
                self.busy_polls -= 1
                self.reply("1000000000101")
            else:
                self.reply("1000000000000")
        elif cmd == "REPG":
            self.reply("50.0")
        elif cmd == "RFF":
            self.listing = iter(self.files + ["NO_MORE"])
            self.reply("ok")
        elif cmd == "NXF":
            self.reply(next(self.listing))
        elif cmd in ("EGB", "PVB"):
            self.busy_polls = 2
            self.reply("ok")
        else:
            self.reply("ok")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr("pycubiio.cubiio.CHUNK_DELAY", 0)
    monkeypatch.setattr("pycubiio.printer.POLL_INTERVAL", 0)


def run(coro):
    return asyncio.run(coro)


def make_device(**kw):
    client = FakeClient(**kw)

    async def setup():
        cub = Cubiio(client)
        await client.start_notify(CHAR_UUID, cub._on_notify)
        return cub

    return client, run(setup())


def test_upload_slices_and_resend():
    client, cub = make_device(drop_first_slice=True)
    data = bytes(range(256)) * 13  # 3328 bytes -> 3 slices
    seen = []
    run(cub.upload("x.bmp", data, lambda d, t: seen.append(d)))
    assert client.log[0] == "_TF=x.bmp:3328"
    assert client.log[-1] == ("upload", "x.bmp", 3328)
    assert seen == [1500, 3000, 3328]


def test_status_bits():
    s = Status("1001000000000101")
    assert s.busy and s.engraving and s.aborted and not s.unlocked


def test_send_uploads_then_engraves():
    client, cub = make_device()
    job = convert(CubiioRaster(), [gradient(20, 10), gradient(20, 10)], power=100, speed=50)

    async def go():
        async with AsyncPrinter("ble://x", password="123456", device=cub) as printer:
            return await printer.send(job)

    result = run(go())
    assert result["outcome"] == "printed" and result["pages_printed"] == 2
    assert result["did_print"] and result["ready_for_next_job"]
    cmds = [c for c in client.log if isinstance(c, str) and c not in ("RES", "REPG", "NXF")]
    once = [
        f"_TF={FILE_NAME}:{len(job.pages[0].bmp())}",
        "RFF",
        "RSE",
        "CPW123456",
        "_P4=10.0",
        "_P6=20.0",
        "_F=APP/PYCUBIIO.BMP",
        "EGB",
    ]
    assert cmds == ["FW"] + once + once


def test_send_reports_abort():
    client, cub = make_device()
    job = convert(CubiioRaster(), [gradient(4, 4)])
    original = client.handle

    def aborting(cmd):
        if cmd == "RES" and client.busy_polls == 1:
            client.busy_polls = 0
            client.log.append(cmd)
            return client.reply("1001000000000000")
        return original(cmd)

    client.handle = aborting

    async def go():
        async with AsyncPrinter("ble://x", password="123456", device=cub) as printer:
            await printer.send(job)

    with pytest.raises(PrintError) as info:
        run(go())
    assert info.value.result["outcome"] == "error" and info.value.result["pages_printed"] == 0


# ---- printer identifier options ----------------------------------------------


def test_parse_identifier():
    assert parse_identifier("ble://ABC") == ("ABC", {})
    assert parse_identifier("cubiio_0535?password=123456&power=75&speed=100") == (
        "cubiio_0535",
        {"password": "123456", "power": 75.0, "speed": 100.0},
    )
    for bad in ("ble://x?pwoer=1", "ble://x?power=101", "ble://x?speed=fast", "ble://x?password="):
        with pytest.raises(ValueError):
            parse_identifier(bad)


def test_precedence_job_over_explicit_over_identifier_over_default():
    job = convert(CubiioRaster(), [gradient(4, 4)])  # power/speed unset
    url = "ble://x?password=111111&power=40&speed=60"
    assert AsyncPrinter("ble://x").settings(job) == (75.0, 100.0)
    assert AsyncPrinter(url).settings(job) == (40.0, 60.0)
    assert AsyncPrinter(url, power=90).settings(job) == (90.0, 60.0)
    set_job = convert(CubiioRaster(), [gradient(4, 4)], power=20, speed=30)
    assert AsyncPrinter(url, power=90).settings(set_job) == (20.0, 30.0)
    assert AsyncPrinter(url).password == "111111"
    assert AsyncPrinter(url, password="222222").password == "222222"


def test_password_from_identifier_reaches_device():
    client, cub = make_device()
    job = convert(CubiioRaster(), [gradient(4, 4)])

    async def go():
        async with AsyncPrinter("ble://x?password=654321&power=100", device=cub) as printer:
            return await printer.send(job)

    assert run(go())["outcome"] == "printed"
    assert "CPW654321" in client.log and "_P6=20.0" in client.log and "_P4=0.0" in client.log


def test_missing_password_fails_before_connecting():
    from pycubiio.backends.helpers import send

    job = convert(CubiioRaster(), [gradient(4, 4)])
    with pytest.raises(ValueError, match="unlock code"):
        send(job, "ble://nonexistent", "bleak")

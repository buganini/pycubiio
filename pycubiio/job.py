"""Engrave jobs: 8-bit grayscale pages (0 = black = full laser), plus power/speed."""

import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from .models import ModelsManager

MAX_JOB_BYTES = 64 * 1024 * 1024
FILE_NAME = "pycubiio.bmp"  # every page is uploaded to this SD card file, then engraved


def encode_bmp(width, height, gray):
    """The BMP the official app uploads: 8-bit, 256-level gray palette, top-down rows
    (negative height), rows padded to 4 bytes."""
    pad = (4 - width % 4) % 4
    rows = b"".join(gray[y * width : (y + 1) * width] + b"\0" * pad for y in range(height))
    palette = b"".join(bytes((i, i, i, 0)) for i in range(256))
    offset = 14 + 40 + len(palette)
    header = struct.pack("<2sIHHI", b"BM", offset + len(rows), 0, 0, offset)
    info = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 8, 0, len(rows), 0, 0, 0, 0)
    return header + info + palette + rows


def decode_bmp(data):
    """Inverse of encode_bmp (only that exact layout)."""
    if data[:2] != b"BM":
        raise ValueError("Not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    _, width, height, _, bpp = struct.unpack_from("<IiiHH", data, 14)
    if bpp != 8:
        raise ValueError("Only 8-bit BMP is supported")
    top_down = height < 0
    height = abs(height)
    stride = (width + 3) & ~3
    rows = [data[offset + y * stride : offset + y * stride + width] for y in range(height)]
    if not top_down:
        rows.reverse()
    return width, height, b"".join(rows)


@dataclass(frozen=True)
class Page:
    width: int
    height: int
    gray: bytes  # width*height bytes, row-major, 0 = black (engraved), 255 = white

    def validate(self, model):
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("Page dimensions must be integers")
        if not (1 <= self.width <= model.max_pixels and 1 <= self.height <= model.max_pixels):
            raise ValueError(
                f"Page is {self.width}x{self.height} px; the work area is "
                f"{model.max_pixels}x{model.max_pixels} px ({model.max_mm:g} mm)"
            )
        if not isinstance(self.gray, bytes) or len(self.gray) != self.width * self.height:
            raise ValueError("Pixel data length does not match page dimensions")

    def bmp(self):
        return encode_bmp(self.width, self.height, self.gray)

    def image(self):
        from PIL import Image

        return Image.frombytes("L", (self.width, self.height), self.gray)


@dataclass(frozen=True)
class EngraveJob:
    model: str
    label: str
    pages: tuple
    power: float = 75.0  # percent
    speed: float = 100.0  # percent

    def validate(self):
        model = ModelsManager().get_model_by_identifier(self.model)
        for field in ("power", "speed"):
            value = getattr(self, field)
            if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise ValueError(f"{field.capitalize()} must be between 0 and 100 %")
        if not self.pages:
            raise ValueError("Job must contain at least one page")
        for page in self.pages:
            page.validate(model)

    def save(self, path):
        self.validate()
        data = dict(
            format="pycubiio",
            version=1,
            model=self.model,
            label=self.label,
            power=self.power,
            speed=self.speed,
            pages=[
                dict(width=p.width, height=p.height, gray=base64.b64encode(p.gray).decode())
                for p in self.pages
            ],
        )
        encoded = json.dumps(data, separators=(",", ":")).encode()
        if len(encoded) + 1 > MAX_JOB_BYTES:
            raise ValueError("Job exceeds 64 MiB file limit")
        Path(path).write_bytes(encoded + b"\n")

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_JOB_BYTES + 1)
        if len(raw) > MAX_JOB_BYTES:
            raise ValueError("Job exceeds 64 MiB file limit")
        try:
            data = json.loads(raw)
            version = data.pop("version")
            if data.pop("format") != "pycubiio" or version != 1:
                raise ValueError("Unsupported job format or version")
            data["pages"] = tuple(
                Page(p["width"], p["height"], base64.b64decode(p["gray"], validate=True))
                for p in data["pages"]
            )
            job = cls(**data)
            job.validate()
            return job
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"Invalid pycubiio job: {exc}") from exc

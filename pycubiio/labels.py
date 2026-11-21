"""Engrave areas expressed like brother_ql/pyniim labels: WIDTHxHEIGHT in millimeters."""

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    identifier: str
    width_mm: float
    height_mm: float

    def pixels(self, pixels_per_mm):
        return tuple(math.floor(mm * pixels_per_mm + 0.5) for mm in (self.width_mm, self.height_mm))


class LabelsManager:
    """Any size up to the 100 x 100 mm work area; these are just common examples."""

    def iter_identifiers(self):
        return iter(("20x20", "30x30", "40x30", "50x50", "100x100"))

    def get_label_by_identifier(self, identifier):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", str(identifier))
        if not match:
            raise ValueError("Label must be WIDTHxHEIGHT in mm, e.g. 40x30")
        width, height = map(float, match.groups())
        if not all(math.isfinite(n) and n > 0 for n in (width, height)):
            raise ValueError("Label dimensions must be positive")
        return Label(identifier, width, height)

"""Cubiio models. Only the 1st-generation Bluetooth Cubiio (2017) is supported."""

from dataclasses import dataclass

from .errors import UnsupportedModel


@dataclass(frozen=True)
class Model:
    identifier: str
    name: str
    pixels_per_mm: int  # official app: engrave size (cm) x 100 = pixels on the long side
    max_mm: float  # square work area at the 155 mm working distance
    min_mm: float  # official app rejects sizes below 0.5 cm

    @property
    def dpi(self):
        return self.pixels_per_mm * 25.4

    @property
    def max_pixels(self):
        return round(self.max_mm * self.pixels_per_mm)


ALL_MODELS = (Model("cubiio", "Cubiio (1st gen, BLE)", 10, 100.0, 5.0),)


class ModelsManager:
    def iter_identifiers(self):
        return (m.identifier for m in ALL_MODELS)

    def get_model_by_identifier(self, identifier):
        normalized = identifier.lower().replace("-", "").replace("_", "").replace(" ", "")
        for model in ALL_MODELS:
            if normalized in (model.identifier, "cubiio1"):
                return model
        raise UnsupportedModel(f"Unknown model: {identifier}")

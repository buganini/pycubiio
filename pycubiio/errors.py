class PycubiioError(Exception):
    """Base error for device and protocol failures."""


class CubiioError(PycubiioError):
    """The Cubiio did not answer or answered unexpectedly."""


class UnsupportedModel(PycubiioError):
    pass


class PrintError(PycubiioError):
    """A failed or uncertain engraving; result preserves the last known progress."""

    def __init__(self, message, result):
        super().__init__(message)
        self.result = result

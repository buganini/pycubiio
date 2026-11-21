import math
from urllib.parse import parse_qsl

from bleak import BleakScanner

from ..cubiio import SERVICE_UUID, Cubiio
from ..errors import CubiioError

OPTIONS = ("password", "power", "speed")


def parse_identifier(identifier):
    """Split ``ble://<device-id-or-name>?password=123456&power=75&speed=100``.

    Returns (address, options); options holds only the keys present, with power and
    speed as floats. The query lets brother_ql-style callers (e.g. labelate) configure
    the Cubiio through printer_identifier alone.
    """
    rest = str(identifier).removeprefix("ble://")
    address, _, query = rest.partition("?")
    if not address or "://" in address:
        raise ValueError("Expected ble://<device-id>[?password=...&power=...&speed=...]")
    options = {}
    for key, value in parse_qsl(query, keep_blank_values=True, strict_parsing=bool(query)):
        if key not in OPTIONS:
            raise ValueError(f"Unknown printer identifier option {key!r}; use {', '.join(OPTIONS)}")
        if key in options:
            raise ValueError(f"Printer identifier option {key!r} given twice")
        if key == "password":
            if not value:
                raise ValueError("Empty password in printer identifier")
            options[key] = value
        else:
            try:
                number = float(value)
            except ValueError:
                raise ValueError(f"{key} must be a number, got {value!r}") from None
            if not math.isfinite(number) or not 0 <= number <= 100:
                raise ValueError(f"{key} must be between 0 and 100 %")
            options[key] = number
    return address, options


def device_address(identifier):
    return parse_identifier(identifier)[0]


async def discover_devices(timeout=8.0):
    """All BLE advertisements; Cubiio advertisements are sparse, so scan for several seconds."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return [
        dict(
            identifier=f"ble://{device.address}",
            name=adv.local_name or device.name,
            rssi=adv.rssi,
            service_match=SERVICE_UUID in [s.lower() for s in adv.service_uuids],
        )
        for device, adv in found.values()
    ]


async def open_device(identifier, timeout=30.0):
    """Connect to ble://<device-id> (as returned by discover) or an advertised name."""
    address = device_address(identifier)
    device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if device is None:
        device = await BleakScanner.find_device_by_name(address, timeout=timeout)
    if device is None:
        raise CubiioError(f"BLE device not found: {identifier}")
    return await Cubiio.connect(device)

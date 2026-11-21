from bleak import BleakScanner

from ..cubiio import SERVICE_UUID, Cubiio
from ..errors import CubiioError


def device_address(identifier):
    address = identifier.removeprefix("ble://")
    if not address or "://" in address:
        raise ValueError("Expected a BLE device identifier or ble://<device-id>")
    return address


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
    """Connect to ble://<device-id> (as returned by discover) or a plain device name."""
    address = device_address(identifier)
    device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if device is None:
        device = await BleakScanner.find_device_by_name(address, timeout=timeout)
    if device is None:
        raise CubiioError(f"BLE device not found: {identifier}")
    return await Cubiio.connect(device)

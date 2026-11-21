import asyncio

from ..printer import AsyncPrinter, ensure_sync_context, require_blocking
from .bleak import discover_devices


def check_backend(identifier):
    if identifier not in (None, "bleak"):
        raise ValueError("Only the bleak backend is implemented")


def is_cubiio(device):
    return device["service_match"] or (device["name"] or "").lower().startswith("cubiio")


async def discover_async(backend_identifier="bleak", *, timeout=8.0, include_unsupported=False):
    check_backend(backend_identifier)
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    devices = [
        dict(device, model="cubiio" if is_cubiio(device) else None)
        for device in await discover_devices(timeout)
    ]
    if include_unsupported:
        return devices
    return [device for device in devices if device["model"]]


def discover(backend_identifier="bleak", *, timeout=8.0, include_unsupported=False):
    ensure_sync_context()
    return asyncio.run(
        discover_async(backend_identifier, timeout=timeout, include_unsupported=include_unsupported)
    )


async def send_async(
    instructions,
    printer_identifier=None,
    backend_identifier=None,
    blocking=True,
    *,
    password,
    timeout=3600.0,
    progress=None,
):
    check_backend(backend_identifier)
    require_blocking(blocking)
    instructions.validate()
    if not printer_identifier:
        raise ValueError("printer_identifier is required")
    async with AsyncPrinter(
        printer_identifier, instructions.model, password=password, timeout=timeout
    ) as printer:
        return await printer.send(instructions, progress=progress)


def send(
    instructions,
    printer_identifier=None,
    backend_identifier=None,
    blocking=True,
    *,
    password,
    timeout=3600.0,
    progress=None,
):
    ensure_sync_context()
    return asyncio.run(
        send_async(
            instructions,
            printer_identifier,
            backend_identifier,
            blocking,
            password=password,
            timeout=timeout,
            progress=progress,
        )
    )

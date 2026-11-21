import asyncio
import functools
import json
import logging
import platform
from dataclasses import asdict
from pathlib import Path

import click
from bleak.exc import BleakError

from . import Printer, __version__
from .backends.bleak import open_device
from .backends.helpers import discover as discover_printers
from .backends.helpers import send
from .conversion import convert
from .errors import PrintError, PycubiioError
from .job import EngraveJob
from .labels import LabelsManager
from .models import ALL_MODELS, ModelsManager
from .raster import CubiioRaster

SAFETY = (
    "Laser: wear safety glasses, keep the target 155 mm in front of the Cubiio "
    "on a stable mount, and stay with the machine."
)


def checked(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (PycubiioError, BleakError, ValueError, OSError, TimeoutError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapped


def emit(value, as_json):
    if as_json:
        click.echo(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, list):
        for item in value:
            click.echo(json.dumps(item, ensure_ascii=False))
    else:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def checked_print(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (PycubiioError, BleakError, ValueError, OSError, TimeoutError) as exc:
            message = str(exc) or type(exc).__name__
            if kwargs.get("as_json"):
                result = dict(exc.result) if isinstance(exc, PrintError) else {"outcome": "error"}
                emit(dict(result, error=message), True)
                raise click.exceptions.Exit(1) from exc
            if isinstance(exc, PrintError):
                message += (
                    f" [outcome={exc.result['outcome']}; "
                    f"pages_printed={exc.result['pages_printed']}]"
                )
            raise click.ClickException(message) from exc

    return wrapped


def printer_id(ctx):
    if not ctx.obj["printer"]:
        raise click.UsageError("Specify --printer or PYCUBIIO_PRINTER (see `pycubiio discover`)")
    return ctx.obj["printer"]


def password(ctx):
    """--password / PYCUBIIO_PASSWORD, else None (then ?password= in --printer is used)."""
    return ctx.obj["password"]


def progress_printer():
    last = {}

    def report(stage, done, total):
        pct = int(done * 100 / total) if total else 100
        if last.get(stage) != pct:
            last[stage] = pct
            click.echo(f"{stage}: {pct}%", err=True)

    return report


@click.group()
@click.option(
    "-b", "--backend", type=click.Choice(["bleak"]), default="bleak", envvar="PYCUBIIO_BACKEND"
)
@click.option("-m", "--model", default="cubiio", envvar="PYCUBIIO_MODEL")
@click.option(
    "-p",
    "--printer",
    envvar="PYCUBIIO_PRINTER",
    help="ble://<device-id-or-name>[?password=...&power=...&speed=...]",
)
@click.option("--password", envvar="PYCUBIIO_PASSWORD", help="6-digit unlock code")
@click.option("--debug", is_flag=True, help="Log BLE TX/RX")
@click.version_option(__version__)
@click.pass_context
def cli(ctx, backend, model, printer, password, debug):
    """Engrave with an original Cubiio (1st gen) over BLE; brother_ql-style interface."""
    ctx.obj = dict(backend=backend, model=model, printer=printer, password=password, debug=debug)
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("pycubiio").setLevel(logging.DEBUG if debug else logging.WARNING)


@cli.command()
@click.option("--timeout", type=click.FloatRange(min=0, min_open=True), default=8.0)
@click.option("--json", "as_json", is_flag=True)
@click.option("--all", "include_unsupported", is_flag=True, help="List every BLE advertisement.")
@click.pass_context
@checked
def discover(ctx, timeout, as_json, include_unsupported):
    """Scan for Cubiios (HM-10 service FFE0 or name cubiio*). Switch the Cubiio on first."""
    emit(
        discover_printers(
            ctx.obj["backend"], timeout=timeout, include_unsupported=include_unsupported
        ),
        as_json,
    )


@cli.group()
def info():
    """Inspect model, label and environment information."""


@info.command()
@click.option("--json", "as_json", is_flag=True)
def models(as_json):
    emit([dict(**asdict(m), dpi=m.dpi, max_pixels=m.max_pixels) for m in ALL_MODELS], as_json)


@info.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
@checked
def labels(ctx, as_json):
    model = ModelsManager().get_model_by_identifier(ctx.obj["model"])
    manager = LabelsManager()
    emit(
        [
            dict(label=i, pixels=manager.get_label_by_identifier(i).pixels(model.pixels_per_mm))
            for i in manager.iter_identifiers()
        ],
        as_json,
    )


@info.command()
@click.option("--json", "as_json", is_flag=True)
def env(as_json):
    emit(
        dict(pycubiio=__version__, python=platform.python_version(), platform=platform.platform()),
        as_json,
    )


def image_options(function):
    decorators = [
        click.argument(
            "images", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False)
        ),
        click.option(
            "-l",
            "--label",
            envvar="PYCUBIIO_LABEL",
            help="WIDTHxHEIGHT mm; image must match at 10 px/mm. Default: image size.",
        ),
        click.option(
            "-r", "--rotate", type=click.Choice(["auto", "0", "90", "180", "270"]), default="auto"
        ),
        click.option(
            "-t", "--threshold", type=click.FloatRange(0, 100), help="Black/white cutoff"
        ),
        click.option("-d", "--dither", is_flag=True),
        click.option("--power", type=click.FloatRange(0, 100), help="Percent; default ?power= or 75"),
        click.option("--speed", type=click.FloatRange(0, 100), help="Percent; default ?speed= or 100"),
    ]
    for decorator in reversed(decorators):
        function = decorator(function)
    return function


def make_job(ctx, **options):
    return convert(CubiioRaster(ctx.obj["model"]), **options)


@cli.command("print")
@image_options
@click.option("--json", "as_json", is_flag=True, help="Emit results and runtime errors as JSON")
@click.pass_context
@checked_print
def print_cmd(ctx, as_json, **options):
    """Convert images, upload them to the SD card and engrave them."""
    identifier = printer_id(ctx)
    job = make_job(ctx, **options)
    click.echo(SAFETY, err=True)
    emit(
        send(
            job,
            identifier,
            ctx.obj["backend"],
            password=password(ctx),
            progress=progress_printer(),
        ),
        as_json,
    )


@cli.command("convert")
@image_options
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False))
@click.option("--bmp", is_flag=True, help="Write the Cubiio BMP of the first page instead")
@click.pass_context
@checked
def convert_cmd(ctx, output, bmp, **options):
    """Save an offline .cubiio JSON job (or the BMP the Cubiio would receive)."""
    job = make_job(ctx, **options)
    if bmp:
        Path(output).write_bytes(job.pages[0].bmp())
    else:
        job.save(output)
    click.echo(output)


@cli.command("send")
@click.argument("instructions", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit results and runtime errors as JSON")
@click.pass_context
@checked_print
def send_cmd(ctx, instructions, as_json):
    """Execute a .cubiio job."""
    identifier = printer_id(ctx)
    job = EngraveJob.load(instructions)
    click.echo(SAFETY, err=True)
    emit(
        send(
            job,
            identifier,
            ctx.obj["backend"],
            password=password(ctx),
            progress=progress_printer(),
        ),
        as_json,
    )


@cli.command()
@image_options
@click.pass_context
@checked
def preview(ctx, **options):
    """Upload the first image and trace its outline at low power (no engraving)."""
    job = make_job(ctx, **options)
    with Printer(printer_id(ctx), ctx.obj["model"], password=password(ctx)) as printer:
        printer.preview(job, lambda d, t: progress_printer()("upload", d, t))
    click.echo("preview finished")


@cli.command()
@click.argument("instructions", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", type=click.Path(file_okay=False), help="Write pages as PNG files")
@click.option("--json", "as_json", is_flag=True)
@checked
def analyze(instructions, output_dir, as_json):
    """Validate a job and optionally render its pages."""
    job = EngraveJob.load(instructions)
    if output_dir:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(job.pages, 1):
            page.image().save(directory / f"page-{index:04d}.png")
    emit(
        dict(
            model=job.model,
            label=job.label,
            pages=len(job.pages),
            power=job.power,
            speed=job.speed,
        ),
        as_json,
    )


@cli.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
@checked
def status(ctx, as_json):
    """Firmware, state and temperature."""
    with Printer(printer_id(ctx), ctx.obj["model"], password=password(ctx)) as printer:
        emit(printer.status(), as_json)


@cli.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
@checked
def files(ctx, as_json):
    """List files on the SD card."""

    async def run():
        cub = await open_device(printer_id(ctx))
        try:
            return await cub.list_files()
        finally:
            await cub.disconnect()

    emit(asyncio.run(run()), as_json)


@cli.command()
@click.pass_context
@checked
def abort(ctx):
    """Stop the current preview/engraving."""

    async def run():
        cub = await open_device(printer_id(ctx))
        try:
            return await cub.abort()
        finally:
            await cub.disconnect()

    click.echo(asyncio.run(run()))

import math

from PIL import Image

from .job import EngraveJob, Page
from .labels import LabelsManager


def convert(
    raster,
    images,
    label=None,
    *,
    rotate="auto",
    threshold=None,
    dither=False,
    power=None,
    speed=None,
):
    """Convert images without device I/O.

    label: "WIDTHxHEIGHT" in mm; the image must then be exactly that many pixels at
    10 px/mm (rotate="auto" accepts swapped width/height). label=None engraves the
    image at its own pixel size (10 px = 1 mm).
    The Cubiio engraves grayscale; threshold (0-100, brother_ql semantics) or dither
    reduce it to black and white first.
    power/speed (percent) left as None are decided at send time: the printer
    identifier's ?power=/?speed=, else 75 % / 100 %.
    """
    model = raster.model_obj
    if threshold is not None and (not math.isfinite(threshold) or not 0 <= threshold <= 100):
        raise ValueError("Threshold must be between 0 and 100")
    if rotate not in ("auto", 0, 90, 180, 270, "0", "90", "180", "270"):
        raise ValueError("Rotate must be auto, 0, 90, 180 or 270")
    size = None
    if label is not None:
        size = LabelsManager().get_label_by_identifier(label).pixels(model.pixels_per_mm)
    pages = []
    for source in images:
        if isinstance(source, Image.Image):
            im = source.copy()
        else:
            with Image.open(source) as opened:
                im = opened.copy()
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, "white")
        im = Image.alpha_composite(bg, rgba).convert("L")
        if rotate == "auto":
            angle = 90 if size and im.size != size and im.size == size[::-1] else 0
        else:
            angle = int(rotate)
        if angle:
            im = im.rotate(angle, expand=True)
        if size and im.size != size:
            raise ValueError(f"Image is {im.size}; label {label} requires {size} pixels")
        if dither:
            im = im.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
        elif threshold is not None:
            cutoff = int((100 - threshold) / 100 * 255)
            im = im.point(lambda v: 0 if v < cutoff else 255)
        pages.append(Page(im.width, im.height, im.tobytes()))
    job = EngraveJob(
        model.identifier,
        label or f"{pages[0].width / model.pixels_per_mm:g}x{pages[0].height / model.pixels_per_mm:g}"
        if pages
        else "",
        tuple(pages),
        None if power is None else float(power),
        None if speed is None else float(speed),
    )
    job.validate()
    raster.job = job
    return job

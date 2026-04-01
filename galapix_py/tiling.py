from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Iterator

from PIL import Image as PILImage

from .models import FileEntry, TILE_SIZE, TileRecord, file_stats_for_path

PILImage.MAX_IMAGE_PIXELS = None

try:
    RESAMPLE_LANCZOS = PILImage.Resampling.LANCZOS
except AttributeError:
    RESAMPLE_LANCZOS = PILImage.LANCZOS


class RasterImage:
    def __init__(self, image: PILImage.Image) -> None:
        self._image = image

    @property
    def width(self) -> int:
        return self._image.width

    @property
    def height(self) -> int:
        return self._image.height

    def thumbnail_image(self, width: int, height: int, size: str = "force") -> "RasterImage":
        if size != "force":
            raise ValueError(f"unsupported thumbnail size mode: {size}")
        resized = self._image.resize((width, height), RESAMPLE_LANCZOS)
        return RasterImage(resized)

    def crop(self, left: int, top: int, width: int, height: int) -> "RasterImage":
        return RasterImage(self._image.crop((left, top, left + width, top + height)))

    def jpegsave_buffer(self, Q: int = 85, strip: bool = True) -> bytes:
        image = _coerce_jpeg_mode(self._image)
        buffer = BytesIO()
        save_kwargs = {"format": "JPEG", "quality": Q}
        if strip:
            save_kwargs["icc_profile"] = None
            save_kwargs["exif"] = b""
            save_kwargs["comment"] = b""
        image.save(buffer, **save_kwargs)
        return buffer.getvalue()


def _coerce_jpeg_mode(image: PILImage.Image) -> PILImage.Image:
    if image.mode in {"RGB", "L", "CMYK"}:
        return image
    if image.mode in {"RGBA", "LA"}:
        alpha_index = image.getbands().index("A")
        alpha = image.getchannel(alpha_index)
        flattened = PILImage.new("RGB", image.size, (0, 0, 0))
        flattened.paste(image.convert("RGBA").convert("RGB"), mask=alpha)
        return flattened
    if image.mode == "P":
        return _coerce_jpeg_mode(image.convert("RGBA" if "transparency" in image.info else "RGB"))
    return image.convert("RGB")


def normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def load_image(path: str | Path, access: str = "sequential") -> RasterImage:
    del access
    normalized = normalize_path(path)
    with PILImage.open(normalized) as image:
        image.load()
        return RasterImage(image.copy())


def probe_file_entry(path: str | Path) -> FileEntry:
    normalized = Path(normalize_path(path))
    image = load_image(normalized, access="sequential")
    mtime_ns, size_bytes = file_stats_for_path(normalized)
    image_format = normalized.suffix.lower().lstrip(".") or "unknown"
    return FileEntry(
        file_id=None,
        url=str(normalized),
        mtime_ns=mtime_ns,
        size_bytes=size_bytes,
        width=image.width,
        height=image.height,
        image_format=image_format,
    )


def compute_thumbnail_scale(width: int, height: int) -> int:
    longest = max(width, height, 1)
    scale = 0
    while longest > TILE_SIZE:
        longest //= 2
        scale += 1
    return scale


def generate_tiles_for_entry(
    entry: FileEntry,
    min_scale: int,
    max_scale: int,
    quality: int = 85,
) -> Iterator[TileRecord]:
    image = load_image(entry.url, access="random")
    for scale in range(min_scale, max_scale + 1):
        factor = 2 ** scale
        width = max(1, math.ceil(entry.width / factor))
        height = max(1, math.ceil(entry.height / factor))
        scaled = image.thumbnail_image(width, height=height, size="force")
        yield from cut_surface_into_tiles(scaled, scale, quality=quality)


def cut_surface_into_tiles(image: RasterImage, scale: int, quality: int = 85) -> Iterator[TileRecord]:
    x_tiles = math.ceil(image.width / TILE_SIZE)
    y_tiles = math.ceil(image.height / TILE_SIZE)
    for y in range(y_tiles):
        for x in range(x_tiles):
            left = x * TILE_SIZE
            top = y * TILE_SIZE
            width = min(TILE_SIZE, image.width - left)
            height = min(TILE_SIZE, image.height - top)
            tile = image.crop(left, top, width, height)
            jpeg_bytes = tile.jpegsave_buffer(Q=quality, strip=True)
            yield TileRecord(
                file_id=None,
                scale=scale,
                x=x,
                y=y,
                width=width,
                height=height,
                jpeg_bytes=bytes(jpeg_bytes),
            )


def preload_tile(entry: FileEntry) -> TileRecord | None:
    tiles = generate_tiles_for_entry(entry, entry.thumbnail_scale, entry.thumbnail_scale)
    return next(tiles, None)

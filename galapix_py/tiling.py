from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Iterator

import pyvips

from .models import FileEntry, TILE_SIZE, TileRecord, file_stats_for_path


def normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def load_image(path: str | Path, access: str = "sequential") -> pyvips.Image:
    normalized = normalize_path(path)
    return pyvips.Image.new_from_file(normalized, access=access)


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


def cut_surface_into_tiles(image: pyvips.Image, scale: int, quality: int = 85) -> Iterator[TileRecord]:
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

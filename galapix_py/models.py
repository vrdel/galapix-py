from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


TILE_SIZE = 256


@dataclass(slots=True)
class FileEntry:
    file_id: Optional[int]
    url: str
    mtime_ns: int
    size_bytes: int
    width: int
    height: int
    image_format: str

    @property
    def thumbnail_scale(self) -> int:
        longest = max(self.width, self.height, 1)
        scale = 0
        while longest > TILE_SIZE:
            longest //= 2
            scale += 1
        return scale

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


@dataclass(slots=True)
class TileRecord:
    file_id: Optional[int]
    scale: int
    x: int
    y: int
    width: int
    height: int
    jpeg_bytes: bytes


@dataclass(slots=True)
class ViewerOptions:
    database: Path
    threads: int = 4
    title: str = "galapix-py"
    width: int = 1280
    height: int = 720
    fullscreen: bool = False
    images_per_row: int | None = None
    memory_only: bool = False
    validate_render: bool = False
    validation_timeout: float = 5.0


@dataclass(slots=True)
class TileRequest:
    scale: int
    x: int
    y: int
    callback: Callable[[TileRecord], None]


@dataclass(slots=True)
class PendingImageLoad:
    url: str
    on_file: Callable[[FileEntry], None]
    on_tile: Optional[Callable[[FileEntry, TileRecord], None]] = None


@dataclass(slots=True)
class ImagePlacement:
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    target_x: float = 0.0
    target_y: float = 0.0
    target_scale: float = 1.0
    last_x: float = 0.0
    last_y: float = 0.0
    last_scale: float = 1.0


@dataclass(slots=True)
class VisibleTile:
    texture_id: int
    left: float
    top: float
    right: float
    bottom: float


def file_stats_for_path(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size

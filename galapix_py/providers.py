from __future__ import annotations

import math
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Protocol

from .database_thread import DatabaseThread
from .jobs import JobHandle, JobManager
from .models import FileEntry, TILE_SIZE, TileRecord
from .tiling import load_image


class TileProvider(Protocol):
    def request_tile(self, scale: int, x: int, y: int, callback: Callable[[TileRecord], None]) -> JobHandle:
        ...

    def get_size(self) -> tuple[int, int]:
        ...

    def get_max_scale(self) -> int:
        ...


@dataclass(slots=True)
class DatabaseTileProvider:
    db_thread: DatabaseThread
    entry: FileEntry

    def request_tile(self, scale: int, x: int, y: int, callback: Callable[[TileRecord], None]) -> JobHandle:
        return self.db_thread.request_tile(self.entry, scale, x, y, callback)

    def get_size(self) -> tuple[int, int]:
        return self.entry.size

    def get_max_scale(self) -> int:
        return self.entry.thumbnail_scale


@dataclass(slots=True)
class InMemoryTileProvider:
    jobs: JobManager
    entry: FileEntry
    quality: int = 85
    _base_image: object | None = None
    _scaled_images: dict[int, object] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def request_tile(self, scale: int, x: int, y: int, callback: Callable[[TileRecord], None]) -> JobHandle:
        def generate() -> None:
            tile = self._generate_tile(scale, x, y)
            if tile is not None and not handle.is_aborted():
                callback(tile)

        handle = self.jobs.submit(generate)
        return handle

    def get_size(self) -> tuple[int, int]:
        return self.entry.size

    def get_max_scale(self) -> int:
        return self.entry.thumbnail_scale

    def _get_base_image(self) -> object:
        with self._lock:
            if self._base_image is None:
                self._base_image = load_image(self.entry.url, access="random")
            return self._base_image

    def _get_scaled_image(self, scale: int) -> object:
        base = self._get_base_image()
        with self._lock:
            scaled = self._scaled_images.get(scale)
            if scaled is not None:
                return scaled
            factor = 2 ** scale
            width = max(1, math.ceil(self.entry.width / factor))
            height = max(1, math.ceil(self.entry.height / factor))
            scaled = base.thumbnail_image(width, height=height, size="force")
            self._scaled_images[scale] = scaled
            return scaled

    def _generate_tile(self, scale: int, x: int, y: int) -> TileRecord | None:
        scaled = self._get_scaled_image(scale)
        if x < 0 or y < 0:
            return None
        left = x * TILE_SIZE
        top = y * TILE_SIZE
        if left >= scaled.width or top >= scaled.height:
            return None
        width = min(TILE_SIZE, scaled.width - left)
        height = min(TILE_SIZE, scaled.height - top)
        tile = scaled.crop(left, top, width, height)
        jpeg_bytes = tile.jpegsave_buffer(Q=self.quality, strip=True)
        return TileRecord(
            file_id=self.entry.file_id,
            scale=scale,
            x=x,
            y=y,
            width=width,
            height=height,
            jpeg_bytes=bytes(jpeg_bytes),
        )

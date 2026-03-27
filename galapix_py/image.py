from __future__ import annotations

import math
import queue
from dataclasses import dataclass, field
from typing import Optional

from .jobs import JobHandle
from .models import FileEntry, ImagePlacement, TILE_SIZE, TileRecord
from .providers import TileProvider


@dataclass(slots=True)
class ImageTileCache:
    provider: TileProvider
    requested: set[tuple[int, int, int]] = field(default_factory=set)
    handles: dict[tuple[int, int, int], JobHandle] = field(default_factory=dict)
    tiles: dict[tuple[int, int, int], TileRecord] = field(default_factory=dict)
    queue: "queue.Queue[TileRecord]" = field(default_factory=queue.Queue)
    focus_scale: int | None = None

    def request_tile(self, scale: int, x: int, y: int) -> TileRecord | None:
        key = (scale, x, y)
        if key in self.tiles:
            return self.tiles[key]
        if key not in self.requested:
            self.requested.add(key)
            self.handles[key] = self.provider.request_tile(scale, x, y, self.receive_tile)
        return None

    def request_parent_tiles(self, scale: int, x: int, y: int) -> None:
        parent_scale = scale + 1
        parent_x = x // 2
        parent_y = y // 2
        max_scale = self.provider.get_max_scale()
        while parent_scale <= max_scale:
            key = (parent_scale, parent_x, parent_y)
            if key not in self.tiles and key not in self.requested:
                self.requested.add(key)
                self.handles[key] = self.provider.request_tile(parent_scale, parent_x, parent_y, self.receive_tile)
            parent_scale += 1
            parent_x //= 2
            parent_y //= 2

    def receive_tile(self, tile: TileRecord) -> None:
        self.queue.put(tile)

    def process_queue(self) -> None:
        while True:
            try:
                tile = self.queue.get_nowait()
            except queue.Empty:
                break
            key = (tile.scale, tile.x, tile.y)
            self.tiles[key] = tile
            self.requested.discard(key)
            self.handles.pop(key, None)

    def clear(self) -> None:
        for handle in self.handles.values():
            handle.abort()
        self.requested.clear()
        self.handles.clear()
        self.tiles.clear()

    def set_focus_scale(self, scale: int) -> None:
        if self.focus_scale == scale:
            return
        self.focus_scale = scale
        self.prune_for_scale(scale)

    def prune_for_scale(self, scale: int, keep_distance: int = 2) -> None:
        min_scale = max(0, scale - keep_distance)
        max_scale = scale + keep_distance
        for key, handle in list(self.handles.items()):
            if not (min_scale <= key[0] <= max_scale):
                handle.abort()
                del self.handles[key]
        self.requested = {
            key for key in self.requested
            if min_scale <= key[0] <= max_scale
        }
        self.tiles = {
            key: tile for key, tile in self.tiles.items()
            if min_scale <= key[0] <= max_scale
        }

    def get_cached_tile(self, scale: int, x: int, y: int) -> TileRecord | None:
        return self.tiles.get((scale, x, y))

    def find_parent_tile(self, scale: int, x: int, y: int) -> tuple[TileRecord, int] | None:
        max_scale = self.provider.get_max_scale()
        parent_scale = scale + 1
        downscale = 2
        while parent_scale <= max_scale:
            tile = self.get_cached_tile(parent_scale, x // downscale, y // downscale)
            if tile is not None:
                return tile, downscale
            parent_scale += 1
            downscale *= 2
        return None


@dataclass(slots=True)
class Image:
    url: str
    placement: ImagePlacement = field(default_factory=ImagePlacement)
    provider: Optional[TileProvider] = None
    file_entry_queue: "queue.Queue[FileEntry]" = field(default_factory=queue.Queue)
    provider_queue: "queue.Queue[TileProvider]" = field(default_factory=queue.Queue)
    tile_queue: "queue.Queue[TileRecord]" = field(default_factory=queue.Queue)
    cache: Optional[ImageTileCache] = None
    file_entry_requested: bool = False
    selected: bool = False
    visible: bool = False

    def set_provider(self, provider: TileProvider) -> None:
        self.provider = provider
        self.cache = ImageTileCache(provider)

    def receive_file_entry(self, entry: FileEntry, provider: TileProvider) -> None:
        self.file_entry_queue.put(entry)
        self.provider_queue.put(provider)

    def receive_tile(self, tile: TileRecord) -> None:
        self.tile_queue.put(tile)

    def process_queues(self) -> None:
        while True:
            try:
                self.file_entry_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                provider = self.provider_queue.get_nowait()
            except queue.Empty:
                break
            self.set_provider(provider)
        while True:
            try:
                tile = self.tile_queue.get_nowait()
            except queue.Empty:
                break
            if self.cache is not None:
                self.cache.receive_tile(tile)
        if self.cache is not None:
            self.cache.process_queue()

    def update_animation(self, progress: float) -> None:
        self.placement.x = (self.placement.last_x * (1.0 - progress)) + (self.placement.target_x * progress)
        self.placement.y = (self.placement.last_y * (1.0 - progress)) + (self.placement.target_y * progress)
        self.placement.scale = (self.placement.last_scale * (1.0 - progress)) + (self.placement.target_scale * progress)

    def set_target(self, x: float, y: float, scale: float) -> None:
        self.placement.last_x = self.placement.x
        self.placement.last_y = self.placement.y
        self.placement.last_scale = self.placement.scale
        self.placement.target_x = x
        self.placement.target_y = y
        self.placement.target_scale = scale

    def set_absolute(self, x: float, y: float, scale: float) -> None:
        self.placement.x = x
        self.placement.y = y
        self.placement.scale = scale
        self.placement.last_x = x
        self.placement.last_y = y
        self.placement.last_scale = scale
        self.placement.target_x = x
        self.placement.target_y = y
        self.placement.target_scale = scale

    def size(self) -> tuple[int, int]:
        if self.provider is not None:
            return self.provider.get_size()
        return TILE_SIZE, TILE_SIZE

    def rect(self) -> tuple[float, float, float, float]:
        width, height = self.size()
        scaled_w = width * self.placement.scale
        scaled_h = height * self.placement.scale
        left = self.placement.x - (scaled_w / 2.0)
        top = self.placement.y - (scaled_h / 2.0)
        return left, top, left + scaled_w, top + scaled_h

    def overlaps(self, clip_rect: tuple[float, float, float, float]) -> bool:
        left, top, right, bottom = self.rect()
        c_left, c_top, c_right, c_bottom = clip_rect
        return not (right < c_left or bottom < c_top or left > c_right or top > c_bottom)

    def contains_point(self, x: float, y: float) -> bool:
        left, top, right, bottom = self.rect()
        return left <= x <= right and top <= y <= bottom

    def choose_scale(self, zoom: float) -> int:
        if self.provider is None:
            return 0
        value = math.log(max(1e-6, 1.0 / max(zoom * self.placement.scale, 1e-6)), 2)
        return max(0, min(int(value), self.provider.get_max_scale()))

    def on_enter_screen(self) -> None:
        self.visible = True

    def on_leave_screen(self) -> None:
        self.visible = False
        if self.cache is not None:
            self.cache.clear()

    def refresh(self) -> None:
        self.provider = None
        self.cache = None
        self.file_entry_requested = False
        self.visible = False
        while not self.file_entry_queue.empty():
            self.file_entry_queue.get_nowait()
        while not self.provider_queue.empty():
            self.provider_queue.get_nowait()
        while not self.tile_queue.empty():
            self.tile_queue.get_nowait()

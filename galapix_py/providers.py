from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .database_thread import DatabaseThread
from .jobs import JobHandle
from .models import FileEntry, TileRecord


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

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .database import Database
from .jobs import JobHandle, JobManager
from .models import FileEntry, TileRecord
from .tiling import generate_tiles_for_entry, preload_tile, probe_file_entry


FileCallback = Callable[[FileEntry], None]
TileCallback = Callable[[FileEntry, TileRecord], None]
SingleTileCallback = Callable[[TileRecord], None]


@dataclass(slots=True)
class RequestFile:
    url: str
    file_callback: FileCallback
    tile_callback: Optional[TileCallback]


@dataclass(slots=True)
class RequestTile:
    entry: FileEntry
    scale: int
    x: int
    y: int
    handle: JobHandle
    callback: SingleTileCallback


class DatabaseThread:
    def __init__(self, database: Database, jobs: JobManager) -> None:
        self.database = database
        self.jobs = jobs
        self.requests: queue.Queue[object] = queue.Queue()
        self.deliveries: queue.Queue[Callable[[], None]] = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="database-thread", daemon=True)
        self._generation_lock = threading.Lock()
        self._tile_generations: set[tuple[str, int]] = set()
        self._pending_tile_requests: dict[tuple[str, int], list[RequestTile]] = {}

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.requests.put(None)
        self.thread.join()

    def request_file(self, url: str, file_callback: FileCallback, tile_callback: Optional[TileCallback] = None) -> None:
        self.requests.put(RequestFile(url=url, file_callback=file_callback, tile_callback=tile_callback))

    def request_tile(self, entry: FileEntry, scale: int, x: int, y: int, callback: SingleTileCallback) -> JobHandle:
        handle = JobHandle()
        self.requests.put(RequestTile(entry=entry, scale=scale, x=x, y=y, handle=handle, callback=callback))
        return handle

    def poll_deliveries(self, limit: int = 128) -> None:
        processed = 0
        for _ in range(limit):
            try:
                callback = self.deliveries.get_nowait()
            except queue.Empty:
                break
            callback()
            processed += 1
        return processed

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                message = self.requests.get(timeout=0.1)
            except queue.Empty:
                continue
            if message is None:
                continue
            if isinstance(message, RequestFile):
                self._handle_request_file(message)
            elif isinstance(message, RequestTile):
                self._handle_request_tile(message)

    def _handle_request_file(self, message: RequestFile) -> None:
        entry = self.database.get_file_entry(message.url)
        if entry and self.database.file_exists_and_matches(entry):
            self.deliveries.put(lambda entry=entry: message.file_callback(entry))
            preload = self.database.get_tile(entry.file_id, entry.thumbnail_scale, 0, 0) if entry.file_id else None
            if preload is not None and message.tile_callback is not None:
                self.deliveries.put(lambda entry=entry, preload=preload: message.tile_callback(entry, preload))
            return
        if entry and entry.file_id is not None:
            self.database.delete_file_entry(entry.file_id)
            entry = None

        def generate() -> None:
            fresh = probe_file_entry(message.url)
            stored = self.database.store_file_entry(fresh)
            preload = preload_tile(stored)
            if preload is not None and stored.file_id is not None:
                self.database.store_tiles(stored.file_id, [preload])
            self.deliveries.put(lambda stored=stored: message.file_callback(stored))
            if preload is not None and message.tile_callback is not None:
                self.deliveries.put(lambda stored=stored, preload=preload: message.tile_callback(stored, preload))

        self.jobs.submit(generate)

    def _handle_request_tile(self, message: RequestTile) -> None:
        if message.entry.file_id is None or message.handle.is_aborted():
            return
        tile = self.database.get_tile(message.entry.file_id, message.scale, message.x, message.y)
        if tile is not None:
            self.deliveries.put(lambda tile=tile, handle=message.handle, callback=message.callback: None if handle.is_aborted() else callback(tile))
            return

        generation_key = (message.entry.url, message.scale)
        with self._generation_lock:
            self._pending_tile_requests.setdefault(generation_key, []).append(message)
            already_running = generation_key in self._tile_generations
            if not already_running:
                self._tile_generations.add(generation_key)

        if not already_running:
            def generate_scale() -> None:
                try:
                    tiles = list(generate_tiles_for_entry(message.entry, message.scale, message.scale))
                    self.database.store_tiles(message.entry.file_id, tiles)
                    tile_map = {(tile.scale, tile.x, tile.y): tile for tile in tiles}
                    with self._generation_lock:
                        pending = self._pending_tile_requests.pop(generation_key, [])
                    for pending_request in pending:
                        if pending_request.handle.is_aborted():
                            continue
                        tile = tile_map.get((pending_request.scale, pending_request.x, pending_request.y))
                        if tile is not None:
                            self.deliveries.put(
                                lambda tile=tile, handle=pending_request.handle, callback=pending_request.callback:
                                None if handle.is_aborted() else callback(tile)
                            )
                finally:
                    with self._generation_lock:
                        self._tile_generations.discard(generation_key)

            self.jobs.submit(generate_scale)

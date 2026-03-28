from __future__ import annotations

import concurrent.futures
import fnmatch
import tempfile
from pathlib import Path
from typing import Iterable

from .database import Database
from .models import ViewerOptions


class GalapixApp:
    def __init__(self, options: ViewerOptions) -> None:
        self.options = options

    def row_spacing(self) -> float:
        return 40.0 * max(1, self.options.spacing)

    def expand_paths(self, paths: Iterable[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            path = Path(raw).expanduser()
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
                        resolved = str(child.resolve())
                        if resolved not in seen:
                            seen.add(resolved)
                            results.append(resolved)
            elif path.exists():
                resolved = str(path.resolve())
                if resolved not in seen:
                            seen.add(resolved)
                            results.append(resolved)
        return results

    def expand_cleanup_paths(self, paths: Iterable[str]) -> list[str]:
        results: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            path = Path(raw).expanduser()
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file() and child.suffix.lower() in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
                        resolved = str(child.resolve())
                        if resolved not in seen:
                            seen.add(resolved)
                            results.append(resolved)
            else:
                resolved = str(path.resolve(strict=False))
                if resolved not in seen:
                    seen.add(resolved)
                    results.append(resolved)
        return results

    def view(self, paths: Iterable[str], patterns: Iterable[str] = ()) -> None:
        from .database_thread import DatabaseThread
        from .image import Image
        from .jobs import JobManager
        from .providers import DatabaseTileProvider, InMemoryTileProvider
        from .sdl_viewer import SDLViewer
        from .viewer import Viewer
        from .tiling import probe_file_entry
        from .workspace import Workspace

        jobs = JobManager(self.options.threads)
        workspace = Workspace()
        database = None
        db_thread = None

        def matches_patterns(path: str) -> bool:
            return not patterns or any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

        def build_memory_provider(url: str) -> InMemoryTileProvider:
            return InMemoryTileProvider(jobs, probe_file_entry(url))

        def resolve_database_entry(path: str):
            entry = database.get_file_entry(path)
            if entry is not None and database.file_exists_and_matches(entry):
                return entry
            if entry is not None and entry.file_id is not None:
                database.delete_file_entry(entry.file_id)
            return database.store_file_entry(probe_file_entry(path))

        if not self.options.memory_only:
            database = Database(self.options.database)
            db_thread = DatabaseThread(database, jobs)

            for pattern in patterns:
                for entry in database.list_files():
                    if fnmatch.fnmatch(entry.url, pattern):
                        image = Image(entry.url)
                        image.set_provider(DatabaseTileProvider(db_thread, entry))
                        workspace.add_image(image)

        loaded_workspace = False
        for path in self.expand_paths(paths):
            if Path(path).suffix.lower() == ".galapix":
                workspace.load(path)
                loaded_workspace = True
                continue
            image = Image(path)
            if self.options.memory_only:
                if matches_patterns(path):
                    image.set_provider(build_memory_provider(path))
                    workspace.add_image(image)
                continue
            entry = resolve_database_entry(path)
            image.set_provider(DatabaseTileProvider(db_thread, entry))
            workspace.add_image(image)

        if self.options.memory_only:
            for image in workspace.images:
                if image.provider is None:
                    image.set_provider(build_memory_provider(image.url))
        else:
            for image in workspace.images:
                if image.provider is None and Path(image.url).suffix.lower() != ".galapix":
                    image.set_provider(DatabaseTileProvider(db_thread, resolve_database_entry(image.url)))

        if not loaded_workspace:
            workspace.layout_row(spacing=self.row_spacing(), max_per_row=self.options.images_per_row)
            workspace.update(1.0)

        try:
            if db_thread is not None:
                db_thread.start()
            provider_factory = build_memory_provider if self.options.memory_only else None
            viewer = Viewer(self.options, workspace, db_thread, provider_factory=provider_factory)
            SDLViewer(
                viewer,
                fullscreen=self.options.fullscreen,
                validate_render=self.options.validate_render,
                validation_timeout=self.options.validation_timeout,
            ).run()
        finally:
            if db_thread is not None:
                db_thread.stop()
            jobs.shutdown()
            if database is not None:
                database.close()

    def prepare(self, paths: Iterable[str]) -> None:
        from .tiling import generate_tiles_for_entry, probe_file_entry

        def prepare_one(path: str, cached_entry, cached_min: int | None, cached_max: int | None):
            fresh = probe_file_entry(path)
            is_current = (
                cached_entry is not None
                and cached_entry.mtime_ns == fresh.mtime_ns
                and cached_entry.size_bytes == fresh.size_bytes
                and cached_entry.width == fresh.width
                and cached_entry.height == fresh.height
                and cached_entry.image_format == fresh.image_format
            )
            is_complete = (
                is_current
                and cached_min is not None
                and cached_max is not None
                and cached_min <= 0
                and cached_max >= fresh.thumbnail_scale
            )
            if is_complete:
                return path, fresh, True, []
            tiles = list(generate_tiles_for_entry(fresh, 0, fresh.thumbnail_scale))
            return path, fresh, False, tiles

        database = Database(self.options.database)
        try:
            expanded = self.expand_paths(paths)
            if not expanded:
                return

            worker_count = max(1, self.options.threads)
            with database.bulk_writes():
                with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                    pending: dict[concurrent.futures.Future, tuple[str, object | None]] = {}
                    path_iter = iter(expanded)

                    def submit_next() -> bool:
                        try:
                            path = next(path_iter)
                        except StopIteration:
                            return False
                        cached_entry = database.get_file_entry(path)
                        cached_min = cached_max = None
                        if cached_entry is not None and cached_entry.file_id is not None:
                            cached_min, cached_max = database.get_min_max_scale(cached_entry.file_id)
                        pending[executor.submit(prepare_one, path, cached_entry, cached_min, cached_max)] = (path, cached_entry)
                        return True

                    for _ in range(worker_count):
                        if not submit_next():
                            break

                    while pending:
                        done, _ = concurrent.futures.wait(
                            pending.keys(),
                            return_when=concurrent.futures.FIRST_COMPLETED,
                        )
                        for future in done:
                            path, cached_entry = pending.pop(future)
                            _, fresh, should_skip, tiles = future.result()
                            if should_skip:
                                submit_next()
                                continue
                            if cached_entry is not None and cached_entry.file_id is not None:
                                database.delete_file_entry(cached_entry.file_id, commit=False)
                            stored_entry = database.store_file_entry(fresh, commit=False)
                            database.store_tiles(stored_entry.file_id, tiles, commit=False)
                            submit_next()
        finally:
            database.close()

    def list_files(self) -> None:
        database = Database(self.options.database)
        try:
            for entry in database.list_files():
                print(entry.url)
        finally:
            database.close()

    def check(self) -> None:
        database = Database(self.options.database)
        try:
            for entry in database.list_files():
                status = "ok" if database.file_exists_and_matches(entry) else "missing-or-stale"
                print(f"{status}: {entry.url}")
        finally:
            database.close()

    def cleanup(self, paths: Iterable[str] = ()) -> None:
        database = Database(self.options.database)
        try:
            expanded = self.expand_cleanup_paths(paths)
            if not expanded:
                database.cleanup()
                return
            with database.bulk_writes():
                for path in expanded:
                    database.delete_file_by_url(path, commit=False)
        finally:
            database.close()

    def selfcheck(self, paths: Iterable[str]) -> None:
        from .image import Image
        from .tiling import generate_tiles_for_entry, probe_file_entry
        from .workspace import Workspace

        expanded = self.expand_paths(paths)
        if not expanded:
            raise RuntimeError("selfcheck requires at least one existing image path")

        with tempfile.TemporaryDirectory(prefix="galapix-py-selfcheck-") as tmpdir:
            database = Database(Path(tmpdir) / "db")
            try:
                stored_entries = []
                for path in expanded:
                    entry = database.store_file_entry(probe_file_entry(path))
                    stored_entries.append(entry)

                listed = database.list_files()
                assert len(listed) == len(stored_entries), "file count mismatch after metadata stage"

                for entry in stored_entries:
                    min_scale = max(0, entry.thumbnail_scale - 1)
                    max_scale = entry.thumbnail_scale
                    tiles = list(generate_tiles_for_entry(entry, min_scale, max_scale))
                    assert tiles, f"no tiles generated for {entry.url}"
                    database.store_tiles(entry.file_id, tiles)
                    thumb = database.get_tile(entry.file_id, entry.thumbnail_scale, 0, 0)
                    assert thumb is not None, f"thumbnail tile missing for {entry.url}"

                workspace = Workspace()
                for index, entry in enumerate(stored_entries[:3]):
                    image = Image(entry.url)
                    image.set_absolute(float(index * 100), float(index * 50), 0.5 + index * 0.1)
                    if index == 0:
                        image.selected = True
                    workspace.add_image(image)

                workspace_path = Path(tmpdir) / "workspace.galapix"
                workspace.save(workspace_path)

                loaded_workspace = Workspace()
                loaded_workspace.load(workspace_path)
                assert len(loaded_workspace.images) == len(workspace.images), "workspace image count mismatch after load"
                assert len(loaded_workspace.selected_images()) == len(workspace.selected_images()), "workspace selection mismatch after load"

                print("selfcheck: ok")
                print(f"  files: {len(stored_entries)}")
                print(f"  database: {Path(tmpdir) / 'db' / 'cache.sqlite3'}")
                print(f"  workspace: {workspace_path}")
            finally:
                database.close()

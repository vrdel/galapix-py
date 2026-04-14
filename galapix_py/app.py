from __future__ import annotations

import concurrent.futures
import re
import tempfile
import time
from pathlib import Path
from typing import Iterable

from .database import Database
from .models import ViewerOptions


class GalapixApp:
    def __init__(self, options: ViewerOptions) -> None:
        self.options = options

    def row_spacing(self) -> float:
        return 40.0 * max(1, self.options.spacing)

    def apply_initial_sort(self, workspace) -> None:
        if self.options.sort == "name":
            workspace.sort_by_name()
        elif self.options.sort == "name-reverse":
            workspace.sort_by_name(reverse=True)
        elif self.options.sort == "mtime":
            workspace.sort_by_mtime()
        elif self.options.sort == "mtime-reverse":
            workspace.sort_by_mtime(reverse=True)

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

    def compile_patterns(self, patterns: Iterable[str]) -> list[re.Pattern[str]]:
        pattern_list = list(patterns)
        if not pattern_list:
            return []
        flags = re.IGNORECASE if self.options.ignore_pattern_case else 0
        return [re.compile(pattern, flags) for pattern in pattern_list]

    def pattern_matches(self, path: str, patterns: list[re.Pattern[str]]) -> bool:
        if not patterns:
            return True
        return any(pattern.search(path) for pattern in patterns)

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
        compiled_patterns = self.compile_patterns(patterns)

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

            for entry in database.list_files():
                if self.pattern_matches(entry.url, compiled_patterns):
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
                if self.pattern_matches(path, compiled_patterns):
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
            self.apply_initial_sort(workspace)
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

    def prepare(self, paths: Iterable[str], patterns: Iterable[str] = ()) -> bool:
        from .tiling import generate_tiles_for_entry, probe_file_entry

        started_at = time.perf_counter()
        compiled_patterns = self.compile_patterns(patterns)

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
            tiles = list(
                generate_tiles_for_entry(
                    fresh,
                    0,
                    fresh.thumbnail_scale,
                    quality=self.options.jpeg_quality,
                )
            )
            return path, fresh, False, tiles

        database = Database(self.options.database)
        try:
            expanded = [path for path in self.expand_paths(paths) if self.pattern_matches(path, compiled_patterns)]
            if not expanded:
                return False

            worker_count = max(1, self.options.threads)
            skipped = 0
            prepared = 0
            stored_tiles = 0
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
                                skipped += 1
                                submit_next()
                                continue
                            if cached_entry is not None and cached_entry.file_id is not None:
                                database.delete_file_entry(cached_entry.file_id, commit=False)
                            stored_entry = database.store_file_entry(fresh, commit=False)
                            database.store_tiles(stored_entry.file_id, tiles, commit=False)
                            prepared += 1
                            stored_tiles += len(tiles)
                            submit_next()
            pending_count = len(expanded) - skipped
            print("galapix-py")
            print(f"  database: {database.path}")
            print(f"  discovered: {len(expanded)}")
            print(f"  skipped: {skipped}")
            print(f"  pending: {pending_count}")
            print(f"  threads: {worker_count}")
            print(f"  prepared: {prepared}")
            print(f"  stored_tiles: {stored_tiles}")
            print(f"  elapsed: {self._format_elapsed(time.perf_counter() - started_at)}")
            return stored_tiles > 0
        finally:
            database.close()

    def _format_elapsed(self, seconds: float) -> str:
        if seconds >= 60.0:
            return f"{seconds:.2f}s ({seconds / 60.0:.2f}m)"
        return f"{seconds:.2f}s"

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

    def cleanup(self, paths: Iterable[str] = (), patterns: Iterable[str] = ()) -> None:
        database = Database(self.options.database)
        try:
            compiled_patterns = self.compile_patterns(patterns)
            expanded = self.expand_cleanup_paths(paths)
            if compiled_patterns:
                if expanded:
                    expanded = [path for path in expanded if self.pattern_matches(Path(path).name, compiled_patterns)]
                else:
                    expanded = [
                        entry.url
                        for entry in database.list_files()
                        if self.pattern_matches(Path(entry.url).name, compiled_patterns)
                    ]
            if not expanded:
                if compiled_patterns:
                    self._print_cleanup_summary(database, 0, 0, 0, 0, had_patterns=True, full_cleanup=False)
                    return
                files_count = len(database.list_files())
                tile_count = sum(database.count_tiles_for_file(entry.file_id) for entry in database.list_files() if entry.file_id is not None)
                database.cleanup()
                self._print_cleanup_summary(
                    database,
                    files_count,
                    files_count,
                    tile_count,
                    tile_count,
                    had_patterns=False,
                    full_cleanup=True,
                )
                return
            matched_entries = []
            seen_urls: set[str] = set()
            for path in expanded:
                entry = database.get_file_entry(path)
                if entry is None or entry.file_id is None or entry.url in seen_urls:
                    continue
                seen_urls.add(entry.url)
                matched_entries.append(entry)
            matched_tiles = sum(database.count_tiles_for_file(entry.file_id) for entry in matched_entries if entry.file_id is not None)
            with database.bulk_writes():
                for entry in matched_entries:
                    database.delete_file_by_url(entry.url, commit=False)
            self._print_cleanup_summary(
                database,
                len(matched_entries),
                len(matched_entries),
                matched_tiles,
                matched_tiles,
                had_patterns=bool(compiled_patterns),
                full_cleanup=False,
            )
        finally:
            database.close()

    def _print_cleanup_summary(
        self,
        database: Database,
        matched_images: int,
        removed_images: int,
        matched_tiles: int,
        removed_tiles: int,
        *,
        had_patterns: bool,
        full_cleanup: bool,
    ) -> None:
        print("galapix-clean")
        print(f"  database: {database.path}")
        print(f"  mode: {'full-cache' if full_cleanup else 'selective'}")
        print(f"  pattern_filter: {'yes' if had_patterns else 'no'}")
        print(f"  matched_images: {matched_images}")
        print(f"  removed_images: {removed_images}")
        print(f"  matched_tiles: {matched_tiles}")
        print(f"  removed_tiles: {removed_tiles}")

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

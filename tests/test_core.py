from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import call, patch

import pyvips

from galapix_py.app import GalapixApp
from galapix_py.database import Database
from galapix_py.image import Image, ImageTileCache
from galapix_py.jobs import JobManager
from galapix_py.models import TileRecord, ViewerOptions
from galapix_py.providers import InMemoryTileProvider
from galapix_py.sdl_viewer import LiveRenderValidation
from galapix_py.tiling import generate_tiles_for_entry, probe_file_entry
from galapix_py.viewer import (
    FrameRenderStats,
    GL_CLAMP_TO_EDGE,
    GL_LINEAR,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S,
    GL_TEXTURE_WRAP_T,
    GL_UNPACK_ALIGNMENT,
    build_label_rgba,
    configure_texture_upload_state,
    filename_overlay_rect,
    overlay_label_text,
)
from galapix_py.workspace import Workspace


def make_test_jpeg(directory: Path, width: int = 96, height: int = 64) -> Path:
    image = pyvips.Image.black(width, height).bandjoin([64, 128])
    path = directory / "sample.jpg"
    image.jpegsave(str(path), Q=90)
    return path


class GalapixPyCoreTests(unittest.TestCase):
    def test_probe_and_tile_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = make_test_jpeg(Path(tmpdir))
            entry = probe_file_entry(image_path)
            self.assertEqual(entry.width, 96)
            self.assertEqual(entry.height, 64)
            tiles = list(generate_tiles_for_entry(entry, entry.thumbnail_scale, entry.thumbnail_scale))
            self.assertTrue(tiles)
            self.assertEqual(tiles[0].x, 0)
            self.assertEqual(tiles[0].y, 0)

    def test_database_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base)
            database = Database(base / "db")
            try:
                entry = database.store_file_entry(probe_file_entry(image_path))
                tiles = list(generate_tiles_for_entry(entry, entry.thumbnail_scale, entry.thumbnail_scale))
                database.store_tiles(entry.file_id, tiles)
                listed = database.list_files()
                self.assertEqual(len(listed), 1)
                tile = database.get_tile(entry.file_id, entry.thumbnail_scale, 0, 0)
                self.assertIsNotNone(tile)
            finally:
                database.close()

    def test_database_store_tiles_accepts_iterables(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base)
            database = Database(base / "db")
            try:
                entry = database.store_file_entry(probe_file_entry(image_path))

                def tiles():
                    for x in range(3):
                        yield TileRecord(entry.file_id, 0, x, 0, 16, 16, bytes([x + 1]))

                database.store_tiles(entry.file_id, tiles())
                self.assertEqual(database.get_tile(entry.file_id, 0, 0, 0).jpeg_bytes, b"\x01")
                self.assertEqual(database.get_tile(entry.file_id, 0, 1, 0).jpeg_bytes, b"\x02")
                self.assertEqual(database.get_tile(entry.file_id, 0, 2, 0).jpeg_bytes, b"\x03")
            finally:
                database.close()

    def test_database_bulk_writes_commit_multiple_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base)
            database = Database(base / "db")
            try:
                entry = probe_file_entry(image_path)
                with database.bulk_writes():
                    stored = database.store_file_entry(entry, commit=False)
                    database.store_tiles(
                        stored.file_id,
                        [TileRecord(stored.file_id, 0, 0, 0, 16, 16, b"tile")],
                        commit=False,
                    )
                listed = database.list_files()
                self.assertEqual(len(listed), 1)
                self.assertEqual(database.get_tile(listed[0].file_id, 0, 0, 0).jpeg_bytes, b"tile")
            finally:
                database.close()

    def test_cleanup_removes_only_selected_cached_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = make_test_jpeg(base, width=100, height=80)
            second = base / "sample-2.jpg"
            pyvips.Image.black(120, 90).bandjoin([32, 96]).jpegsave(str(second), Q=90)

            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)
            app.prepare([str(first), str(second)])
            app.cleanup([str(first)])

            database = Database(base / "db")
            try:
                self.assertIsNone(database.get_file_entry(str(first.resolve())))
                self.assertIsNotNone(database.get_file_entry(str(second.resolve())))
            finally:
                database.close()

    def test_cleanup_accepts_missing_file_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base, width=100, height=80)

            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)
            app.prepare([str(image_path)])
            image_path.unlink()
            app.cleanup([str(image_path)])

            database = Database(base / "db")
            try:
                self.assertIsNone(database.get_file_entry(str(image_path.resolve(strict=False))))
            finally:
                database.close()

    def test_cleanup_without_paths_clears_entire_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = make_test_jpeg(base, width=100, height=80)
            second = base / "sample-2.jpg"
            pyvips.Image.black(120, 90).bandjoin([32, 96]).jpegsave(str(second), Q=90)

            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)
            app.prepare([str(first), str(second)])
            app.cleanup()

            database = Database(base / "db")
            try:
                self.assertEqual(database.list_files(), [])
            finally:
                database.close()

    def test_prepare_all_tiles_skips_unchanged_cached_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base, width=320, height=240)
            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)

            app.prepare([str(image_path)])

            with patch("galapix_py.tiling.generate_tiles_for_entry", side_effect=AssertionError("should skip unchanged image")):
                app.prepare([str(image_path)])

    def test_prepare_all_tiles_regenerates_changed_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base, width=320, height=240)
            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)

            app.prepare([str(image_path)])
            time.sleep(0.01)
            pyvips.Image.black(400, 200).bandjoin([16, 200]).jpegsave(str(image_path), Q=90)

            original = generate_tiles_for_entry
            with patch("galapix_py.tiling.generate_tiles_for_entry", wraps=original) as wrapped:
                app.prepare([str(image_path)])
            self.assertTrue(wrapped.called)

    def test_prepare_all_tiles_rebuilds_when_cached_entry_dimensions_are_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base, width=320, height=240)
            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)

            app.prepare([str(image_path)])

            database = Database(base / "db")
            try:
                entry = database.get_file_entry(str(image_path))
                database.store_file_entry(
                    entry.__class__(
                        file_id=entry.file_id,
                        url=entry.url,
                        mtime_ns=entry.mtime_ns,
                        size_bytes=entry.size_bytes,
                        width=entry.width + 1,
                        height=entry.height,
                        image_format=entry.image_format,
                    )
                )
            finally:
                database.close()

            original = generate_tiles_for_entry
            with patch("galapix_py.tiling.generate_tiles_for_entry", wraps=original) as wrapped:
                app.prepare([str(image_path)])
            self.assertTrue(wrapped.called)

    def test_view_uncached_files_are_stored_before_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = make_test_jpeg(base, width=320, height=120)
            second = base / "sample-2.jpg"
            pyvips.Image.black(160, 300).bandjoin([32, 96]).jpegsave(str(second), Q=90)

            options = ViewerOptions(database=base / "db")
            app = GalapixApp(options)

            class StopViewer(Exception):
                pass

            captured = {}

            def fake_run(self) -> None:
                captured["workspace"] = self.viewer.workspace
                raise StopViewer()

            with patch("galapix_py.sdl_viewer.SDLViewer.run", new=fake_run):
                with self.assertRaises(StopViewer):
                    app.view([str(first), str(second)])

            workspace = captured["workspace"]
            self.assertEqual(len(workspace.images), 2)
            self.assertIsNotNone(workspace.images[0].provider)
            self.assertIsNotNone(workspace.images[1].provider)
            self.assertEqual(workspace.images[0].size(), (320, 120))
            self.assertEqual(workspace.images[1].size(), (160, 300))
            self.assertLess(workspace.images[0].placement.x, workspace.images[1].placement.x)

    def test_workspace_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            workspace = Workspace()
            first = Image("/tmp/a.jpg")
            first.set_absolute(10.0, 20.0, 0.5)
            first.selected = True
            second = Image("/tmp/b.jpg")
            second.set_absolute(30.0, 40.0, 0.75)
            workspace.add_image(first)
            workspace.add_image(second)

            path = base / "workspace.galapix"
            workspace.save(path)

            loaded = Workspace()
            loaded.load(path)

            self.assertEqual(len(loaded.images), 2)
            self.assertEqual(len(loaded.selected_images()), 1)
            self.assertAlmostEqual(loaded.images[0].placement.x, 10.0)
            self.assertAlmostEqual(loaded.images[1].placement.scale, 0.75)

    def test_workspace_layout_row_keeps_images_left_to_right(self) -> None:
        workspace = Workspace()
        first = Image("/tmp/a.jpg")
        first.set_absolute(0.0, 0.0, 1.0)
        second = Image("/tmp/b.jpg")
        second.set_absolute(0.0, 0.0, 1.0)
        workspace.add_image(first)
        workspace.add_image(second)

        workspace.layout_row()
        workspace.update(1.0)

        self.assertLess(workspace.images[0].placement.x, workspace.images[1].placement.x)
        self.assertAlmostEqual(workspace.images[0].placement.y, workspace.images[1].placement.y)
        self.assertAlmostEqual(
            (workspace.images[0].placement.x + workspace.images[1].placement.x) / 2.0,
            0.0,
        )

    def test_workspace_layout_row_wraps_after_max_per_row(self) -> None:
        workspace = Workspace()
        for index in range(4):
            image = Image(f"/tmp/{index}.jpg")
            image.set_absolute(0.0, 0.0, 1.0)
            workspace.add_image(image)

        workspace.layout_row(max_per_row=2)
        workspace.update(1.0)

        self.assertAlmostEqual(workspace.images[0].placement.y, workspace.images[1].placement.y)
        self.assertAlmostEqual(workspace.images[2].placement.y, workspace.images[3].placement.y)
        self.assertLess(workspace.images[0].placement.y, workspace.images[2].placement.y)
        self.assertLess(workspace.images[0].placement.x, workspace.images[1].placement.x)
        self.assertLess(workspace.images[2].placement.x, workspace.images[3].placement.x)
        self.assertAlmostEqual(
            (workspace.images[0].placement.x + workspace.images[1].placement.x) / 2.0,
            0.0,
        )
        self.assertAlmostEqual(
            (workspace.images[2].placement.x + workspace.images[3].placement.x) / 2.0,
            0.0,
        )

    def test_workspace_layout_row_auto_wraps_nine_images_to_three_per_row(self) -> None:
        workspace = Workspace()
        for index in range(9):
            image = Image(f"/tmp/{index}.jpg")
            image.set_absolute(0.0, 0.0, 1.0)
            workspace.add_image(image)

        workspace.layout_row()
        workspace.update(1.0)

        first_row = workspace.images[:3]
        second_row = workspace.images[3:6]
        third_row = workspace.images[6:9]
        self.assertTrue(all(image.placement.y == first_row[0].placement.y for image in first_row))
        self.assertTrue(all(image.placement.y == second_row[0].placement.y for image in second_row))
        self.assertTrue(all(image.placement.y == third_row[0].placement.y for image in third_row))
        self.assertLess(first_row[0].placement.y, second_row[0].placement.y)
        self.assertLess(second_row[0].placement.y, third_row[0].placement.y)

    def test_workspace_layout_row_auto_wraps_twenty_five_images_to_five_per_row(self) -> None:
        workspace = Workspace()
        for index in range(25):
            image = Image(f"/tmp/{index}.jpg")
            image.set_absolute(0.0, 0.0, 1.0)
            workspace.add_image(image)

        workspace.layout_row()
        workspace.update(1.0)

        self.assertAlmostEqual(workspace.images[0].placement.y, workspace.images[4].placement.y)
        self.assertAlmostEqual(workspace.images[5].placement.y, workspace.images[9].placement.y)
        self.assertLess(workspace.images[0].placement.y, workspace.images[5].placement.y)
        self.assertLess(workspace.images[5].placement.y, workspace.images[10].placement.y)

    def test_app_selfcheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base)
            options = ViewerOptions(database=base / "unused-db")
            app = GalapixApp(options)
            app.selfcheck([str(image_path)])

    def test_expand_paths_preserves_cli_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = make_test_jpeg(base, width=96, height=64)
            second = base / "sample-2.jpg"
            pyvips.Image.black(64, 64).bandjoin([32, 96]).jpegsave(str(second), Q=90)

            options = ViewerOptions(database=base / "unused-db")
            app = GalapixApp(options)
            expanded = app.expand_paths([str(second), str(first)])

            self.assertEqual(expanded, [str(second.resolve()), str(first.resolve())])

    def test_cli_accepts_images_per_row(self) -> None:
        from galapix_py.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["view", "--images-per-row", "10"])
        self.assertEqual(args.images_per_row, 10)

    def test_cli_view_accepts_show_filenames(self) -> None:
        from galapix_py.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["view", "--show-filenames"])
        self.assertTrue(args.show_filenames)

    def test_cli_defaults_images_per_row_to_auto(self) -> None:
        from galapix_py.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["view"])
        self.assertIsNone(args.images_per_row)

    def test_cli_view_accepts_view_only_flags(self) -> None:
        from galapix_py.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "view",
                "--geometry",
                "1920x1080",
                "--fullscreen",
                "--memory-only",
            ]
        )
        self.assertEqual(args.geometry, "1920x1080")
        self.assertTrue(args.fullscreen)
        self.assertTrue(args.memory_only)

    def test_cli_prepare_rejects_view_only_flags(self) -> None:
        from galapix_py.cli import build_parser

        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["prepare", "--fullscreen"])

    def test_cli_cleanup_accepts_paths(self) -> None:
        from galapix_py.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["cleanup", "/tmp/a.jpg"])
        self.assertEqual(args.paths, ["/tmp/a.jpg"])

    def test_live_render_validation_waits_for_textured_tiles(self) -> None:
        validation = LiveRenderValidation(timeout=1.0)

        success, message = validation.observe(0.2, FrameRenderStats(visible_images=1, placeholder_tiles=1))
        self.assertFalse(success)
        self.assertIsNone(message)

        success, message = validation.observe(0.4, FrameRenderStats(visible_images=1, textured_tiles=2))
        self.assertTrue(success)
        self.assertIn("passed", message)

    def test_live_render_validation_times_out_without_textures(self) -> None:
        validation = LiveRenderValidation(timeout=0.5)

        success, message = validation.observe(0.5, FrameRenderStats(visible_images=1, placeholder_tiles=3))
        self.assertFalse(success)
        self.assertIn("timed out", message)

    def test_in_memory_tile_provider_generates_tile_without_database_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base, width=320, height=240)
            entry = probe_file_entry(image_path)
            jobs = JobManager(1)
            try:
                provider = InMemoryTileProvider(jobs, entry)
                delivered: list[object] = []
                delivered_event = threading.Event()

                handle = provider.request_tile(
                    entry.thumbnail_scale,
                    0,
                    0,
                    lambda tile: (delivered.append(tile), delivered_event.set()),
                )
                self.assertIsNotNone(handle.future)
                handle.future.result(timeout=3)
                self.assertTrue(delivered_event.wait(timeout=1))
                self.assertEqual(len(delivered), 1)
                self.assertEqual(delivered[0].x, 0)
                self.assertEqual(delivered[0].y, 0)
            finally:
                jobs.shutdown()

    def test_in_memory_tile_provider_evicts_old_scaled_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base, width=1024, height=1024)
            entry = probe_file_entry(image_path)
            jobs = JobManager(1)
            try:
                provider = InMemoryTileProvider(jobs, entry, max_cached_scales=2)
                provider._get_scaled_image(0)
                provider._get_scaled_image(1)
                provider._get_scaled_image(2)
                self.assertEqual(list(provider._scaled_images.keys()), [1, 2])

                provider._get_scaled_image(1)
                provider._get_scaled_image(3)
                self.assertEqual(list(provider._scaled_images.keys()), [1, 3])
            finally:
                jobs.shutdown()

    def test_image_tile_cache_evicts_oldest_tiles(self) -> None:
        class DummyProvider:
            def request_tile(self, scale: int, x: int, y: int, callback):
                raise AssertionError("request_tile should not be called")

            def get_size(self) -> tuple[int, int]:
                return (256, 256)

            def get_max_scale(self) -> int:
                return 0

        cache = ImageTileCache(DummyProvider(), max_cached_tiles=2)
        cache.receive_tile(TileRecord(None, 0, 0, 0, 64, 64, b"a"))
        cache.receive_tile(TileRecord(None, 0, 1, 0, 64, 64, b"b"))
        cache.process_queue()
        self.assertEqual(list(cache.tiles.keys()), [(0, 0, 0), (0, 1, 0)])

        self.assertIsNotNone(cache.get_cached_tile(0, 0, 0))
        cache.receive_tile(TileRecord(None, 0, 2, 0, 64, 64, b"c"))
        cache.process_queue()

        self.assertEqual(list(cache.tiles.keys()), [(0, 0, 0), (0, 2, 0)])

    def test_configure_texture_upload_state_sets_alignment_and_clamp(self) -> None:
        with (
            patch("galapix_py.viewer.glPixelStorei") as pixel_store,
            patch("galapix_py.viewer.glTexParameteri") as tex_parameter,
        ):
            configure_texture_upload_state()

        pixel_store.assert_called_once_with(GL_UNPACK_ALIGNMENT, 1)
        tex_parameter.assert_has_calls(
            [
                call(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR),
                call(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR),
                call(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE),
                call(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE),
            ]
        )
        self.assertEqual(len(tex_parameter.call_args_list), 4)

    def test_overlay_label_text_uses_basename_and_truncates(self) -> None:
        self.assertEqual(overlay_label_text("/tmp/example.jpg"), "example.jpg")
        self.assertEqual(
            overlay_label_text("/tmp/" + ("a" * 60) + ".jpg", max_chars=20),
            ("a" * 17) + "...",
        )

    def test_build_label_rgba_creates_white_text_mask(self) -> None:
        rgba, width, height = build_label_rgba("sample.jpg")

        self.assertEqual(rgba.shape, (height, width, 4))
        self.assertEqual(rgba.dtype.name, "uint8")
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertEqual(int(rgba[:, :, 3].max()), 255)
        self.assertEqual(tuple(rgba[:, :, :3].max(axis=(0, 1))), (255, 255, 255))

    def test_filename_overlay_rect_places_label_above_image(self) -> None:
        rect = filename_overlay_rect(100.0, 200.0, 320.0, 80.0, 24.0)
        self.assertEqual(rect, (100.0, 172.0, 180.0, 196.0))

    def test_filename_overlay_rect_skips_very_narrow_images(self) -> None:
        self.assertIsNone(filename_overlay_rect(100.0, 200.0, 130.0, 80.0, 24.0))


if __name__ == "__main__":
    unittest.main()

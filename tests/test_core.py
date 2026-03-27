from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import call, patch

import pyvips

from galapix_py.app import GalapixApp
from galapix_py.database import Database
from galapix_py.image import Image
from galapix_py.jobs import JobManager
from galapix_py.models import ViewerOptions
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
    configure_texture_upload_state,
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


if __name__ == "__main__":
    unittest.main()

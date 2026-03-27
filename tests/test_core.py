from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pyvips

from galapix_py.app import GalapixApp
from galapix_py.database import Database
from galapix_py.image import Image
from galapix_py.models import ViewerOptions
from galapix_py.tiling import generate_tiles_for_entry, probe_file_entry
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

    def test_app_selfcheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            image_path = make_test_jpeg(base)
            options = ViewerOptions(database=base / "unused-db")
            app = GalapixApp(options)
            app.selfcheck([str(image_path)])


if __name__ == "__main__":
    unittest.main()

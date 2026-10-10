from pathlib import Path
import unittest
from unittest.mock import patch

from galapix_py.models import ViewerOptions
from galapix_py.viewer import LabelTexture, Viewer, _measure_text
from galapix_py.workspace import Workspace


class ViewerRenderingTests(unittest.TestCase):
    def setUp(self):
        self.viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-render-test")), Workspace(), None)

    def test_overlay_cache_reuses_and_replaces_without_growing(self):
        with (
            patch.object(self.viewer, "_create_text_texture", side_effect=[
                LabelTexture(i, 50, 20) for i in range(1, 23)
            ]) as create,
            patch("galapix_py.viewer.glDeleteTextures") as delete,
        ):
            first = self.viewer._get_overlay_texture("query", "cat", 16)
            self.assertIs(self.viewer._get_overlay_texture("query", "cat", 16), first)
            create.assert_called_once()
            delete.assert_not_called()
            for index in range(20):
                self.viewer._get_overlay_texture("query", str(index), 16)
            self.assertEqual(len(self.viewer.overlay_textures), 1)
            self.assertEqual(delete.call_count, 20)
            self.viewer._get_overlay_texture("query", "19", 18)
            self.assertEqual(create.call_count, 22)
            delete.assert_called_with([21])

    def test_failed_replacement_preserves_cached_texture(self):
        cached = LabelTexture(5, 50, 20)
        self.viewer.overlay_textures["query"] = ("cat", 16, cached)
        with (
            patch.object(self.viewer, "_create_text_texture", side_effect=RuntimeError("upload failed")),
            patch("galapix_py.viewer.glDeleteTextures") as delete,
        ):
            with self.assertRaises(RuntimeError):
                self.viewer._get_overlay_texture("query", "dog", 16)
            self.assertIs(self.viewer._get_overlay_texture("query", "cat", 16), cached)
            delete.assert_not_called()

    def test_cache_clear_and_shutdown_release_overlay_textures(self):
        for action in (self.viewer.clear_all_caches, self.viewer.release_gl_resources):
            with self.subTest(action=action.__name__):
                self.viewer.label_textures["file"] = LabelTexture(1, 50, 20)
                self.viewer.overlay_textures["query"] = ("cat", 16, LabelTexture(2, 50, 20))
                with patch("galapix_py.viewer.glDeleteTextures") as delete:
                    action()
                    action()
                delete.assert_called_once_with([1, 2])
                self.assertEqual(self.viewer.label_textures, {})
                self.assertEqual(self.viewer.overlay_textures, {})

    def test_search_measures_without_uploading_invisible_text(self):
        with (
            patch.object(self.viewer, "_create_text_texture", return_value=LabelTexture(1, 50, 20)) as create,
            patch.object(self.viewer, "_draw_solid_rect"),
            patch.object(self.viewer, "_draw_text_label"),
            patch("galapix_py.viewer.glDeleteTextures"),
        ):
            self.viewer._draw_search_overlay()
            self.viewer._draw_search_overlay()
            self.assertEqual(create.call_count, 2)  # Only title and visible query.
            self.viewer.search_query = "cat"
            self.viewer._draw_search_overlay()
            self.assertEqual(create.call_count, 3)
        self.assertTrue(all(value > 0 for value in _measure_text("Mg", 16)))

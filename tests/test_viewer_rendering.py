import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import sdl2

from galapix_py.image import Image, ImageTileCache
from galapix_py.models import ViewerOptions
from galapix_py.sdl_viewer import SDLViewer
from galapix_py.viewer import FrameRenderStats, LabelTexture, Viewer, _measure_text
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

    def test_viewport_keeps_logical_coordinates_and_physical_density(self):
        with patch("galapix_py.viewer.glViewport") as viewport:
            self.viewer.set_viewport(400, 300, 800, 600)
            viewport.assert_called_once_with(0, 0, 800, 600)
            self.assertEqual((self.viewer.viewport_width, self.viewer.viewport_height), (400, 300))
            self.assertEqual(self.viewer.drawable_scale, 2)
            self.viewer.set_viewport(400, 300)
            self.assertEqual(self.viewer.drawable_scale, 1)
            self.viewer.set_viewport(0, 0, 0, 0)
            viewport.assert_called_with(0, 0, 1, 1)
            self.assertEqual(self.viewer.drawable_scale, 1)

    def test_sdl_syncs_both_sizes_on_resize_and_display_change(self):
        viewer = Mock()
        backend = SDLViewer(viewer)

        def size_result(width, height):
            def get_size(window, w, h):
                ctypes.cast(w, ctypes.POINTER(ctypes.c_int))[0] = width
                ctypes.cast(h, ctypes.POINTER(ctypes.c_int))[0] = height
            return get_size

        with (
            patch("galapix_py.sdl_viewer.sdl2.SDL_GetWindowSize", side_effect=size_result(400, 300)),
            patch("galapix_py.sdl_viewer.sdl2.SDL_GL_GetDrawableSize", side_effect=size_result(800, 600)),
        ):
            for event_type in (sdl2.SDL_WINDOWEVENT_SIZE_CHANGED, sdl2.SDL_WINDOWEVENT_MOVED,
                               sdl2.SDL_WINDOWEVENT_DISPLAY_CHANGED, sdl2.SDL_WINDOWEVENT_RESTORED):
                viewer.reset_mock()
                backend._process_event(SimpleNamespace(type=sdl2.SDL_WINDOWEVENT,
                                                       window=SimpleNamespace(event=event_type)))
                viewer.set_viewport.assert_called_once_with(400, 300, 800, 600)

    def test_high_dpi_requests_higher_resolution_tiles(self):
        provider = Mock()
        provider.get_size.return_value = (1024, 1024)
        provider.get_max_scale.return_value = 4
        image = Image("/tmp/sample.png")
        image.provider = provider
        image.cache = ImageTileCache(provider)
        image.set_absolute(512, 512, 1)
        self.viewer.state.scale = 0.25
        with patch("galapix_py.viewer.glViewport"), patch.object(self.viewer, "_draw_solid_rect"):
            self.viewer.set_viewport(400, 300, 400, 300)
            self.viewer._draw_image(image, FrameRenderStats())
            self.assertEqual(image.cache.focus_scale, 2)
            self.viewer.set_viewport(400, 300, 800, 600)
            self.viewer._draw_image(image, FrameRenderStats())
            self.assertEqual(image.cache.focus_scale, 1)

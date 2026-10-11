"""Opt-in pixel tests against a real OpenGL core context.

On Linux without a display:
SDL_VIDEODRIVER=offscreen PYOPENGL_PLATFORM=egl GALAPIX_TEST_GL=1 \
    python -m unittest discover -s tests -p test_renderer_gl.py -v
"""

from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from OpenGL import GL
from PIL import Image as PILImage
import sdl2

from galapix_py.models import TileRecord, ViewerOptions
from galapix_py.image import Image, ImageTileCache
from galapix_py.renderer import RectangleRenderer
from galapix_py.render_profile import RenderProfiler
from galapix_py.tile_textures import TileTextureArrays
from galapix_py.viewer import TextureCache, Viewer
from galapix_py.workspace import Workspace


@unittest.skipUnless(os.environ.get("GALAPIX_TEST_GL") == "1", "set GALAPIX_TEST_GL=1 for real GL tests")
class RendererGLTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO), 0, sdl2.SDL_GetError())
        self.addCleanup(sdl2.SDL_Quit)
        for attribute, value in (
            (sdl2.SDL_GL_CONTEXT_MAJOR_VERSION, 3),
            (sdl2.SDL_GL_CONTEXT_MINOR_VERSION, 3),
            (sdl2.SDL_GL_CONTEXT_PROFILE_MASK, sdl2.SDL_GL_CONTEXT_PROFILE_CORE),
            (sdl2.SDL_GL_CONTEXT_FLAGS, sdl2.SDL_GL_CONTEXT_FORWARD_COMPATIBLE_FLAG),
        ):
            self.assertEqual(sdl2.SDL_GL_SetAttribute(attribute, value), 0)
        window = sdl2.SDL_CreateWindow(b"renderer test", 0, 0, 64, 64,
                                       sdl2.SDL_WINDOW_OPENGL | sdl2.SDL_WINDOW_HIDDEN)
        self.assertTrue(window, sdl2.SDL_GetError())
        self.addCleanup(sdl2.SDL_DestroyWindow, window)
        context = sdl2.SDL_GL_CreateContext(window)
        self.assertTrue(context, sdl2.SDL_GetError())
        self.addCleanup(sdl2.SDL_GL_DeleteContext, context)
        self.renderer = RectangleRenderer()
        self.addCleanup(self.renderer.close)
        self.cache = TextureCache()
        self.addCleanup(self.cache.clear)
        GL.glViewport(0, 0, 64, 64)
        GL.glClearColor(0, 0, 0, 1)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        self.renderer.begin_frame(64, 64)

    def pixel(self, x, y):
        # The viewer uses a top-left origin, unlike glReadPixels.
        return tuple(GL.glReadPixels(x, 63 - y, 1, 1, GL.GL_RGB, GL.GL_UNSIGNED_BYTE))

    def texture(self, image):
        data = BytesIO()
        image.save(data, format="PNG")
        source = SimpleNamespace(url=str(len(self.cache.textures)))
        return self.cache.get_or_create(source, 0, 0, 0, data.getvalue())

    def test_coordinates_and_resize(self):
        self.renderer.draw(0, 0, 32, 32, color=(1, 0, 0, 1))
        self.assertEqual(self.pixel(8, 8), (255, 0, 0))
        self.assertEqual(self.pixel(8, 56), (0, 0, 0))
        self.assertEqual(self.pixel(56, 8), (0, 0, 0))
        self.renderer.begin_frame(128, 128)
        self.renderer.draw(64, 64, 128, 128, color=(0, 1, 0, 1))
        self.assertEqual(self.pixel(48, 48), (0, 255, 0))
        self.renderer.begin_frame(0, 0)
        self.assertEqual(GL.glGetError(), GL.GL_NO_ERROR)

    def test_texture_orientation_and_cropped_parent_uv(self):
        pixels = np.zeros((8, 8, 3), dtype=np.uint8)
        pixels[:4, :4] = (255, 0, 0)
        pixels[:4, 4:] = (0, 255, 0)
        pixels[4:, :4] = (0, 0, 255)
        pixels[4:, 4:] = (255, 255, 0)
        texture = self.texture(PILImage.fromarray(pixels))
        self.renderer.draw(0, 0, 64, 64, texture=texture)
        for point, color in (((8, 8), (255, 0, 0)), ((56, 8), (0, 255, 0)),
                             ((8, 56), (0, 0, 255)), ((56, 56), (255, 255, 0))):
            self.assertEqual(self.pixel(*point), color)
        self.renderer.draw(0, 0, 64, 64, texture=texture, uv=(0.5, 0, 1, 0.5))
        self.assertEqual(self.pixel(32, 32), (0, 255, 0))

    def test_grayscale_and_odd_width_rgb_uploads(self):
        for mode, color, expected in (("L", 137, (137, 137, 137)),
                                      ("RGB", (17, 99, 231), (17, 99, 231))):
            with self.subTest(mode=mode):
                texture = self.texture(PILImage.new(mode, (3, 5), color))
                self.renderer.draw(0, 0, 64, 64, texture=texture)
                self.assertEqual(self.pixel(32, 32), expected)

    def test_alpha_and_solid_draw_after_texture(self):
        self.renderer.draw(0, 0, 64, 64, color=(0, 0, 1, 1))
        texture = self.texture(PILImage.new("RGBA", (3, 3), (255, 0, 0, 128)))
        self.renderer.draw(0, 0, 64, 64, texture=texture)
        self.assertEqual(self.pixel(32, 32), (128, 0, 127))
        self.renderer.draw(0, 0, 64, 64, color=(0, 1, 0, 0.5))
        for actual, expected in zip(self.pixel(32, 32), (64, 128, 63)):
            self.assertLessEqual(abs(actual - expected), 1)

    def test_outline_and_resource_deletion(self):
        self.renderer.draw(8.5, 8.5, 55.5, 55.5, color=(1, 1, 1, 1), outline=True)
        self.assertEqual(self.pixel(8, 32), (255, 255, 255))
        self.assertEqual(self.pixel(32, 32), (0, 0, 0))
        self.renderer.end_frame()
        program, vao, vbo = self.renderer.program, self.renderer.vao, self.renderer.vbo
        self.renderer.close()
        self.renderer.close()
        self.assertFalse(GL.glIsProgram(program))
        self.assertFalse(GL.glIsVertexArray(vao))
        self.assertFalse(GL.glIsBuffer(vbo))

    def test_viewer_text_and_search_overlays(self):
        viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-gl-test")), Workspace(), None)
        self.addCleanup(viewer.release_gl_resources)
        viewer.set_viewport(64, 64)
        viewer.search_active = True
        viewer.search_query = "test"
        viewer.keyboard_selection_active = True
        with patch.object(viewer, "_create_text_texture", wraps=viewer._create_text_texture) as create:
            viewer.draw()
            self.assertEqual(create.call_count, 3)
            viewer.draw()
            self.assertEqual(create.call_count, 3)
            old_query = viewer.overlay_textures["search_query"][2].texture_id
            viewer.search_query = "changed"
            viewer.draw()
            self.assertEqual(create.call_count, 4)
            self.assertFalse(GL.glIsTexture(old_query))
        self.assertEqual(GL.glGetError(), GL.GL_NO_ERROR)
        pixels = GL.glReadPixels(0, 0, 64, 64, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        self.assertGreater(len(set(pixels)), 1)
        viewer.release_gl_resources()
        viewer.draw()  # Resources can be recreated in the current context.
        self.assertEqual(GL.glGetError(), GL.GL_NO_ERROR)

    def test_high_dpi_viewport_uses_entire_framebuffer(self):
        viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-gl-test")), Workspace(), None)
        self.addCleanup(viewer.release_gl_resources)
        viewer.set_viewport(32, 32, 64, 64)
        self.renderer.begin_frame(viewer.viewport_width, viewer.viewport_height)
        self.renderer.draw(16, 16, 32, 32, color=(0, 1, 0, 1))
        self.assertEqual(self.pixel(60, 60), (0, 255, 0))
        self.assertEqual(self.pixel(24, 24), (0, 0, 0))
        self.assertEqual(viewer.state.screen_to_world(16, 16), (16, 16))

    def test_opt_in_profiling_counts_draws_uploads_and_gpu_samples(self):
        viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-gl-test"), profile_render=True), Workspace(), None)
        self.addCleanup(viewer.release_gl_resources)
        viewer.set_viewport(64, 64)
        viewer.search_active = True
        for _ in range(3):
            viewer.draw()
            # Only the test waits; normal profiling always polls availability.
            GL.glFinish()
        totals = viewer.profiler.totals
        self.assertEqual(totals.frames, 3)
        self.assertEqual(totals.uploads, 2)
        self.assertEqual(totals.draw_calls, 15)
        self.assertEqual(totals.textured_draws, 6)
        self.assertGreaterEqual(totals.gpu_samples, 2)
        self.assertGreater(totals.cpu_ms, 0)
        self.assertGreater(totals.upload_bytes, 0)
        self.assertEqual(GL.glGetError(), GL.GL_NO_ERROR)

    def test_default_rendering_does_not_allocate_timer_queries(self):
        viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-gl-test")), Workspace(), None)
        self.addCleanup(viewer.release_gl_resources)
        viewer.set_viewport(64, 64)
        with patch("galapix_py.render_profile.GL.glGenQueries") as queries:
            viewer.draw()
            queries.assert_not_called()

    def test_profiling_counts_tile_uploads_only_on_cache_miss(self):
        self.cache.profiler = RenderProfiler()
        texture = self.texture(PILImage.new("RGB", (3, 5), (12, 34, 56)))
        cached = self.cache.get_or_create(SimpleNamespace(url="0"), 0, 0, 0, b"unused cached data")
        self.assertEqual(cached, texture)
        self.assertEqual(self.cache.profiler.totals.uploads, 1)
        self.assertEqual(self.cache.profiler.totals.upload_bytes, 45)

    def test_uniform_reuse_handles_state_changes_and_texture_uploads(self):
        red = self.texture(PILImage.new("RGB", (3, 3), (255, 0, 0)))
        with (
            patch("galapix_py.renderer.GL.glUniform4f", wraps=GL.glUniform4f) as uniform4,
            patch("galapix_py.renderer.GL.glUniform1i", wraps=GL.glUniform1i) as uniform1,
        ):
            self.renderer.draw(0, 0, 32, 32, texture=red)
            self.renderer.draw(32, 0, 64, 32, texture=red)
            # Two positions, one UV, one color and one textured flag.
            self.assertEqual(uniform4.call_count, 4)
            self.assertEqual(uniform1.call_count, 1)
            # A separate upload changes the texture binding; the next draw
            # must bind its own texture even when its uniforms are cached.
            self.texture(PILImage.new("RGB", (3, 3), (0, 0, 255)))
            self.renderer.draw(0, 32, 32, 64, texture=red)
            self.assertEqual(self.pixel(16, 48), (255, 0, 0))
            self.renderer.draw(32, 32, 64, 64, color=(0, 1, 0, 1))
            self.assertEqual(self.pixel(48, 48), (0, 255, 0))
            self.renderer.draw(32, 32, 64, 64, texture=red)
            self.assertEqual(self.pixel(48, 48), (255, 0, 0))

    def test_solid_batches_match_immediate_alpha_and_texture_order(self):
        texture = self.texture(PILImage.new("RGBA", (3, 3), (80, 120, 240, 180)))

        def scene(renderer):
            renderer.begin_frame(64, 64)
            renderer.draw(0, 0, 64, 64, texture=texture)
            for i in range(6):
                renderer.draw(i * 3, i * 3, 50, 50, color=(1, i / 6, 0, 0.25))
            # Texture and outline draws must flush older fills first.
            renderer.draw(16, 16, 56, 56, texture=texture)
            for i in range(4):
                renderer.draw(8, 8, 60, 60, color=(0, 0.2 * i, 1, 0.3))
            renderer.draw(8.5, 8.5, 55.5, 55.5, color=(1, 1, 1, 1), outline=True)
            renderer.draw(0, 0, 8, 8, color=(0, 1, 0, 1))
            renderer.end_frame()
            return np.frombuffer(GL.glReadPixels(0, 0, 64, 64, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE), dtype=np.uint8)

        expected = scene(self.renderer)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        profiler = RenderProfiler()
        batched = RectangleRenderer(profiler, batch_solids=True)
        self.addCleanup(batched.close)
        actual = scene(batched)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(profiler.totals.instanced_rectangles, 10)
        self.assertEqual(profiler.totals.draw_calls, 6)
        self.assertEqual(profiler.totals.instance_upload_bytes, 10 * 32)

    def test_solid_batch_limit_and_instance_buffer_cleanup(self):
        profiler = RenderProfiler()
        batched = RectangleRenderer(profiler, batch_solids=True)
        self.addCleanup(batched.close)
        batched.begin_frame(64, 64)
        for _ in range(batched.MAX_SOLID_BATCH + 3):
            batched.draw(0, 0, 32, 32, color=(1, 0, 0, 1))
        self.assertEqual(len(batched._solid_rects), 3)
        batched.end_frame()
        self.assertEqual(self.pixel(16, 16), (255, 0, 0))
        self.assertEqual(profiler.totals.draw_calls, 4)
        self.assertEqual(profiler.totals.instanced_rectangles, batched.MAX_SOLID_BATCH)
        instance_vbo = batched.instance_vbo
        batched.close()
        batched.close()
        self.assertFalse(GL.glIsBuffer(instance_vbo))

    def test_viewer_batches_visible_placeholders(self):
        workspace = Workspace()
        for index in range(100):
            image = Image(f"/tmp/placeholder-{index}.png")
            image.file_entry_requested = True
            image.set_absolute(3 + (index % 10) * 6, 3 + (index // 10) * 6, 0.02)
            workspace.add_image(image)
        viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-gl-test"), profile_render=True), workspace, None)
        self.addCleanup(viewer.release_gl_resources)
        viewer.set_viewport(64, 64)
        stats = viewer.draw()
        self.assertEqual(stats.placeholder_tiles, 100)
        self.assertEqual(viewer.profiler.totals.draw_calls, 1)
        self.assertEqual(viewer.profiler.totals.instanced_rectangles, 100)
        self.assertEqual(GL.glGetError(), GL.GL_NO_ERROR)

    def test_array_tiles_match_2d_textures_with_cropping_alpha_and_draw_order(self):
        arrays = TileTextureArrays()
        self.addCleanup(arrays.clear)
        images = []
        for index in range(6):
            pixels = np.zeros((5, 7, 4), dtype=np.uint8)
            pixels[:, :, :] = (20 + 30 * index, 190, 50, 140)
            pixels[:2, :3] = (255, 0, 70, 255)
            images.append(PILImage.fromarray(pixels))
        images.extend([PILImage.new("L", (3, 5), 137), PILImage.new("RGB", (7, 3), (17, 99, 231))])
        ordinary = [self.texture(image) for image in images]
        layered = [arrays.upload(np.array(image), image.width, image.height,
                                 len(image.getbands())) for image in images]

        def scene(use_arrays):
            self.renderer.begin_frame(64, 64)
            self.renderer.draw(0, 0, 64, 64, color=(0, 0, 1, 1))
            for index in range(len(images)):
                args = (index * 3, index * 3, 60, 60)
                uv = (-0.2, 0.25, 1.2, 0.75) if index % 2 else (0, 0, 1, 1)
                if use_arrays:
                    self.renderer.draw_tile(layered[index], *args, uv=uv)
                else:
                    self.renderer.draw(*args, texture=ordinary[index], uv=uv)
                if index == 2:
                    self.renderer.draw(0, 0, 40, 40, color=(0, 1, 0, 0.2))
                    self.renderer.draw(4, 4, 32, 32, texture=ordinary[0])
            self.renderer.draw(8.5, 8.5, 55.5, 55.5, outline=True)
            self.renderer.end_frame()
            return np.frombuffer(GL.glReadPixels(0, 0, 64, 64, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE), dtype=np.uint8)

        expected = scene(False)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        actual = scene(True)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(GL.glGetError(), GL.GL_NO_ERROR)

    def test_array_uploads_during_queued_draws_and_page_rollover(self):
        arrays = TileTextureArrays()
        self.addCleanup(arrays.clear)
        profiler = RenderProfiler()
        self.renderer.profiler = profiler
        handles = []
        for index in range(16):
            pixels = np.full((3, 3, 4), (index * 15, 80, 200, 255), dtype=np.uint8)
            handle = arrays.upload(pixels, 3, 3, 4)
            handles.append(handle)
            self.renderer.draw_tile(handle, index * 4, 0, index * 4 + 4, 64)
        self.renderer.end_frame()
        for index in range(16):
            self.assertEqual(self.pixel(index * 4 + 2, 32), (index * 15, 80, 200))
        self.assertEqual(profiler.totals.draw_calls, len({handle.array_id for handle in handles}))
        self.assertEqual(profiler.totals.draw_calls, 3)
        self.assertEqual(profiler.totals.instanced_tiles, 16)
        self.assertEqual(profiler.totals.instance_upload_bytes, 16 * 52)

    def test_array_cache_eviction_reuses_slots_and_releases_empty_pages(self):
        arrays = TileTextureArrays()
        cache = TextureCache(arrays=arrays, max_idle_frames=1)
        self.addCleanup(cache.clear)

        def get(name, color):
            data = BytesIO()
            PILImage.new("RGB", (3, 5), color).save(data, format="PNG")
            return cache.get_or_create(SimpleNamespace(url=name), 0, 0, 0, data.getvalue())

        first = get("first", (255, 0, 0))
        expired = get("expired", (0, 255, 0))
        cache.begin_frame()
        self.assertEqual(get("first", (255, 0, 0)), first)
        cache.end_frame()
        replacement = get("replacement", (0, 0, 255))
        self.assertEqual(replacement, expired)
        self.renderer.draw_tile(first, 0, 0, 32, 64)
        self.renderer.draw_tile(replacement, 32, 0, 64, 64)
        self.renderer.end_frame()
        self.assertEqual(self.pixel(16, 32), (255, 0, 0))
        self.assertEqual(self.pixel(48, 32), (0, 0, 255))
        cache.begin_frame()
        cache.end_frame()
        self.assertFalse(GL.glIsTexture(first.array_id))
        self.assertEqual(arrays.pages, {})
        self.assertEqual(arrays.pools, {})
        cache.clear()

    def test_failed_array_upload_releases_new_page(self):
        arrays = TileTextureArrays()
        self.addCleanup(arrays.clear)
        with patch("galapix_py.tile_textures.GL.glTexSubImage3D", side_effect=RuntimeError("upload failed")):
            with self.assertRaises(RuntimeError):
                arrays.upload(np.zeros((3, 3, 4), dtype=np.uint8), 3, 3, 4)
        self.assertEqual(arrays.pages, {})
        self.assertEqual(arrays.pools, {})

    def test_tile_batch_limit_and_buffer_cleanup(self):
        arrays = TileTextureArrays()
        self.addCleanup(arrays.clear)
        handle = arrays.upload(np.full((3, 3, 4), 255, dtype=np.uint8), 3, 3, 4)
        profiler = RenderProfiler()
        self.renderer.profiler = profiler
        for _ in range(self.renderer.MAX_TILE_BATCH + 3):
            self.renderer.draw_tile(handle, 0, 0, 32, 32)
        self.assertEqual(len(self.renderer._tile_rects), 3)
        self.renderer.end_frame()
        self.assertEqual(self.pixel(16, 16), (255, 255, 255))
        self.assertEqual(profiler.totals.draw_calls, 2)
        vbo, vao = self.renderer.tile_vbo, self.renderer.tile_vao
        self.renderer.close()
        self.assertFalse(GL.glIsBuffer(vbo))
        self.assertFalse(GL.glIsVertexArray(vao))

    def test_viewer_batches_exact_parent_and_child_tiles(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        for mode in ("exact", "parent", "children"):
            with self.subTest(mode=mode):
                provider = Mock()
                provider.get_size.return_value = (512, 512)
                provider.get_max_scale.return_value = 1
                image = Image("/tmp/tile-batch.png")
                image.provider = provider
                image.cache = ImageTileCache(provider)
                image.set_absolute(32, 32, 0.125)
                if mode == "parent":
                    parent = PILImage.new("RGB", (256, 256))
                    for index, color in enumerate(colors):
                        x, y = index % 2 * 128, index // 2 * 128
                        parent.paste(color, (x, y, x + 128, y + 128))
                    tiles = [(1, 0, 0, parent)]
                else:
                    tiles = [(0, index % 2, index // 2, PILImage.new("RGB", (256, 256), color))
                             for index, color in enumerate(colors)]
                for scale, x, y, pixels in tiles:
                    data = BytesIO()
                    pixels.save(data, format="PNG")
                    image.cache.tiles[(scale, x, y)] = TileRecord(None, scale, x, y, 256, 256, data.getvalue())
                workspace = Workspace()
                workspace.add_image(image)
                viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-gl-test"), profile_render=True), workspace, None)
                self.addCleanup(viewer.release_gl_resources)
                viewer.set_viewport(64, 64)
                with patch.object(Image, "choose_scale", return_value=1 if mode == "children" else 0):
                    stats = viewer.draw()
                    viewer.draw()
                self.assertEqual(stats.textured_tiles, 4)
                self.assertEqual(viewer.profiler.totals.draw_calls, 2)
                self.assertEqual(viewer.profiler.totals.instanced_tiles, 8)
                self.assertEqual(viewer.profiler.totals.uploads, len(tiles))
                for index, color in enumerate(colors):
                    self.assertEqual(self.pixel(16 + index % 2 * 32, 16 + index // 2 * 32), color)


if __name__ == "__main__":
    unittest.main()

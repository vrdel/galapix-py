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
from unittest.mock import patch

import numpy as np
from OpenGL import GL
from PIL import Image as PILImage
import sdl2

from galapix_py.models import ViewerOptions
from galapix_py.renderer import RectangleRenderer
from galapix_py.render_profile import RenderProfiler
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


if __name__ == "__main__":
    unittest.main()

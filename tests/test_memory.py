import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from galapix_py.memory import release_unused_memory
from galapix_py.models import ViewerOptions
from galapix_py.viewer import Viewer
from galapix_py.workspace import Workspace


class MemoryReleaseTests(unittest.TestCase):
    def test_linux_calls_malloc_trim_with_size_t_argument(self):
        trim = Mock(return_value=1)
        with (
            patch("galapix_py.memory.sys.platform", "linux"),
            patch("galapix_py.memory.ctypes.CDLL", return_value=SimpleNamespace(malloc_trim=trim)),
        ):
            self.assertTrue(release_unused_memory())
            trim.assert_called_once_with(0)
            self.assertEqual(trim.argtypes, [ctypes.c_size_t])
            self.assertIs(trim.restype, ctypes.c_int)
            trim.return_value = 0
            self.assertFalse(release_unused_memory())

    def test_other_platforms_do_not_load_allocator(self):
        with (
            patch("galapix_py.memory.sys.platform", "darwin"),
            patch("galapix_py.memory.ctypes.CDLL") as load,
        ):
            self.assertFalse(release_unused_memory())
            load.assert_not_called()

    def test_missing_malloc_trim_is_harmless(self):
        for failure in (AttributeError("missing malloc_trim"), OSError("unavailable allocator")):
            with (
                self.subTest(failure=failure),
                patch("galapix_py.memory.sys.platform", "linux"),
                patch("galapix_py.memory.ctypes.CDLL", side_effect=failure),
            ):
                self.assertFalse(release_unused_memory())

    def test_cache_clear_releases_resources_before_trimming(self):
        actions = []
        image = Mock()
        image.cache.clear.side_effect = lambda: actions.append("tiles")
        viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-memory-test")), Workspace(images=[image]), None)
        with (
            patch("galapix_py.viewer.TextureCache.clear", side_effect=lambda: actions.append("textures")),
            patch.object(viewer, "_clear_text_textures", side_effect=lambda: actions.append("labels")),
            patch("galapix_py.viewer.release_unused_memory", side_effect=lambda: actions.append("trim")),
        ):
            viewer.clear_all_caches()
        self.assertEqual(actions, ["tiles", "textures", "labels", "trim"])
        self.assertTrue(viewer.needs_redraw)

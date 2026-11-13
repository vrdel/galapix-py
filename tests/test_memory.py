import ctypes
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from galapix_py.database_thread import DatabaseThread
from galapix_py.jobs import JobManager
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

    def test_completed_jobs_trim_after_idle_delay_with_rate_limit(self):
        jobs = JobManager(1)
        self.addCleanup(jobs.shutdown)
        with (
            patch("galapix_py.jobs.monotonic", return_value=0.0) as clock,
            patch("galapix_py.jobs.release_unused_memory") as trim,
        ):
            jobs.release_idle_memory()
            trim.assert_not_called()
            jobs.submit(lambda: None).future.result(timeout=2)
            clock.return_value = 0.5
            jobs.release_idle_memory()
            trim.assert_not_called()
            clock.return_value = 1.0
            jobs.release_idle_memory()
            trim.assert_called_once_with()
            clock.return_value = 2.0
            jobs.submit(lambda: None).future.result(timeout=2)
            clock.return_value = 3.0
            jobs.release_idle_memory()
            self.assertEqual(trim.call_count, 1)
            clock.return_value = 6.0
            jobs.release_idle_memory()
            self.assertEqual(trim.call_count, 2)
            clock.return_value = 60.0
            jobs.release_idle_memory()
            self.assertEqual(trim.call_count, 2)  # No repeat without new work.

    def test_running_and_queued_jobs_defer_trimming_until_batch_finishes(self):
        jobs = JobManager(1)
        gate = Event()
        self.addCleanup(jobs.shutdown)
        self.addCleanup(gate.set)
        with (
            patch("galapix_py.jobs.monotonic", return_value=0.0) as clock,
            patch("galapix_py.jobs.release_unused_memory") as trim,
        ):
            jobs.submit(lambda: None).future.result(timeout=2)
            active = jobs.submit(gate.wait)
            queued = jobs.submit(lambda: None)
            clock.return_value = 10.0
            jobs.release_idle_memory()
            trim.assert_not_called()
            gate.set()
            active.future.result(timeout=2)
            queued.future.result(timeout=2)
            jobs.release_idle_memory()
            trim.assert_not_called()
            clock.return_value = 11.0
            jobs.release_idle_memory()
            trim.assert_called_once_with()

    def test_failed_jobs_still_allow_memory_release(self):
        jobs = JobManager(1)
        self.addCleanup(jobs.shutdown)
        with (
            patch("galapix_py.jobs.monotonic", return_value=0.0) as clock,
            patch("galapix_py.jobs.release_unused_memory") as trim,
        ):
            def fail():
                raise ValueError("image decoding failed")

            with self.assertRaises(ValueError):
                jobs.submit(fail).future.result(timeout=2)
            clock.return_value = 1.0
            jobs.release_idle_memory()
            trim.assert_called_once_with()

    def test_delivery_poll_trims_without_requesting_redraw(self):
        jobs = JobManager(1)
        self.addCleanup(jobs.shutdown)
        database_thread = DatabaseThread(Mock(), jobs)
        with (
            patch("galapix_py.jobs.monotonic", return_value=0.0) as clock,
            patch("galapix_py.jobs.release_unused_memory") as trim,
        ):
            jobs.submit(lambda: None).future.result(timeout=2)
            clock.return_value = 1.0
            self.assertEqual(database_thread.poll_deliveries(), 0)
            trim.assert_called_once_with()

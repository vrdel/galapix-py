"""Cache replacement races and images returning to the viewport."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from galapix_py.database import Database
from galapix_py.database_thread import DatabaseThread, RequestCachedFiles
from galapix_py.image import Image
from galapix_py.jobs import JobHandle
from galapix_py.models import FileEntry, TileRecord, ViewerOptions
from galapix_py.providers import DatabaseTileProvider
from galapix_py.viewer import Viewer
from galapix_py.workspace import Workspace


class CacheRefreshTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock(initial_cache_version=1)
        self.db.poll_deliveries.return_value = 0
        self.entries = [FileEntry(i, f"/tmp/{i}.jpg", 100, 10, 64, 64, "jpeg") for i in (1, 2)]
        self.images = [Image(entry.url) for entry in self.entries]
        for image, entry in zip(self.images, self.entries):
            image.set_provider(DatabaseTileProvider(self.db, entry))
            image.visible = True
        self.viewer = Viewer(ViewerOptions(database=Path("/tmp/unused-cache-test")), Workspace(self.images), self.db)

    def poll(self, version):
        self.viewer.update(Viewer.CACHE_REFRESH_INTERVAL)
        self.db.request_cache_version.call_args.args[0](version)

    def snapshot(self, signatures, version):
        self.db.request_cached_files.call_args.args[1](
            {entry.url: entry for entry in self.entries},
            {image.url: signature for image, signature in zip(self.images, signatures)},
            version,
        )

    def test_hidden_image_is_checked_after_other_images_consumed_change(self):
        self.poll(1)
        self.snapshot([b"old", b"old"], 1)
        original = self.images[1].cache
        self.images[1].visible = False
        self.poll(2)
        self.poll(2)
        self.snapshot([b"old", b"new"], 2)
        self.assertIs(self.images[1].cache, original)
        self.images[1].visible = True
        self.poll(2)
        self.snapshot([b"old", b"new"], 2)
        self.assertIsNot(self.images[1].cache, original)

    def test_change_before_first_snapshot_reloads_without_baseline(self):
        original = self.images[0].cache
        self.poll(2)
        self.poll(2)
        self.snapshot([b"new", b"old"], 2)
        self.assertIsNot(self.images[0].cache, original)

    def test_unstable_snapshot_is_retried_after_debounce(self):
        original = self.images[0].cache
        self.poll(1)
        self.snapshot([b"partial", b"old"], None)
        self.assertIs(self.images[0].cache, original)
        self.poll(2)
        self.poll(2)
        self.snapshot([b"complete", b"old"], 2)
        self.assertIsNot(self.images[0].cache, original)

    def test_late_snapshot_does_not_replace_refreshed_provider(self):
        self.poll(1)
        self.images[0].set_provider(DatabaseTileProvider(self.db, self.entries[0]))
        provider = self.images[0].provider
        self.snapshot([b"old", b"old"], 1)
        self.assertIs(self.images[0].provider, provider)
        self.poll(1)
        self.assertEqual(self.db.request_cached_files.call_count, 2)

    def test_replacement_aborts_old_requests_and_flushes_before_release(self):
        self.poll(1)
        self.snapshot([b"old", b"old"], 1)
        cache = self.images[0].cache
        handle = JobHandle()
        cache.handles[(0, 0, 0)] = handle
        # A queued delivery belongs to the old cache even if it arrives late.
        events = []
        self.viewer.renderer = Mock()
        self.viewer.renderer.flush.side_effect = lambda: events.append("flush")
        self.poll(2)
        self.poll(2)
        cache.receive_tile(TileRecord(1, 0, 0, 0, 64, 64, b"old"))
        with patch("galapix_py.viewer.TextureCache.remove_image", side_effect=lambda image: events.append("release")):
            self.snapshot([b"new", b"old"], 2)
        self.assertTrue(handle.is_aborted())
        self.assertEqual(events, ["flush", "release"])
        self.assertFalse(self.images[0].cache.tiles)

    def test_worker_snapshot_delivers_on_poll_and_marks_racing_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory))
            self.addCleanup(database.close)
            entry = database.store_file_entry(self.entries[0])
            thread = DatabaseThread(database, Mock())
            callback = Mock()
            request = RequestCachedFiles((entry.url,), callback)
            with patch.object(database, "data_version", side_effect=[1, 2]):
                thread._handle_request_cached_files(request)
            callback.assert_not_called()
            self.assertEqual(thread.poll_deliveries(), 0)
            entries, signatures, version = callback.call_args.args
            self.assertEqual(entries[entry.url], entry)
            self.assertEqual(signatures[entry.url], database.tile_signature(entry.file_id))
            self.assertIsNone(version)

    def test_manual_refresh_evicts_gpu_tiles_and_aborts_pending_requests(self):
        image = self.images[0]
        image.selected = True
        handle = JobHandle()
        image.cache.handles[(0, 0, 0)] = handle
        key = (image.url, 0, 0, 0)
        self.viewer.texture_cache.arrays = None
        self.viewer.texture_cache.textures[key] = 41
        self.viewer.needs_redraw = False
        with patch("galapix_py.viewer.glDeleteTextures") as delete:
            self.viewer.refresh_selection()
        delete.assert_called_once_with([41])
        self.assertTrue(handle.is_aborted())
        self.assertNotIn(key, self.viewer.texture_cache.textures)
        self.assertIsNone(image.provider)
        self.assertTrue(self.viewer.needs_redraw)

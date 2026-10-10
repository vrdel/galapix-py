import ctypes
from contextlib import ExitStack, redirect_stderr
import io
import unittest
from unittest.mock import patch

from galapix_py.render_profile import RenderProfiler


class RenderProfileTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.gl = {}
        for name in ("glGenQueries", "glQueryCounter", "glGetQueryObjectiv", "glDeleteQueries"):
            self.gl[name] = self.stack.enter_context(patch("galapix_py.render_profile.GL." + name))
        self.gl["glGenQueries"].return_value = list(range(1, 9))
        self.gl["glGetQueryObjectiv"].return_value = False
        self.result = self.stack.enter_context(patch("galapix_py.render_profile.glGetQueryObjectui64v"))
        self.profiler = RenderProfiler()

    def test_busy_gpu_skips_samples_without_blocking_or_allocating_more_queries(self):
        for _ in range(12):
            self.profiler.begin_frame()
            self.profiler.end_frame()
        self.assertEqual(self.profiler.totals.frames, 12)
        self.gl["glGenQueries"].assert_called_once_with(8)
        self.assertEqual(self.gl["glQueryCounter"].call_count, 8)
        self.assertEqual(len(self.profiler.pending), 4)
        self.result.assert_not_called()

    def test_completed_samples_are_reused_and_converted_from_nanoseconds(self):
        self.profiler.begin_frame()
        self.profiler.end_frame()
        self.gl["glGetQueryObjectiv"].return_value = True

        def result(query, parameter, pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint64))[0] = 10_000_000 if query % 2 else 12_500_000

        self.result.side_effect = result
        self.profiler.begin_frame()
        self.profiler.end_frame()
        self.assertEqual(self.profiler.totals.gpu_samples, 1)
        self.assertEqual(self.profiler.totals.gpu_ms, 2.5)
        self.assertEqual(self.gl["glQueryCounter"].call_args_list[0], self.gl["glQueryCounter"].call_args_list[2])

    def test_close_releases_pool_once_even_mid_frame(self):
        self.profiler.begin_frame()
        self.profiler.close()
        self.profiler.close()
        self.gl["glQueryCounter"].assert_called_once()
        self.gl["glDeleteQueries"].assert_called_once()
        self.assertEqual(self.profiler.queries, [])
        self.assertEqual(list(self.profiler.pending), [])

    def test_report_is_cumulative_and_does_not_repeat_idle_frames(self):
        with patch("galapix_py.render_profile.perf_counter", return_value=1.0):
            self.profiler.begin_frame()
        with patch("galapix_py.render_profile.perf_counter", return_value=1.005):
            self.profiler.record_upload(1048576, 1.003)
            self.profiler.end_frame()
        output = io.StringIO()
        with redirect_stderr(output):
            self.profiler.report(force=True)
            self.profiler.report(force=True)
        self.assertEqual(output.getvalue().count("render profile:"), 1)
        self.assertIn("cpu_ms_avg=5.000", output.getvalue())
        self.assertIn("gpu_ms_avg=pending", output.getvalue())
        self.assertIn("uploads=1 upload_mib=1.000 upload_cpu_ms=2.000", output.getvalue())

    def test_cli_entrypoints_enable_profiling(self):
        from galapix_py import cli, cli_view
        for module, args in ((cli, ["galapix-py", "--profile-render", "view", "sample.jpg"]),
                             (cli_view, ["galapix-view", "--profile-render", "sample.jpg"])):
            with self.subTest(module=module.__name__), patch("sys.argv", args), patch("galapix_py.app.GalapixApp") as app:
                module.main()
                self.assertTrue(app.call_args.args[0].profile_render)

"""Opt-in rendering measurements with non-blocking OpenGL timer queries."""

from collections import deque
import ctypes
from dataclasses import dataclass
import sys
from time import perf_counter

from OpenGL import GL
from OpenGL.raw.GL.VERSION.GL_3_3 import glGetQueryObjectui64v


@dataclass
class ProfileTotals:
    frames: int = 0
    cpu_ms: float = 0.0
    max_cpu_ms: float = 0.0
    gpu_ms: float = 0.0
    gpu_samples: int = 0
    draw_calls: int = 0
    textured_draws: int = 0
    uploads: int = 0
    upload_bytes: int = 0
    upload_cpu_ms: float = 0.0
    overlay_cpu_ms: float = 0.0


class RenderProfiler:
    """Cumulative statistics; GPU samples may arrive several frames later.

    A small query pool bounds memory and skips GPU sampling if the GPU falls
    behind. CPU timings exclude event handling, buffer swapping and vsync.
    """

    def __init__(self) -> None:
        self.totals = ProfileTotals()
        self.queries: list[int] = []
        self.free: list[tuple[int, int]] = []
        self.pending: deque[tuple[int, int]] = deque()
        self.active: tuple[int, int] | None = None
        self.started = 0.0
        self.last_report = perf_counter()
        self.reported_frames = 0

    def _collect(self) -> None:
        while self.pending:
            begin, end = self.pending[0]
            if not GL.glGetQueryObjectiv(end, GL.GL_QUERY_RESULT_AVAILABLE):
                break
            # The raw entrypoint avoids PyOpenGL's uint64 NumPy converter,
            # which is unavailable in some supported PyOpenGL versions.
            started, finished = ctypes.c_uint64(), ctypes.c_uint64()
            glGetQueryObjectui64v(begin, GL.GL_QUERY_RESULT, ctypes.byref(started))
            glGetQueryObjectui64v(end, GL.GL_QUERY_RESULT, ctypes.byref(finished))
            if finished.value >= started.value:
                self.totals.gpu_ms += (finished.value - started.value) / 1_000_000
                self.totals.gpu_samples += 1
            self.free.append(self.pending.popleft())

    def begin_frame(self) -> None:
        if not self.queries:
            # Timestamp pairs also work on fresh contexts where some Mesa
            # drivers return an invalid first GL_TIME_ELAPSED result.
            self.queries = [int(query) for query in GL.glGenQueries(8)]
            self.free = list(zip(self.queries[::2], self.queries[1::2]))
        self._collect()
        self.started = perf_counter()
        if self.free:
            self.active = self.free.pop()
            GL.glQueryCounter(self.active[0], GL.GL_TIMESTAMP)

    def end_frame(self) -> None:
        if self.active is not None:
            GL.glQueryCounter(self.active[1], GL.GL_TIMESTAMP)
            self.pending.append(self.active)
            self.active = None
        elapsed = (perf_counter() - self.started) * 1000
        self.totals.frames += 1
        self.totals.cpu_ms += elapsed
        self.totals.max_cpu_ms = max(self.totals.max_cpu_ms, elapsed)

    def record_upload(self, byte_count: int, started: float) -> None:
        self.totals.uploads += 1
        self.totals.upload_bytes += byte_count
        self.totals.upload_cpu_ms += (perf_counter() - started) * 1000

    def report(self, *, force: bool = False) -> None:
        now = perf_counter()
        totals = self.totals
        if totals.frames == self.reported_frames or (not force and now - self.last_report < 1.0):
            return
        self._collect()
        gpu = f"{totals.gpu_ms / totals.gpu_samples:.3f}" if totals.gpu_samples else "pending"
        print(
            f"render profile: frames={totals.frames} "
            f"cpu_ms_avg={totals.cpu_ms / totals.frames:.3f} cpu_ms_max={totals.max_cpu_ms:.3f} "
            f"gpu_ms_avg={gpu} gpu_samples={totals.gpu_samples} "
            f"draws_per_frame={totals.draw_calls / totals.frames:.1f} "
            f"textured_draws_per_frame={totals.textured_draws / totals.frames:.1f} "
            f"uploads={totals.uploads} upload_mib={totals.upload_bytes / 1048576:.3f} "
            f"upload_cpu_ms={totals.upload_cpu_ms:.3f} "
            f"overlay_cpu_ms={totals.overlay_cpu_ms:.3f}",
            file=sys.stderr,
        )
        self.last_report = now
        self.reported_frames = totals.frames

    def close(self) -> None:
        self.active = None
        if self.queries:
            GL.glDeleteQueries(len(self.queries), self.queries)
            self.queries.clear()
            self.free.clear()
            self.pending.clear()

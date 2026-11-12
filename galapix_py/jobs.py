from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Callable, Optional

from .memory import release_unused_memory


@dataclass(slots=True)
class JobHandle:
    future: Optional[Future] = None
    aborted: Event = field(default_factory=Event)

    def set_future(self, future: Future) -> None:
        self.future = future

    def abort(self) -> None:
        self.aborted.set()

    def is_aborted(self) -> bool:
        return self.aborted.is_set()

    def done(self) -> bool:
        return self.future.done() if self.future else False


class JobManager:
    MEMORY_IDLE_DELAY = 1.0
    MEMORY_RELEASE_INTERVAL = 5.0

    def __init__(self, max_workers: int) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="galapix")
        self._lock = Lock()
        self._activity_lock = Lock()
        self._pending_jobs = 0
        self._completed_at: float | None = None
        self._last_memory_release = float("-inf")

    def submit(self, fn: Callable[..., object], *args: object, **kwargs: object) -> JobHandle:
        handle = JobHandle()
        with self._activity_lock:
            self._pending_jobs += 1

        def run() -> object:
            try:
                if handle.is_aborted():
                    return None
                return fn(*args, **kwargs)
            finally:
                # fn's frame (including temporary decoded images) is gone
                # before the main thread considers returning free heap pages.
                with self._activity_lock:
                    self._pending_jobs -= 1
                    self._completed_at = monotonic()

        try:
            future = self.executor.submit(run)
        except BaseException:
            with self._activity_lock:
                self._pending_jobs -= 1
            raise
        handle.set_future(future)
        return handle

    def release_idle_memory(self) -> None:
        """Trim once after a quiet batch of jobs, without evicting live caches."""
        now = monotonic()
        with self._activity_lock:
            if (
                self._pending_jobs
                or self._completed_at is None
                or now - self._completed_at < self.MEMORY_IDLE_DELAY
                or now - self._last_memory_release < self.MEMORY_RELEASE_INTERVAL
            ):
                return
            self._completed_at = None
            self._last_memory_release = now
        release_unused_memory()

    def shutdown(self) -> None:
        with self._lock:
            self.executor.shutdown(wait=True, cancel_futures=False)

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Callable, Optional


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
    def __init__(self, max_workers: int) -> None:
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="galapix")
        self._lock = Lock()

    def submit(self, fn: Callable[..., object], *args: object, **kwargs: object) -> JobHandle:
        handle = JobHandle()

        def run() -> object:
            if handle.is_aborted():
                return None
            return fn(*args, **kwargs)

        future = self.executor.submit(run)
        handle.set_future(future)
        return handle

    def shutdown(self) -> None:
        with self._lock:
            self.executor.shutdown(wait=True, cancel_futures=False)

"""Best-effort release of unused allocator pages after explicit cache clearing."""

import ctypes
import sys


def release_unused_memory() -> bool:
    """Ask glibc to return free heap pages to the OS, when available.

    Clearing Python objects does not necessarily reduce RSS: glibc can keep
    their storage in worker-thread arenas. This is deliberately an explicit
    operation rather than part of rendering or individual tile eviction.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return bool(trim(0))
    except (AttributeError, OSError):
        # Other Linux allocators may not provide glibc's malloc_trim.
        return False

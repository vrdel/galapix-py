"""Paged OpenGL texture arrays for tiles of matching size and format."""

from dataclasses import dataclass, field

import numpy as np
from OpenGL import GL


@dataclass(frozen=True, slots=True)
class TileTexture:
    array_id: int
    layer: int


@dataclass(slots=True)
class _Page:
    texture: int
    shape: tuple[int, int, int]
    capacity: int
    free: list[int]
    used: set[int] = field(default_factory=set)


class TileTextureArrays:
    """Allocate small pages first, then grow to at most 64 layers per page.

    Separate dimensions preserve normal [0, 1] UVs and clamp-to-edge filtering
    for partial tiles. Release slots only after queued draws have been flushed.
    All methods that touch GL require the owning context to be current.
    """

    def __init__(self) -> None:
        self.pages: dict[int, _Page] = {}
        self.pools: dict[tuple[int, int, int], list[_Page]] = {}

    def upload(self, pixels: np.ndarray, width: int, height: int, channels: int) -> TileTexture:
        shape = (width, height, channels)
        pool = self.pools.setdefault(shape, [])
        page = next((page for page in pool if page.free), None)
        if page is None:
            capacity = min(64, pool[-1].capacity * 2 if pool else 4,
                           int(GL.glGetIntegerv(GL.GL_MAX_ARRAY_TEXTURE_LAYERS)))
            texture = int(GL.glGenTextures(1))
            try:
                GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, texture)
                for parameter in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
                    GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, parameter, GL.GL_LINEAR)
                for parameter in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
                    GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, parameter, GL.GL_CLAMP_TO_EDGE)
                if channels == 1:
                    GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, GL.GL_TEXTURE_SWIZZLE_G, GL.GL_RED)
                    GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY, GL.GL_TEXTURE_SWIZZLE_B, GL.GL_RED)
                internal, external = {1: (GL.GL_R8, GL.GL_RED), 3: (GL.GL_RGB8, GL.GL_RGB),
                                      4: (GL.GL_RGBA8, GL.GL_RGBA)}[channels]
                GL.glTexImage3D(GL.GL_TEXTURE_2D_ARRAY, 0, internal, width, height, capacity,
                                0, external, GL.GL_UNSIGNED_BYTE, None)
            except Exception:
                GL.glDeleteTextures([texture])
                if not pool:
                    self.pools.pop(shape, None)
                raise
            page = _Page(texture, shape, capacity, list(reversed(range(capacity))))
            pool.append(page)
            self.pages[texture] = page
        layer = page.free.pop()
        page.used.add(layer)
        handle = TileTexture(page.texture, layer)
        try:
            GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, page.texture)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            external = {1: GL.GL_RED, 3: GL.GL_RGB, 4: GL.GL_RGBA}[channels]
            GL.glTexSubImage3D(GL.GL_TEXTURE_2D_ARRAY, 0, 0, 0, layer, width, height, 1,
                               external, GL.GL_UNSIGNED_BYTE, pixels)
        except Exception:
            self.release(handle)
            raise
        return handle

    def release(self, handle: TileTexture) -> None:
        page = self.pages[handle.array_id]
        page.used.remove(handle.layer)
        page.free.append(handle.layer)
        if not page.used:
            GL.glDeleteTextures([page.texture])
            del self.pages[page.texture]
            pool = self.pools[page.shape]
            pool.remove(page)
            if not pool:
                del self.pools[page.shape]

    def clear(self) -> None:
        if self.pages:
            GL.glDeleteTextures(list(self.pages))
        self.pages.clear()
        self.pools.clear()

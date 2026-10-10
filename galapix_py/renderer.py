"""OpenGL 3.3 rectangle renderer; all resources belong to the current context."""

from __future__ import annotations

import ctypes

import numpy as np
from OpenGL import GL
from OpenGL.GL.shaders import compileShader

from .render_profile import RenderProfiler


VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec2 corner;
layout(location = 1) in vec4 instance_rect;
layout(location = 2) in vec4 instance_color;
uniform vec2 viewport;
uniform vec4 rect;
uniform vec4 uv_rect;
uniform vec4 color;
uniform bool instanced;
out vec2 uv;
out vec4 tint;
void main() {
    vec4 bounds = instanced ? instance_rect : rect;
    vec2 pixel = mix(bounds.xy, bounds.zw, corner);
    gl_Position = vec4(pixel.x * 2.0 / viewport.x - 1.0,
                       1.0 - pixel.y * 2.0 / viewport.y, 0.0, 1.0);
    uv = mix(uv_rect.xy, uv_rect.zw, corner);
    tint = instanced ? instance_color : color;
}
"""

FRAGMENT_SHADER = """#version 330 core
in vec2 uv;
in vec4 tint;
uniform sampler2D image;
uniform bool textured;
out vec4 fragment;
void main() {
    fragment = tint;
    if (textured) fragment *= texture(image, uv);
}
"""


class RectangleRenderer:
    """Reuse one static unit quad, transforming it with per-draw uniforms.

    With batch_solids enabled, consecutive solid fills are queued until a
    texture/outline draw, flush(), or end_frame(). Other draws remain immediate.
    Submission order and texture lifetimes are preserved. Unchanged shader
    uniforms are reused between draws.
    Construct and close only while the owning GL context is current.
    """

    MAX_SOLID_BATCH = 1024
    MIN_SOLID_BATCH = 4

    def __init__(self, profiler: RenderProfiler | None = None, *, batch_solids: bool = False) -> None:
        self.profiler = profiler
        self.batch_solids = batch_solids
        self._solid_rects: list[tuple[float, ...]] = []
        self.program = self.vao = self.vbo = self.instance_vbo = 0
        self._uv = self._color = self._textured = None
        shaders = []
        try:
            shaders.append(compileShader(VERTEX_SHADER, GL.GL_VERTEX_SHADER))
            shaders.append(compileShader(FRAGMENT_SHADER, GL.GL_FRAGMENT_SHADER))
            self.program = GL.glCreateProgram()
            for shader in shaders:
                GL.glAttachShader(self.program, shader)
            GL.glLinkProgram(self.program)
            if not GL.glGetProgramiv(self.program, GL.GL_LINK_STATUS):
                raise RuntimeError(f"Shader link failed: {GL.glGetProgramInfoLog(self.program)!r}")
            self.uniforms = {
                name: GL.glGetUniformLocation(self.program, name)
                for name in ("viewport", "rect", "uv_rect", "image", "textured", "color", "instanced")
            }
            # Perimeter order serves both a triangle fan and a line loop.
            corners = np.array([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32)
            self.vao = int(GL.glGenVertexArrays(1))
            self.vbo = int(GL.glGenBuffers(1))
            GL.glBindVertexArray(self.vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, corners.nbytes, corners, GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, ctypes.c_void_p(0))
            if batch_solids:
                self.instance_vbo = int(GL.glGenBuffers(1))
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.instance_vbo)
                # Keep a valid first instance even for ordinary draws, whose
                # shader branch ignores these attributes.
                GL.glBufferData(GL.GL_ARRAY_BUFFER, 32, np.zeros(8, dtype=np.float32), GL.GL_STREAM_DRAW)
                for location, offset in ((1, 0), (2, 16)):
                    GL.glEnableVertexAttribArray(location)
                    GL.glVertexAttribPointer(location, 4, GL.GL_FLOAT, GL.GL_FALSE, 32, ctypes.c_void_p(offset))
                    GL.glVertexAttribDivisor(location, 1)
            GL.glBindVertexArray(0)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        except Exception:
            self.close()
            raise
        finally:
            for shader in shaders:
                if self.program:
                    GL.glDetachShader(self.program, shader)
                GL.glDeleteShader(shader)

    def begin_frame(self, width: int, height: int) -> None:
        self._uv = self._color = self._textured = None
        GL.glUseProgram(self.program)
        GL.glBindVertexArray(self.vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glUniform1i(self.uniforms["image"], 0)
        GL.glUniform1i(self.uniforms["instanced"], False)
        GL.glUniform2f(self.uniforms["viewport"], max(1, width), max(1, height))

    def draw(
        self,
        left: float,
        top: float,
        right: float,
        bottom: float,
        *,
        texture: int = 0,
        uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
        color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        outline: bool = False,
    ) -> None:
        if self.batch_solids and not texture and not outline:
            self._solid_rects.append((left, top, right, bottom, *color))
            if len(self._solid_rects) >= self.MAX_SOLID_BATCH:
                self.flush()
            return
        if self._solid_rects:
            self.flush()
        self._draw_immediate(left, top, right, bottom, texture=texture, uv=uv, color=color, outline=outline)

    def _draw_immediate(
        self, left: float, top: float, right: float, bottom: float, *,
        texture: int = 0,
        uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
        color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        outline: bool = False,
    ) -> None:
        GL.glUniform4f(self.uniforms["rect"], left, top, right, bottom)
        if uv != self._uv:
            GL.glUniform4f(self.uniforms["uv_rect"], *uv)
            self._uv = uv
        if color != self._color:
            GL.glUniform4f(self.uniforms["color"], *color)
            self._color = color
        textured = bool(texture)
        if textured != self._textured:
            GL.glUniform1i(self.uniforms["textured"], textured)
            self._textured = textured
        if texture:
            # Uploads elsewhere in the viewer can change the bound texture.
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glDrawArrays(GL.GL_LINE_LOOP if outline else GL.GL_TRIANGLE_FAN, 0, 4)
        if self.profiler is not None:
            self.profiler.totals.draw_calls += 1
            self.profiler.totals.textured_draws += bool(texture)

    def flush(self) -> None:
        if not self._solid_rects:
            return
        rectangles, self._solid_rects = self._solid_rects, []
        # Small runs cost less as ordinary draws than as streamed uploads.
        if len(rectangles) < self.MIN_SOLID_BATCH:
            for rectangle in rectangles:
                self._draw_immediate(*rectangle[:4], color=rectangle[4:])
            return
        instances = np.asarray(rectangles, dtype=np.float32)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.instance_vbo)
        # Replace storage so queued GPU work can retain the previous upload.
        GL.glBufferData(GL.GL_ARRAY_BUFFER, instances.nbytes, instances, GL.GL_STREAM_DRAW)
        GL.glUniform1i(self.uniforms["textured"], False)
        self._textured = False
        GL.glUniform1i(self.uniforms["instanced"], True)
        try:
            GL.glDrawArraysInstanced(GL.GL_TRIANGLE_FAN, 0, 4, len(rectangles))
        finally:
            GL.glUniform1i(self.uniforms["instanced"], False)
        if self.profiler is not None:
            self.profiler.totals.draw_calls += 1
            self.profiler.totals.instanced_rectangles += len(rectangles)
            self.profiler.totals.instance_upload_bytes += instances.nbytes

    def end_frame(self) -> None:
        try:
            self.flush()
        finally:
            GL.glBindVertexArray(0)
            GL.glUseProgram(0)

    def close(self) -> None:
        self._solid_rects.clear()
        if self.instance_vbo:
            GL.glDeleteBuffers(1, [self.instance_vbo])
            self.instance_vbo = 0
        if self.vbo:
            GL.glDeleteBuffers(1, [self.vbo])
            self.vbo = 0
        if self.vao:
            GL.glDeleteVertexArrays(1, [self.vao])
            self.vao = 0
        if self.program:
            GL.glDeleteProgram(self.program)
            self.program = 0

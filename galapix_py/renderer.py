"""OpenGL 3.3 rectangle renderer; all resources belong to the current context."""

from __future__ import annotations

import ctypes

import numpy as np
from OpenGL import GL
from OpenGL.GL.shaders import compileShader

from .render_profile import RenderProfiler


VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec2 corner;
uniform vec2 viewport;
uniform vec4 rect;
uniform vec4 uv_rect;
out vec2 uv;
void main() {
    vec2 pixel = mix(rect.xy, rect.zw, corner);
    gl_Position = vec4(pixel.x * 2.0 / viewport.x - 1.0,
                       1.0 - pixel.y * 2.0 / viewport.y, 0.0, 1.0);
    uv = mix(uv_rect.xy, uv_rect.zw, corner);
}
"""

FRAGMENT_SHADER = """#version 330 core
in vec2 uv;
uniform sampler2D image;
uniform bool textured;
uniform vec4 color;
out vec4 fragment;
void main() {
    fragment = color;
    if (textured) fragment *= texture(image, uv);
}
"""


class RectangleRenderer:
    """Reuse one static unit quad, transforming it with per-draw uniforms.

    Draws are submitted immediately to preserve alpha compositing order and
    allow the viewer to delete temporary label textures after drawing them.
    Construct and close only while the owning GL context is current.
    """

    def __init__(self, profiler: RenderProfiler | None = None) -> None:
        self.profiler = profiler
        self.program = self.vao = self.vbo = 0
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
                for name in ("viewport", "rect", "uv_rect", "image", "textured", "color")
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
        GL.glUseProgram(self.program)
        GL.glBindVertexArray(self.vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glUniform1i(self.uniforms["image"], 0)
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
        GL.glUniform4f(self.uniforms["rect"], left, top, right, bottom)
        GL.glUniform4f(self.uniforms["uv_rect"], *uv)
        GL.glUniform4f(self.uniforms["color"], *color)
        GL.glUniform1i(self.uniforms["textured"], bool(texture))
        if texture:
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glDrawArrays(GL.GL_LINE_LOOP if outline else GL.GL_TRIANGLE_FAN, 0, 4)
        if self.profiler is not None:
            self.profiler.totals.draw_calls += 1
            self.profiler.totals.textured_draws += bool(texture)

    def end_frame(self) -> None:
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

    def close(self) -> None:
        if self.vbo:
            GL.glDeleteBuffers(1, [self.vbo])
            self.vbo = 0
        if self.vao:
            GL.glDeleteVertexArrays(1, [self.vao])
            self.vao = 0
        if self.program:
            GL.glDeleteProgram(self.program)
            self.program = 0

from __future__ import annotations

from io import BytesIO
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont
from OpenGL.GL import (
    GL_BLEND,
    GL_CLAMP_TO_EDGE,
    GL_COLOR_BUFFER_BIT,
    GL_LINEAR,
    GL_LINE_LOOP,
    GL_LINES,
    GL_LUMINANCE,
    GL_MODELVIEW,
    GL_PROJECTION,
    GL_QUADS,
    GL_RGB,
    GL_RGBA,
    GL_TEXTURE_2D,
    GL_TEXTURE_MAG_FILTER,
    GL_TEXTURE_MIN_FILTER,
    GL_TEXTURE_WRAP_S,
    GL_TEXTURE_WRAP_T,
    GL_UNSIGNED_BYTE,
    GL_UNPACK_ALIGNMENT,
    glBegin,
    glBindTexture,
    glBlendFunc,
    glClear,
    glClearColor,
    glColor3f,
    glColor4f,
    glDeleteTextures,
    glDisable,
    glEnable,
    glEnd,
    glGenTextures,
    glLoadIdentity,
    glMatrixMode,
    glOrtho,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    glTexCoord2f,
    glTexImage2D,
    glTexParameteri,
    glVertex2f,
    glViewport,
    glPixelStorei,
)

from .database_thread import DatabaseThread
from .image import Image
from .models import ViewerOptions
from .providers import DatabaseTileProvider
from .viewer_state import ViewerState
from .workspace import Workspace

WORKSPACE_DUMP_PATH = "/tmp/workspace-dump.galapix"
LABEL_FONT_SIZE = 14
SEARCH_FONT_SIZE = 16
LABEL_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
)


def _decode_texture_pixels(jpeg_bytes: bytes) -> tuple[np.ndarray, int, int, int]:
    with PILImage.open(BytesIO(jpeg_bytes)) as image:
        image.load()
        if image.mode not in {"L", "RGB", "RGBA"}:
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        pixels = np.array(image, dtype=np.uint8)
        if pixels.ndim == 2:
            channels = 1
        else:
            channels = int(pixels.shape[2])
        return pixels, image.width, image.height, channels


def _load_label_font(size: int = LABEL_FONT_SIZE) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in LABEL_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def configure_texture_upload_state() -> None:
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)


@dataclass(slots=True)
class TextureCache:
    textures: dict[tuple[str, int, int, int], int] = field(default_factory=dict)
    idle_frames: dict[tuple[str, int, int, int], int] = field(default_factory=dict)
    live_keys: set[tuple[str, int, int, int]] = field(default_factory=set)
    max_idle_frames: int = 120

    def begin_frame(self) -> None:
        self.live_keys.clear()

    def get_or_create(self, image: Image, scale: int, x: int, y: int, jpeg_bytes: bytes) -> int:
        key = (image.url, scale, x, y)
        texture = self.textures.get(key)
        if texture is not None:
            self.live_keys.add(key)
            self.idle_frames[key] = 0
            return texture
        texture = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, texture)
        configure_texture_upload_state()
        memory, width, height, channels = _decode_texture_pixels(jpeg_bytes)
        fmt = {1: GL_LUMINANCE, 3: GL_RGB, 4: GL_RGBA}.get(channels, GL_RGB)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            fmt,
            width,
            height,
            0,
            fmt,
            GL_UNSIGNED_BYTE,
            memory,
        )
        self.textures[key] = texture
        self.idle_frames[key] = 0
        self.live_keys.add(key)
        return texture

    def clear(self) -> None:
        if self.textures:
            glDeleteTextures(list(self.textures.values()))
            self.textures.clear()
            self.idle_frames.clear()
            self.live_keys.clear()

    def end_frame(self) -> None:
        expired: list[tuple[str, int, int, int]] = []
        for key in list(self.textures):
            if key in self.live_keys:
                self.idle_frames[key] = 0
            else:
                self.idle_frames[key] = self.idle_frames.get(key, 0) + 1
                if self.idle_frames[key] >= self.max_idle_frames:
                    expired.append(key)
        if expired:
            glDeleteTextures([self.textures[key] for key in expired])
            for key in expired:
                del self.textures[key]
                self.idle_frames.pop(key, None)


@dataclass(slots=True)
class FrameRenderStats:
    visible_images: int = 0
    textured_tiles: int = 0
    placeholder_tiles: int = 0


@dataclass(slots=True)
class LabelTexture:
    texture_id: int
    width: int
    height: int


@dataclass(slots=True)
class SavedPlacement:
    x: float
    y: float
    scale: float
    target_x: float
    target_y: float
    target_scale: float


def overlay_label_text(path: str, max_chars: int = 48) -> str:
    name = Path(path).name or path
    if len(name) <= max_chars:
        return name
    return f"{name[:max_chars - 3]}..."


def build_label_rgba(text: str, padding_x: int = 6, padding_y: int = 4) -> tuple[np.ndarray, int, int]:
    font = _load_label_font()
    probe = PILImage.new("L", (1, 1), 0)
    probe_draw = ImageDraw.Draw(probe)
    left, top, right, bottom = probe_draw.textbbox((0, 0), text, font=font)
    text_width = max(1, right - left)
    text_height = max(1, bottom - top)
    width = text_width + padding_x * 2
    height = text_height + padding_y * 2
    mask_image = PILImage.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask_image)
    draw.text((padding_x - left, padding_y - top), text, fill=255, font=font)
    alpha = np.array(mask_image, dtype=np.uint8)
    alpha_max = int(alpha.max())
    if alpha_max and alpha_max < 255:
        alpha = np.clip((alpha.astype(np.uint16) * 255) // alpha_max, 0, 255).astype(np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[:, :, :3] = 255
    rgba[:, :, 3] = alpha
    return rgba, width, height


def filename_overlay_rect(
    screen_left: float,
    screen_top: float,
    screen_right: float,
    label_width: float,
    label_height: float,
    margin: float = 4.0,
) -> tuple[float, float, float, float] | None:
    available_width = screen_right - screen_left
    if available_width < 40.0:
        return None
    label_left = screen_left
    label_top = screen_top - label_height - margin
    label_right = min(screen_right, label_left + label_width)
    if label_right <= label_left:
        return None
    return label_left, label_top, label_right, label_top + label_height


def brighten_rgb(
    color: tuple[float, float, float, float],
    levels: int = 4,
    step: float = 1.0 / 16.0,
) -> tuple[float, float, float]:
    lift = levels * step
    return tuple(min(1.0, channel + lift) for channel in color[:3])


def darken_rgb(
    color: tuple[float, float, float, float],
    levels: int = 4,
    step: float = 1.0 / 16.0,
) -> tuple[float, float, float]:
    drop = levels * step
    return tuple(max(0.0, channel - drop) for channel in color[:3])


def shade_rgb(
    color: tuple[float, float, float, float],
    factor: float,
    floor: float = 0.06,
) -> tuple[float, float, float]:
    return tuple(max(floor, min(1.0, channel * factor)) for channel in color[:3])


class Viewer:
    def __init__(
        self,
        options: ViewerOptions,
        workspace: Workspace,
        db_thread: DatabaseThread | None,
        provider_factory: Callable[[str], object] | None = None,
    ) -> None:
        self.options = options
        self.workspace = workspace
        self.db_thread = db_thread
        self.provider_factory = provider_factory
        self.state = ViewerState()
        self.texture_cache = TextureCache()
        self.label_textures: dict[str, LabelTexture] = {}
        self.background_colors = [
            (0.39, 0.39, 0.39, 1.0),
            (0.25, 0.25, 0.25, 1.0),
            (0.50, 0.50, 0.50, 1.0),
            (1.00, 1.00, 1.00, 1.0),
            (1.00, 0.00, 0.00, 1.0),
            (1.00, 1.00, 0.00, 1.0),
            (1.00, 0.00, 1.00, 1.0),
            (0.00, 1.00, 0.00, 1.0),
            (0.00, 1.00, 1.00, 1.0),
            (0.00, 0.00, 1.00, 1.0),
            (0.50, 0.00, 0.00, 1.0),
            (0.50, 0.50, 0.00, 1.0),
            (0.50, 0.00, 0.50, 1.0),
            (0.00, 0.50, 0.00, 1.0),
            (0.00, 0.50, 0.50, 1.0),
            (0.00, 0.00, 0.50, 1.0),
        ]
        if options.background_color is not None:
            self.background_colors = [options.background_color]
        self.background_index = 0
        self.needs_redraw = True
        self.viewport_width = options.width
        self.viewport_height = options.height
        self.show_status = True
        self.last_frame_stats = FrameRenderStats()
        self.search_active = False
        self.search_query = ""
        self.search_saved_layout: dict[str, SavedPlacement] = {}

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.state.offset_x + x * self.state.scale,
            self.state.offset_y + y * self.state.scale,
        )

    def set_viewport(self, width: int, height: int) -> None:
        self.viewport_width = width
        self.viewport_height = height
        glViewport(0, 0, width, height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, width, height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        self.needs_redraw = True

    def zoom_home(self) -> None:
        self.state = ViewerState()
        self.needs_redraw = True

    def zoom_to_workspace(self) -> None:
        left, top, right, bottom = self.workspace.filtered_bounding_rect()
        self.state.zoom_to_rect(self.viewport_width, self.viewport_height, left, top, right, bottom)
        self.needs_redraw = True

    def zoom_to_selection(self) -> None:
        rect = self.workspace.filtered_selection_bounding_rect()
        if rect is None:
            self.zoom_to_workspace()
            return
        left, top, right, bottom = rect
        self.state.zoom_to_rect(self.viewport_width, self.viewport_height, left, top, right, bottom)
        self.needs_redraw = True

    def zoom_to_original(self) -> None:
        selected = self.workspace.selected_images()
        if selected:
            image = selected[0]
        elif self.workspace.images:
            image = self.workspace.images[0]
        else:
            return
        scale = 1.0 / image.placement.scale
        cx = image.placement.x
        cy = image.placement.y
        self.state.target_scale = scale
        self.state.target_offset_x = (self.viewport_width / 2.0) - cx * scale
        self.state.target_offset_y = (self.viewport_height / 2.0) - cy * scale
        self.needs_redraw = True

    def save_workspace(self, path: str = WORKSPACE_DUMP_PATH) -> None:
        self.workspace.save(path)

    def load_workspace(self, path: str = WORKSPACE_DUMP_PATH) -> None:
        self.workspace.load(path)
        self.zoom_to_workspace()
        self.request_redraw()

    def refresh_selection(self) -> None:
        refreshed = False
        for image in self.workspace.selected_images():
            image.refresh()
            refreshed = True
        if refreshed:
            self.request_redraw()

    def update(self, delta: float) -> None:
        processed = self.db_thread.poll_deliveries() if self.db_thread is not None else 0
        state_changed = self.state.update(delta)
        self.workspace.update(delta)
        for image in self.workspace.images:
            image.process_queues()
        if processed or state_changed or self.workspace.is_animated():
            self.needs_redraw = True

    def draw(self) -> FrameRenderStats:
        stats = FrameRenderStats()
        self.texture_cache.begin_frame()
        glClearColor(*self.background_colors[self.background_index])
        glClear(GL_COLOR_BUFFER_BIT)
        glLoadIdentity()
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        clip = self.state.world_rect(self.viewport_width, self.viewport_height)
        active_images = self.workspace.filtered_images()
        active_ids = {id(image) for image in active_images}
        for image in self.workspace.images:
            if id(image) not in active_ids and image.visible:
                image.on_leave_screen()
        for image in active_images:
            if not image.overlaps(clip):
                if image.visible:
                    image.on_leave_screen()
                continue
            stats.visible_images += 1
            if not image.visible:
                image.on_enter_screen()
            self._draw_image(image, stats)
        if self.search_active:
            self._draw_search_overlay()
        self.texture_cache.end_frame()
        self.needs_redraw = False
        self.last_frame_stats = stats
        return stats

    def request_redraw(self) -> None:
        self.needs_redraw = True

    def selection_outline_color(self) -> tuple[float, float, float]:
        if self.options.selection_border_color is not None:
            return self.options.selection_border_color[:3]
        return brighten_rgb(
            self.background_colors[self.background_index],
            levels=4,
        )

    def toggle_status(self) -> None:
        self.show_status = not self.show_status
        self.request_redraw()

    def cycle_background(self, backwards: bool = False) -> None:
        if backwards:
            self.background_index = (self.background_index - 1) % len(self.background_colors)
        else:
            self.background_index = (self.background_index + 1) % len(self.background_colors)
        self.request_redraw()

    def status_text(self) -> str:
        filtered = self.workspace.filtered_images()
        selected = len(self.workspace.filtered_selected_images())
        visible = sum(1 for image in filtered if image.visible)
        textures = len(self.texture_cache.textures)
        parts = [
            f"{self.options.title} | "
            f"zoom={self.state.scale:.2f} "
            f"images={len(self.workspace.images)} "
            f"selected={selected} "
            f"visible={visible} "
            f"textures={textures}"
        ]
        if self.workspace.has_active_search():
            parts.append(f' filtered={len(filtered)} search="{self.search_query}"')
        return "".join(parts)

    def clear_all_caches(self) -> None:
        for image in self.workspace.images:
            if image.cache is not None:
                image.cache.clear()
            image.visible = False
        self.texture_cache.clear()
        if self.label_textures:
            glDeleteTextures([label.texture_id for label in self.label_textures.values()])
            self.label_textures.clear()
        self.request_redraw()

    def print_visible_images(self) -> None:
        clip = self.state.world_rect(self.viewport_width, self.viewport_height)
        for image in self.workspace.visible_images(clip):
            print(image.url)

    def print_info(self) -> None:
        clip = self.state.world_rect(self.viewport_width, self.viewport_height)
        visible = self.workspace.visible_images(clip)
        print("Workspace Info:")
        print(f"  images: {len(self.workspace.images)}")
        print(f"  selected: {len(self.workspace.filtered_selected_images())}")
        print(f"  visible: {len(visible)}")
        print(f"  zoom: {self.state.scale:.3f}")
        print(f"  offset: ({self.state.offset_x:.1f}, {self.state.offset_y:.1f})")
        print(f"  textures: {len(self.texture_cache.textures)}")
        if self.workspace.has_active_search():
            print(f'  search: "{self.search_query}"')
            print(f"  filtered: {len(self.workspace.filtered_images())}")

    def _get_label_texture(self, text: str) -> LabelTexture:
        label = self.label_textures.get(text)
        if label is not None:
            return label
        texture = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, texture)
        configure_texture_upload_state()
        rgba, width, height = build_label_rgba(text)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGBA,
            width,
            height,
            0,
            GL_RGBA,
            GL_UNSIGNED_BYTE,
            rgba,
        )
        label = LabelTexture(texture, width, height)
        self.label_textures[text] = label
        return label

    def _create_text_texture(
        self,
        text: str,
        padding_x: int = 6,
        padding_y: int = 4,
        font_size: int = LABEL_FONT_SIZE,
    ) -> LabelTexture:
        texture = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, texture)
        configure_texture_upload_state()
        font = _load_label_font(font_size)
        probe = PILImage.new("L", (1, 1), 0)
        probe_draw = ImageDraw.Draw(probe)
        left, top, right, bottom = probe_draw.textbbox((0, 0), text, font=font)
        text_width = max(1, right - left)
        text_height = max(1, bottom - top)
        width = text_width + padding_x * 2
        height = text_height + padding_y * 2
        mask_image = PILImage.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask_image)
        draw.text((padding_x - left, padding_y - top), text, fill=255, font=font)
        alpha = np.array(mask_image, dtype=np.uint8)
        alpha_max = int(alpha.max())
        if alpha_max and alpha_max < 255:
            alpha = np.clip((alpha.astype(np.uint16) * 255) // alpha_max, 0, 255).astype(np.uint8)
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255
        rgba[:, :, 3] = alpha
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGBA,
            width,
            height,
            0,
            GL_RGBA,
            GL_UNSIGNED_BYTE,
            rgba,
        )
        return LabelTexture(texture, width, height)

    def _draw_solid_rect(self, left: float, top: float, right: float, bottom: float, color: tuple[float, float, float]) -> None:
        glDisable(GL_TEXTURE_2D)
        glColor3f(*color)
        glBegin(GL_QUADS)
        glVertex2f(left, top)
        glVertex2f(right, top)
        glVertex2f(right, bottom)
        glVertex2f(left, bottom)
        glEnd()
        glEnable(GL_TEXTURE_2D)
        glColor3f(1.0, 1.0, 1.0)

    def _draw_filename_overlay(self, image: Image) -> None:
        left, top, right, _ = image.rect()
        screen_left, screen_top = self.world_to_screen(left, top)
        screen_right, _ = self.world_to_screen(right, top)
        label = self._get_label_texture(overlay_label_text(image.url))
        rect = filename_overlay_rect(screen_left, screen_top, screen_right, label.width, label.height)
        if rect is None:
            return
        label_left, label_top, label_right, label_bottom = rect
        self._draw_solid_rect(label_left, label_top, label_right, label_bottom, (0.0, 0.0, 0.0))
        u1 = (label_right - label_left) / label.width
        glColor4f(1.0, 1.0, 1.0, 1.0)
        self._draw_textured_rect(label.texture_id, label_left, label_top, label_right, label_bottom, 0.0, 0.0, u1, 1.0)
        glColor3f(1.0, 1.0, 1.0)

    def _draw_search_overlay(self) -> None:
        title = self._create_text_texture("Filename contains", padding_x=0, padding_y=0, font_size=SEARCH_FONT_SIZE)
        query = self._create_text_texture(self.search_query or " ", padding_x=0, padding_y=0, font_size=SEARCH_FONT_SIZE)
        min_query_width = self._create_text_texture("M" * 10, padding_x=0, padding_y=0, font_size=SEARCH_FONT_SIZE)
        min_query_height = self._create_text_texture("Mg", padding_x=0, padding_y=0, font_size=SEARCH_FONT_SIZE)
        pad = 16.0
        gap = 10.0
        query_box_pad = 6.0
        panel_color = shade_rgb(self.background_colors[self.background_index], 0.55)
        query_box_color = shade_rgb(self.background_colors[self.background_index], 0.35)
        query_display_width = max(query.width, min_query_width.width)
        query_display_height = max(query.height, min_query_height.height)
        width = max(title.width, query_display_width + 20) + pad * 2.0
        height = (
            pad * 2.0
            + title.height
            + gap
            + query_display_height
            + query_box_pad * 2.0
        )
        left = (self.viewport_width - width) / 2.0
        top = max(24.0, (self.viewport_height - height) / 4.0)
        right = left + width
        bottom = top + height
        self._draw_solid_rect(left, top, right, bottom, panel_color)
        y = top + pad
        try:
            self._draw_text_label(title, left + pad, y)
        finally:
            glDeleteTextures([title.texture_id])
        y += title.height + gap
        query_top = y
        query_bottom = query_top + query_display_height + query_box_pad * 2.0
        self._draw_solid_rect(left + pad, query_top, right - pad, query_bottom, query_box_color)
        try:
            text_top = query_top + query_box_pad + (query_display_height - query.height) / 2.0
            self._draw_text_label(query, left + pad + query_box_pad, text_top)
            cursor_x = left + pad + query_box_pad + query.width + 2.0
            self._draw_solid_rect(
                cursor_x,
                query_top + query_box_pad,
                cursor_x + 2.0,
                query_bottom - query_box_pad,
                (1.0, 1.0, 1.0),
            )
        finally:
            glDeleteTextures([query.texture_id])
            glDeleteTextures([min_query_width.texture_id])
            glDeleteTextures([min_query_height.texture_id])

    def _draw_text_label(self, label: LabelTexture, left: float, top: float) -> None:
        glColor4f(1.0, 1.0, 1.0, 1.0)
        self._draw_textured_rect(label.texture_id, left, top, left + label.width, top + label.height)
        glColor3f(1.0, 1.0, 1.0)

    def _draw_textured_rect(
        self,
        texture: int,
        left: float,
        top: float,
        right: float,
        bottom: float,
        u0: float = 0.0,
        v0: float = 0.0,
        u1: float = 1.0,
        v1: float = 1.0,
    ) -> None:
        glBindTexture(GL_TEXTURE_2D, texture)
        glBegin(GL_QUADS)
        glTexCoord2f(u0, v0)
        glVertex2f(left, top)
        glTexCoord2f(u1, v0)
        glVertex2f(right, top)
        glTexCoord2f(u1, v1)
        glVertex2f(right, bottom)
        glTexCoord2f(u0, v1)
        glVertex2f(left, bottom)
        glEnd()

    def _draw_outline(self, left: float, top: float, right: float, bottom: float, color: tuple[float, float, float]) -> None:
        glDisable(GL_TEXTURE_2D)
        glColor3f(*color)
        glBegin(GL_LINE_LOOP)
        glVertex2f(left, top)
        glVertex2f(right, top)
        glVertex2f(right, bottom)
        glVertex2f(left, bottom)
        glEnd()
        glEnable(GL_TEXTURE_2D)
        glColor3f(1.0, 1.0, 1.0)

    def select_at_screen(self, screen_x: float, screen_y: float) -> None:
        world_x, world_y = self.state.screen_to_world(screen_x, screen_y)
        self.workspace.select_at(world_x, world_y)
        self.request_redraw()

    def open_search(self) -> None:
        self.search_active = True
        self.request_redraw()

    def close_search(self, clear: bool = False) -> None:
        self.search_active = False
        if clear:
            self.set_search_query("")
        self.request_redraw()

    def append_search_text(self, text: str) -> None:
        if text:
            self.set_search_query(self.search_query + text)

    def backspace_search(self) -> None:
        if self.search_query:
            self.set_search_query(self.search_query[:-1])

    def has_active_filter(self) -> bool:
        return self.workspace.has_active_search()

    def set_search_query(self, query: str) -> None:
        previous_query = self.search_query
        normalized = query
        if normalized == previous_query:
            return
        if normalized and not previous_query:
            self._capture_search_layout()
        self.search_query = normalized
        self.workspace.set_search_query(normalized)
        self._clear_filtered_out_selection()
        if normalized:
            self._layout_search_results()
        elif previous_query:
            self._restore_search_layout()
        self.request_redraw()

    def _capture_search_layout(self) -> None:
        self.search_saved_layout = {
            image.url: SavedPlacement(
                x=image.placement.x,
                y=image.placement.y,
                scale=image.placement.scale,
                target_x=image.placement.target_x,
                target_y=image.placement.target_y,
                target_scale=image.placement.target_scale,
            )
            for image in self.workspace.images
        }

    def _restore_search_layout(self) -> None:
        for image in self.workspace.images:
            saved = self.search_saved_layout.get(image.url)
            if saved is None:
                continue
            image.set_absolute(saved.x, saved.y, saved.scale)
            image.placement.target_x = saved.target_x
            image.placement.target_y = saved.target_y
            image.placement.target_scale = saved.target_scale
            image.placement.last_x = saved.x
            image.placement.last_y = saved.y
            image.placement.last_scale = saved.scale
        self.search_saved_layout.clear()
        self.workspace.animation_progress = 1.0
        self.zoom_to_workspace()

    def _layout_search_results(self) -> None:
        filtered = self.workspace.filtered_images()
        if not filtered:
            self.workspace.animation_progress = 1.0
            return
        self.workspace.layout_row(
            spacing=40.0 * max(1, self.options.spacing),
            max_per_row=self.options.images_per_row,
            images=filtered,
        )

    def _clear_filtered_out_selection(self) -> None:
        filtered_ids = {id(image) for image in self.workspace.filtered_images()}
        for image in self.workspace.images:
            if image.selected and id(image) not in filtered_ids:
                image.selected = False

    def _draw_image(self, image: Image, stats: FrameRenderStats) -> None:
        img_left, img_top, img_right, img_bottom = image.rect()
        if image.provider is None and self.provider_factory is not None:
            image.set_provider(self.provider_factory(image.url))
        if image.provider is None:
            left, top = self.world_to_screen(img_left, img_top)
            right, bottom = self.world_to_screen(img_right, img_bottom)
            self._draw_solid_rect(left, top, right, bottom, (0.55, 0.45, 0.10))
            stats.placeholder_tiles += 1
            if image.selected:
                self._draw_outline(left, top, right, bottom, self.selection_outline_color())
            if not image.file_entry_requested:
                image.file_entry_requested = True

                def on_file(entry, image=image) -> None:
                    image.receive_file_entry(entry, DatabaseTileProvider(self.db_thread, entry))

                def on_tile(entry, tile, image=image) -> None:
                    image.receive_tile(tile)

                if self.db_thread is None:
                    raise RuntimeError(f"image provider unavailable for {image.url}")
                self.db_thread.request_file(image.url, on_file, on_tile)
            return

        if image.cache is None:
            return

        zoom = self.state.scale
        scale = image.choose_scale(zoom)
        image.cache.set_focus_scale(scale)
        factor = 2 ** scale
        source_w, source_h = image.provider.get_size()
        scaled_w = max(1, math.ceil(source_w / factor))
        scaled_h = max(1, math.ceil(source_h / factor))
        tiles_x = math.ceil(scaled_w / 256)
        tiles_y = math.ceil(scaled_h / 256)
        tile_world_w = 256 * factor * image.placement.scale
        tile_world_h = 256 * factor * image.placement.scale

        clip_left, clip_top, clip_right, clip_bottom = self.state.world_rect(self.viewport_width, self.viewport_height)
        start_x = max(0, int((clip_left - img_left) // tile_world_w))
        end_x = min(tiles_x, int(math.ceil((clip_right - img_left) / tile_world_w)))
        start_y = max(0, int((clip_top - img_top) // tile_world_h))
        end_y = min(tiles_y, int(math.ceil((clip_bottom - img_top) / tile_world_h)))

        for ty in range(start_y, end_y):
            for tx in range(start_x, end_x):
                tile_left_world = img_left + tx * tile_world_w
                tile_top_world = img_top + ty * tile_world_h
                left, top = self.world_to_screen(tile_left_world, tile_top_world)
                right, bottom = self.world_to_screen(
                    tile_left_world + tile_world_w,
                    tile_top_world + tile_world_h,
                )

                tile = image.cache.request_tile(scale, tx, ty)
                if tile is not None:
                    texture = self.texture_cache.get_or_create(image, scale, tx, ty, tile.jpeg_bytes)
                    exact_right, exact_bottom = self.world_to_screen(
                        tile_left_world + tile.width * factor * image.placement.scale,
                        tile_top_world + tile.height * factor * image.placement.scale,
                    )
                    self._draw_textured_rect(texture, left, top, exact_right, exact_bottom)
                    stats.textured_tiles += 1
                    continue

                image.cache.request_parent_tiles(scale, tx, ty)

                child_scale = scale - 1
                if child_scale >= 0:
                    drew_child = False
                    child_factor = factor / 2.0
                    for child_y in range(2):
                        for child_x in range(2):
                            child_tx = tx * 2 + child_x
                            child_ty = ty * 2 + child_y
                            child_tile = image.cache.get_cached_tile(child_scale, child_tx, child_ty)
                            if child_tile is None:
                                continue
                            drew_child = True
                            child_texture = self.texture_cache.get_or_create(
                                image,
                                child_scale,
                                child_tx,
                                child_ty,
                                child_tile.jpeg_bytes,
                            )
                            child_left_world = tile_left_world + child_x * (tile_world_w / 2.0)
                            child_top_world = tile_top_world + child_y * (tile_world_h / 2.0)
                            child_left, child_top = self.world_to_screen(child_left_world, child_top_world)
                            child_right, child_bottom = self.world_to_screen(
                                child_left_world + child_tile.width * child_factor * image.placement.scale,
                                child_top_world + child_tile.height * child_factor * image.placement.scale,
                            )
                            self._draw_textured_rect(child_texture, child_left, child_top, child_right, child_bottom)
                            stats.textured_tiles += 1
                    if drew_child:
                        continue

                parent_result = image.cache.find_parent_tile(scale, tx, ty)
                if parent_result is not None:
                    parent_tile, downscale = parent_result
                    parent_texture = self.texture_cache.get_or_create(
                        image,
                        parent_tile.scale,
                        parent_tile.x,
                        parent_tile.y,
                        parent_tile.jpeg_bytes,
                    )
                    u0 = (tx % downscale) / downscale
                    v0 = (ty % downscale) / downscale
                    u1 = u0 + 1.0 / downscale
                    v1 = v0 + 1.0 / downscale
                    self._draw_textured_rect(parent_texture, left, top, right, bottom, u0, v0, u1, v1)
                    stats.textured_tiles += 1
                    continue

                self._draw_solid_rect(left, top, right, bottom, (0.30, 0.10, 0.30))
                stats.placeholder_tiles += 1

        if image.selected:
            left, top = self.world_to_screen(img_left, img_top)
            right, bottom = self.world_to_screen(img_right, img_bottom)
            self._draw_outline(left, top, right, bottom, self.selection_outline_color())
        if self.options.show_filenames:
            self._draw_filename_overlay(image)

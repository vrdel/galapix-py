from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ViewerState:
    scale: float = 1.0
    angle: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def zoom(self, factor: float, screen_x: float, screen_y: float) -> None:
        self.scale *= factor
        self.offset_x = screen_x - ((screen_x - self.offset_x) * factor)
        self.offset_y = screen_y - ((screen_y - self.offset_y) * factor)

    def move(self, dx: float, dy: float) -> None:
        self.offset_x += dx
        self.offset_y += dy

    def screen_to_world(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale)

    def world_rect(self, width: int, height: int) -> tuple[float, float, float, float]:
        left, top = self.screen_to_world(0, 0)
        right, bottom = self.screen_to_world(width, height)
        return left, top, right, bottom

    def zoom_to_rect(self, screen_width: int, screen_height: int, left: float, top: float, right: float, bottom: float) -> None:
        rect_width = max(1.0, right - left)
        rect_height = max(1.0, bottom - top)
        scale_x = screen_width / rect_width
        scale_y = screen_height / rect_height
        self.scale = min(scale_x, scale_y)
        self.offset_x = -left * self.scale + (screen_width - rect_width * self.scale) / 2.0
        self.offset_y = -top * self.scale + (screen_height - rect_height * self.scale) / 2.0

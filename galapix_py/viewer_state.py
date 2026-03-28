from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ViewerState:
    scale: float = 1.0
    angle: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    target_scale: float = 1.0
    target_offset_x: float = 0.0
    target_offset_y: float = 0.0

    def zoom(self, factor: float, screen_x: float, screen_y: float) -> None:
        self.target_scale *= factor
        self.target_offset_x = screen_x - ((screen_x - self.target_offset_x) * factor)
        self.target_offset_y = screen_y - ((screen_y - self.target_offset_y) * factor)

    def move(self, dx: float, dy: float) -> None:
        self.target_offset_x += dx
        self.target_offset_y += dy

    def snap_to_target(self) -> None:
        self.scale = self.target_scale
        self.offset_x = self.target_offset_x
        self.offset_y = self.target_offset_y

    def update(self, delta: float) -> bool:
        factor = min(1.0, 1.0 - pow(0.001, max(0.0, delta) * 6.0))
        changed = False

        def step(current: float, target: float) -> tuple[float, bool]:
            if abs(target - current) < 1e-4:
                return target, current != target
            return current + ((target - current) * factor), True

        self.scale, scale_changed = step(self.scale, self.target_scale)
        self.offset_x, offset_x_changed = step(self.offset_x, self.target_offset_x)
        self.offset_y, offset_y_changed = step(self.offset_y, self.target_offset_y)
        changed = scale_changed or offset_x_changed or offset_y_changed
        return changed

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
        self.target_scale = min(scale_x, scale_y)
        self.target_offset_x = -left * self.target_scale + (screen_width - rect_width * self.target_scale) / 2.0
        self.target_offset_y = -top * self.target_scale + (screen_height - rect_height * self.target_scale) / 2.0
        self.snap_to_target()

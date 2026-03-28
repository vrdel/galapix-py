from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from .image import Image


@dataclass(slots=True)
class Workspace:
    images: list[Image] = field(default_factory=list)
    animation_progress: float = 1.0

    def add_image(self, image: Image) -> None:
        self.images.append(image)

    def clear(self) -> None:
        self.images.clear()

    def clear_selection(self) -> None:
        for image in self.images:
            image.selected = False

    def get_image_at(self, x: float, y: float) -> Image | None:
        for image in reversed(self.images):
            if image.contains_point(x, y):
                return image
        return None

    def select_at(self, x: float, y: float) -> Image | None:
        image = self.get_image_at(x, y)
        self.clear_selection()
        if image is not None:
            image.selected = True
        return image

    def selected_images(self) -> list[Image]:
        return [image for image in self.images if image.selected]

    def update(self, delta: float) -> None:
        if self.animation_progress < 1.0:
            self.animation_progress = min(1.0, self.animation_progress + delta * 2.0)
            for image in self.images:
                image.update_animation(self.animation_progress)

    def is_animated(self) -> bool:
        return self.animation_progress < 1.0

    def sort_by_url(self, reverse: bool = False) -> None:
        self.images.sort(key=lambda image: image.url, reverse=reverse)

    def shuffle_images(self) -> None:
        random.shuffle(self.images)

    def isolate_selection(self) -> None:
        selected = self.selected_images()
        if not selected:
            return
        self.images = list(selected)
        self.clear_selection()

    def delete_selection(self) -> None:
        selected = set(self.selected_images())
        if not selected:
            return
        self.images = [image for image in self.images if image not in selected]
        self.clear_selection()

    def save(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        payload = {
            "version": 1,
            "images": [
                {
                    "url": image.url,
                    "x": image.placement.x,
                    "y": image.placement.y,
                    "scale": image.placement.scale,
                    "selected": image.selected,
                }
                for image in self.images
            ],
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self, path: str | Path) -> None:
        source = Path(path).expanduser()
        payload = json.loads(source.read_text(encoding="utf-8"))
        self.clear()
        for item in payload.get("images", []):
            image = Image(str(Path(item["url"]).expanduser()))
            image.set_absolute(float(item.get("x", 0.0)), float(item.get("y", 0.0)), float(item.get("scale", 1.0)))
            image.selected = bool(item.get("selected", False))
            self.add_image(image)
        self.animation_progress = 1.0

    def layout_tight(self, aspect_w: float, aspect_h: float) -> None:
        spacing = 24.0
        width_sum = 0.0
        for image in self.images:
            iw, ih = image.size()
            scale = (1000.0 + spacing) / max(ih, 1)
            width_sum += iw * scale
        target_width = max(1024.0, width_sum / math.sqrt(max(width_sum / ((aspect_w / aspect_h) * (1000.0 + spacing)), 1.0)))
        x = 0.0
        y = 0.0
        last_x = 0.0
        go_right = True
        for image in self.images:
            iw, ih = image.size()
            scale = 1000.0 / max(ih, 1)
            scaled_w = iw * scale
            scaled_h = ih * scale
            if go_right:
                if x + scaled_w > target_width:
                    x = last_x
                    y += 1000.0 + spacing
                    go_right = False
                cx = x + scaled_w / 2.0
                cy = y + scaled_h / 2.0
                image.set_target(cx, cy, scale)
                last_x = x
                x += scaled_w + spacing
            else:
                if x - scaled_w < 0:
                    y += 1000.0 + spacing
                    go_right = True
                    cx = x + scaled_w / 2.0
                    cy = y + scaled_h / 2.0
                    image.set_target(cx, cy, scale)
                    last_x = x
                    x += scaled_w + spacing
                else:
                    x -= scaled_w + spacing
                    cx = x + scaled_w / 2.0
                    cy = y + scaled_h / 2.0
                    image.set_target(cx, cy, scale)
                    last_x = x
        self.animation_progress = 0.0

    def layout_row(
        self,
        spacing: float = 24.0,
        target_height: float = 1000.0,
        max_per_row: int | None = None,
    ) -> None:
        if max_per_row is None:
            image_count = len(self.images)
            if image_count > 2:
                max_per_row = math.ceil(math.sqrt(image_count))
        y = 0.0
        row_images: list[tuple[Image, float, float]] = []
        for index, image in enumerate(self.images):
            if max_per_row is not None and max_per_row > 0 and index > 0 and index % max_per_row == 0:
                y += self._layout_row_segment(row_images, y, spacing)
                row_images = []
            iw, ih = image.size()
            scale = target_height / max(ih, 1)
            scaled_w = iw * scale
            scaled_h = ih * scale
            row_images.append((image, scaled_w, scaled_h))
        if row_images:
            self._layout_row_segment(row_images, y, spacing)
        self.animation_progress = 0.0

    def _layout_row_segment(
        self,
        row_images: list[tuple[Image, float, float]],
        top_y: float,
        spacing: float,
    ) -> float:
        row_width = sum(width for _, width, _ in row_images)
        if len(row_images) > 1:
            row_width += spacing * (len(row_images) - 1)
        x = -(row_width / 2.0)
        row_height = 0.0
        for image, scaled_w, scaled_h in row_images:
            center_x = x + (scaled_w / 2.0)
            center_y = top_y + (scaled_h / 2.0)
            image.set_target(center_x, center_y, scaled_h / max(image.size()[1], 1))
            x += scaled_w + spacing
            row_height = max(row_height, scaled_h)
        return row_height + spacing

    def layout_random(self) -> None:
        span = max(1500, int(math.sqrt(max(len(self.images), 1)) * 1500))
        for image in self.images:
            image.set_target(
                random.uniform(0.0, span),
                random.uniform(0.0, span),
                random.uniform(0.25, 1.25),
            )
        self.animation_progress = 0.0

    def bounding_rect(self) -> tuple[float, float, float, float]:
        if not self.images:
            return 0.0, 0.0, 1.0, 1.0
        left, top, right, bottom = self.images[0].rect()
        for image in self.images[1:]:
            i_left, i_top, i_right, i_bottom = image.rect()
            left = min(left, i_left)
            top = min(top, i_top)
            right = max(right, i_right)
            bottom = max(bottom, i_bottom)
        return left, top, right, bottom

    def selection_bounding_rect(self) -> tuple[float, float, float, float] | None:
        selected = self.selected_images()
        if not selected:
            return None
        left, top, right, bottom = selected[0].rect()
        for image in selected[1:]:
            i_left, i_top, i_right, i_bottom = image.rect()
            left = min(left, i_left)
            top = min(top, i_top)
            right = max(right, i_right)
            bottom = max(bottom, i_bottom)
        return left, top, right, bottom

    def visible_images(self, clip_rect: tuple[float, float, float, float]) -> list[Image]:
        return [image for image in self.images if image.overlaps(clip_rect)]

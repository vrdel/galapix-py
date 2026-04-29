from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .image import Image


@dataclass(slots=True)
class Workspace:
    images: list[Image] = field(default_factory=list)
    animation_progress: float = 1.0
    search_query: str = ""

    def add_image(self, image: Image) -> None:
        self.images.append(image)

    def clear(self) -> None:
        self.images.clear()

    def clear_selection(self) -> None:
        for image in self.images:
            image.selected = False

    def get_image_at(self, x: float, y: float) -> Image | None:
        for image in reversed(self.filtered_images()):
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

    def filtered_selected_images(self) -> list[Image]:
        return [image for image in self.filtered_images() if image.selected]

    def set_search_query(self, query: str) -> None:
        self.search_query = query

    def clear_search(self) -> None:
        self.search_query = ""

    def has_active_search(self) -> bool:
        return bool(self.search_query)

    def matches_search(self, image: Image) -> bool:
        if not self.search_query:
            return True
        return self.search_query.casefold() in Path(image.url).name.casefold()

    def filtered_images(self) -> list[Image]:
        if not self.search_query:
            return list(self.images)
        return [image for image in self.images if self.matches_search(image)]

    def update(self, delta: float) -> None:
        if self.animation_progress < 1.0:
            self.animation_progress = min(1.0, self.animation_progress + delta * 2.0)
            for image in self.images:
                image.update_animation(self.animation_progress)

    def is_animated(self) -> bool:
        return self.animation_progress < 1.0

    def sort_by_url(self, reverse: bool = False, case_insensitive: bool = False) -> None:
        if case_insensitive:
            self.images.sort(key=lambda image: image.url.lower(), reverse=reverse)
        else:
            self.images.sort(key=lambda image: image.url, reverse=reverse)

    def sort_by_name(self, reverse: bool = False) -> None:
        def name_key(image: Image) -> tuple[str, str]:
            path = Path(image.url)
            return (path.name.lower(), image.url)

        self.images.sort(key=name_key, reverse=reverse)

    def sort_by_mtime(self, reverse: bool = False) -> None:
        def mtime_key(image: Image) -> tuple[int, str]:
            try:
                mtime_ns = Path(image.url).stat().st_mtime_ns
            except FileNotFoundError:
                mtime_ns = -1
            return (mtime_ns, image.url)

        self.images.sort(key=mtime_key, reverse=reverse)

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

    def layout_row(
        self,
        spacing: float = 40.0,
        target_height: float = 1000.0,
        max_per_row: int | None = None,
        images: list[Image] | None = None,
    ) -> None:
        target_images = self.images if images is None else images
        if not target_images:
            self.animation_progress = 1.0
            return
        if max_per_row is None:
            image_count = len(target_images)
            if image_count > 2:
                max_per_row = math.ceil(math.sqrt(image_count))
        y = 0.0
        row_images: list[tuple[Image, float, float]] = []
        for index, image in enumerate(target_images):
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

    def bounding_rect(self, images: list[Image] | None = None) -> tuple[float, float, float, float]:
        target_images = self.images if images is None else images
        if not target_images:
            return 0.0, 0.0, 1.0, 1.0
        left, top, right, bottom = target_images[0].rect()
        for image in target_images[1:]:
            i_left, i_top, i_right, i_bottom = image.rect()
            left = min(left, i_left)
            top = min(top, i_top)
            right = max(right, i_right)
            bottom = max(bottom, i_bottom)
        return left, top, right, bottom

    def filtered_bounding_rect(self) -> tuple[float, float, float, float]:
        return self.bounding_rect(self.filtered_images())

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

    def filtered_selection_bounding_rect(self) -> tuple[float, float, float, float] | None:
        selected = self.filtered_selected_images()
        if not selected:
            return None
        return self.bounding_rect(selected)

    def visible_images(self, clip_rect: tuple[float, float, float, float]) -> list[Image]:
        return [image for image in self.filtered_images() if image.overlaps(clip_rect)]

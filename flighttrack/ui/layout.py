"""Screen regions, derived from whatever surface size we were given."""

from dataclasses import dataclass
from math import hypot

import pygame

TOP_BAR_PX = 48  # tall enough to hold touch targets
BOTTOM_BAR_PX = 32
RING_MARGIN_PX = 8


@dataclass(frozen=True)
class Layout:
    top: pygame.Rect
    bottom: pygame.Rect
    view: pygame.Rect  # the map, with the range rings centered in it
    panel: pygame.Rect  # selected aircraft / nearest list, right edge

    @property
    def ring_radius(self) -> int:
        """Radius in pixels of the outer ring, which is the selected range."""
        return min(self.view.width, self.view.height) // 2 - RING_MARGIN_PX

    @property
    def corner_factor(self) -> float:
        """How far the view's corners are from its center, as a multiple of the range."""
        return hypot(self.view.width / 2, self.view.height / 2) / self.ring_radius


def compute_layout(width: int, height: int) -> Layout:
    panel_width = min(max(round(width * 0.26), 200), 280)
    middle = height - TOP_BAR_PX - BOTTOM_BAR_PX
    return Layout(
        top=pygame.Rect(0, 0, width, TOP_BAR_PX),
        bottom=pygame.Rect(0, height - BOTTOM_BAR_PX, width, BOTTOM_BAR_PX),
        view=pygame.Rect(0, TOP_BAR_PX, width - panel_width, middle),
        panel=pygame.Rect(width - panel_width, TOP_BAR_PX, panel_width, middle),
    )

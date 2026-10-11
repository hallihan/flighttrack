"""The FlightTrack mark: a radar scope with one small aircraft on it.

One description of it, in a unit square, drawn two ways: with pygame for the display's top bar,
and as SVG text for the web page (scripts/make_icon.py writes the files the portal serves).
"""

from functools import lru_cache
from math import cos, hypot, radians, sin

import pygame

CYAN = (70, 215, 230)  # the rings, as the accent color of the display
CYAN_DIM = (46, 142, 158)  # the inner ring: clear of the lit sweep it crosses
AMBER = (255, 176, 46)  # the aircraft, as low traffic is drawn
NIGHT = (8, 16, 28)  # behind it, where it needs a background of its own

# Everything in a square 0-1, y downward.
CENTER = (0.5, 0.5)
OUTER_R, OUTER_W = 0.43, 0.075
INNER_R, INNER_W = 0.215, 0.045
# Strong enough to show when the mark is only 16 pixels across, light enough that the inner
# ring still shows through it when the mark is large.
SWEEP_OPACITY = 0.38
SWEEP = (121.0, 179.0)  # the lit wedge, about 10 o'clock: degrees counter-clockwise from east
# The aircraft flies out of the scope towards the top-right corner: its tail just clear of
# the dot in the middle, its nose in the corner (inside the rounded corner of the web icon).
PLANE_TAIL, PLANE_NOSE = (0.538, 0.462), (0.93, 0.07)
PLANE_AT = ((PLANE_TAIL[0] + PLANE_NOSE[0]) / 2, (PLANE_TAIL[1] + PLANE_NOSE[1]) / 2)
PLANE_LEN = hypot(PLANE_NOSE[0] - PLANE_TAIL[0], PLANE_NOSE[1] - PLANE_TAIL[1])
PLANE_HEADING = 45.0  # clockwise from north
# Half of the aircraft, nose up, its length 1 and its middle at the origin.
_HALF = ((0.0, -0.5), (0.085, -0.36), (0.085, -0.1), (0.5, 0.13), (0.5, 0.24), (0.085, 0.12),
         (0.07, 0.33), (0.22, 0.43), (0.22, 0.5), (0.0, 0.45))  # fmt: skip


def plane_points() -> list[tuple[float, float]]:
    """The aircraft's outline in the unit square."""
    outline = list(_HALF) + [(-x, y) for x, y in reversed(_HALF[1:-1])]
    a = radians(PLANE_HEADING)
    return [
        (
            PLANE_AT[0] + (x * cos(a) - y * sin(a)) * PLANE_LEN,
            PLANE_AT[1] + (x * sin(a) + y * cos(a)) * PLANE_LEN,
        )
        for x, y in outline
    ]


def sweep_points(steps: int = 12) -> list[tuple[float, float]]:
    """The lit wedge: the center, then along the inside of the outer ring."""
    radius = OUTER_R - OUTER_W / 2
    points = [CENTER]
    for step in range(steps + 1):
        a = radians(SWEEP[0] + (SWEEP[1] - SWEEP[0]) * step / steps)
        points.append((CENTER[0] + radius * cos(a), CENTER[1] - radius * sin(a)))
    return points


@lru_cache(maxsize=8)
def icon(size: int, background: bool = False) -> pygame.Surface:
    """The mark as a picture size pixels square. With background, on a rounded dark square
    (for a browser tab, which may be light); without, on nothing (for the top bar)."""
    big = size * 4  # drawn large and reduced: pygame's own shapes are not smoothed
    canvas = pygame.Surface((big, big), pygame.SRCALPHA)

    def at(point: tuple[float, float]) -> tuple[float, float]:
        return point[0] * big, point[1] * big

    if background:
        pygame.draw.rect(canvas, NIGHT, canvas.get_rect(), border_radius=round(big * 0.2))
    wedge = pygame.Surface((big, big), pygame.SRCALPHA)
    lit = (*CYAN, round(255 * SWEEP_OPACITY))
    pygame.draw.polygon(wedge, lit, [at(p) for p in sweep_points()])
    canvas.blit(wedge, (0, 0))
    pygame.draw.circle(canvas, CYAN_DIM, at(CENTER), INNER_R * big, max(round(INNER_W * big), 1))
    pygame.draw.circle(canvas, CYAN, at(CENTER), OUTER_R * big, max(round(OUTER_W * big), 1))
    pygame.draw.circle(canvas, CYAN, at(CENTER), 0.05 * big)
    plane = [at(p) for p in plane_points()]
    # A dark edge round the aircraft, so it stands off the rings. Drawn as a thick line along
    # each side with a disc at each corner: pygame's own thick outline leaves a notch at
    # every sharp corner, which showed at the wing tips.
    edge = max(round(0.05 * big), 2)
    for start, end in zip(plane, plane[1:] + plane[:1], strict=True):
        pygame.draw.line(canvas, NIGHT, start, end, edge)
        pygame.draw.circle(canvas, NIGHT, start, edge / 2)
    pygame.draw.polygon(canvas, AMBER, plane)
    return pygame.transform.smoothscale(canvas, (size, size))


def svg(background: bool = True) -> str:
    """The same mark as an SVG document."""

    def rgb(color: tuple[int, int, int]) -> str:
        return "#{:02x}{:02x}{:02x}".format(*color)

    def path(points: list[tuple[float, float]]) -> str:
        return " ".join(f"{x * 100:.1f},{y * 100:.1f}" for x, y in points)

    cx, cy = CENTER[0] * 100, CENTER[1] * 100
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">']
    if background:
        parts.append(f'<rect width="100" height="100" rx="20" fill="{rgb(NIGHT)}"/>')
    parts += [
        f'<polygon points="{path(sweep_points())}" fill="{rgb(CYAN)}" '
        f'fill-opacity="{SWEEP_OPACITY}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{INNER_R * 100 - INNER_W * 50:.1f}" fill="none" '
        f'stroke="{rgb(CYAN_DIM)}" stroke-width="{INNER_W * 100:.1f}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="{OUTER_R * 100 - OUTER_W * 50:.1f}" fill="none" '
        f'stroke="{rgb(CYAN)}" stroke-width="{OUTER_W * 100:.1f}"/>',
        f'<circle cx="{cx}" cy="{cy}" r="5" fill="{rgb(CYAN)}"/>',
        f'<polygon points="{path(plane_points())}" fill="{rgb(AMBER)}" stroke="{rgb(NIGHT)}" '
        'stroke-width="3" stroke-linejoin="round" paint-order="stroke"/>',
        "</svg>",
    ]
    return "\n".join(parts) + "\n"

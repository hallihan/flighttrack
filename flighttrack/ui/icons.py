"""Aircraft silhouettes, one per kind, drawn as small rotated sprites.

Shapes are described once, nose up, in a unit square (nose at y = -1, tail at y = +1), as the
right-hand half of a symmetric outline. Sprites are rendered oversized and scaled down so the
edges are smooth, then cached by kind, heading, color and size.
"""

from dataclasses import dataclass
from functools import lru_cache
from math import ceil, cos, radians, sin

import pygame

from flighttrack.aircraft_types import Kind
from flighttrack.ui.theme import Color

Point = tuple[float, float]

OUTLINE: Color = (6, 10, 16)
HEADING_STEP_DEG = 5
_SUPERSAMPLE = 4


@dataclass(frozen=True)
class Shape:
    half_length_px: float  # nose-to-center distance at normal zoom
    body: tuple[Point, ...]  # right half of the outline, nose to tail
    parts: tuple[tuple[Point, ...], ...] = ()  # extra right-side polygons, mirrored to the left
    rotor_radius: float = 0.0  # helicopters: rotor disc, in unit coordinates
    rotor_center_y: float = 0.0


def _swept_jet(
    fuselage: float, wing_root: float, span: float, sweep: float, chord: float
) -> tuple[Point, ...]:
    """A jet with underwing engines: fuselage half-width, where the wing starts, and its shape."""
    return (
        (0.0, -1.0),
        (fuselage * 0.75, -0.88),
        (fuselage, -0.70),
        (fuselage, wing_root),
        (span, wing_root + sweep),
        (span, wing_root + sweep + chord * 0.4),
        (fuselage, wing_root + chord),
        (fuselage, 0.68),
        (0.36, 0.90),
        (0.36, 1.0),
        (fuselage * 0.6, 0.90),
        (0.0, 1.0),
    )


def _engine(x: float, y: float, width: float = 0.09, length: float = 0.24) -> tuple[Point, ...]:
    return ((x, y), (x + width, y), (x + width, y + length), (x, y + length))


_REAR_ENGINE_JET = (
    # Slim fuselage, wing set back, engines on the tail, T-tail.
    (0.0, -1.0),
    (0.06, -0.86),
    (0.08, -0.66),
    (0.08, -0.02),
    (0.78, 0.36),
    (0.78, 0.46),
    (0.08, 0.26),
    (0.08, 0.80),
    (0.34, 0.94),
    (0.34, 1.0),
    (0.0, 0.96),
)

_STRAIGHT_WING = (
    # Tapered straight wing, as on a turboprop or a light twin.
    (0.0, -1.0),
    (0.08, -0.84),
    (0.10, -0.60),
    (0.10, -0.26),
    (0.95, -0.16),
    (0.95, -0.02),
    (0.10, 0.06),
    (0.08, 0.72),
    (0.38, 0.78),
    (0.38, 0.92),
    (0.05, 0.94),
    (0.0, 1.0),
)

SHAPES: dict[Kind, Shape] = {
    Kind.WIDEBODY: Shape(
        15.0,
        _swept_jet(0.11, -0.22, 0.92, 0.52, 0.38),
        (_engine(0.30, -0.16), _engine(0.56, 0.02)),
    ),
    Kind.NARROWBODY: Shape(
        12.0, _swept_jet(0.09, -0.16, 0.84, 0.46, 0.32), (_engine(0.32, -0.12),)
    ),
    Kind.REGIONAL_JET: Shape(10.0, _REAR_ENGINE_JET, (_engine(0.08, 0.44, 0.10, 0.26),)),
    Kind.BUSINESS_JET: Shape(8.5, _REAR_ENGINE_JET, (_engine(0.08, 0.40, 0.12, 0.28),)),
    Kind.TURBOPROP: Shape(10.5, _STRAIGHT_WING, (_engine(0.32, -0.46, 0.11, 0.42),)),
    Kind.PISTON_TWIN: Shape(8.5, _STRAIGHT_WING, (_engine(0.30, -0.44, 0.13, 0.40),)),
    Kind.HIGH_WING: Shape(
        # Cessna: a long constant-chord wing well forward, slim tail boom.
        8.5,
        (
            (0.0, -1.0),
            (0.12, -0.92),
            (0.13, -0.56),
            (1.0, -0.56),
            (1.0, -0.22),
            (0.13, -0.22),
            (0.07, 0.70),
            (0.40, 0.72),
            (0.40, 0.94),
            (0.0, 1.0),
        ),
    ),
    Kind.LOW_WING: Shape(
        # Piper: a broad "Hershey bar" wing at mid-fuselage, shorter span.
        8.5,
        (
            (0.0, -1.0),
            (0.13, -0.90),
            (0.15, -0.30),
            (0.88, -0.30),
            (0.88, 0.14),
            (0.15, 0.14),
            (0.08, 0.72),
            (0.42, 0.72),
            (0.42, 0.96),
            (0.0, 1.0),
        ),
    ),
    Kind.COMPOSITE: Shape(
        # Cirrus: pointed nose, slender tapered wing, small swept tail.
        8.5,
        (
            (0.0, -1.0),
            (0.10, -0.80),
            (0.14, -0.40),
            (1.0, -0.20),
            (1.0, -0.08),
            (0.14, 0.02),
            (0.07, 0.70),
            (0.34, 0.84),
            (0.34, 0.96),
            (0.0, 0.92),
        ),
    ),
    Kind.HELICOPTER: Shape(
        9.0,
        (
            (0.0, -0.62),
            (0.20, -0.48),
            (0.25, -0.12),
            (0.12, 0.22),
            (0.05, 0.84),
            (0.24, 0.84),
            (0.24, 1.0),
            (0.0, 1.0),
        ),
        rotor_radius=0.80,
        rotor_center_y=-0.14,
    ),
    Kind.HEAVY_HELICOPTER: Shape(
        11.5,
        (
            (0.0, -0.70),
            (0.24, -0.56),
            (0.28, 0.10),
            (0.14, 0.34),
            (0.06, 0.84),
            (0.26, 0.84),
            (0.26, 1.0),
            (0.0, 1.0),
        ),
        rotor_radius=0.82,
        rotor_center_y=-0.12,
    ),
    Kind.UNKNOWN: Shape(9.0, ((0.0, -1.0), (0.70, 0.80), (0.0, 0.42))),
}


def _mirrored(half: tuple[Point, ...]) -> list[Point]:
    """Full outline from its right half: down the right side, back up the left."""
    return [*half, *((-x, y) for x, y in reversed(half) if x != 0.0)]


def heading_bucket(track_deg: float) -> int:
    """Heading rounded to the sprite step, so nearby headings share a sprite."""
    return round(track_deg / HEADING_STEP_DEG) * HEADING_STEP_DEG % 360


@lru_cache(maxsize=4096)
def sprite(kind: Kind, heading: int, color: Color, scale: float = 1.0) -> pygame.Surface:
    """Sprite for an aircraft of this kind pointing along heading (degrees true)."""
    shape = SHAPES[kind]
    half = shape.half_length_px * scale
    size = ceil(half * 2.5) | 1  # odd, so there is a center pixel
    big = size * _SUPERSAMPLE
    unit = half * _SUPERSAMPLE
    c, s = cos(radians(heading)), sin(radians(heading))

    def place(points: list[Point]) -> list[Point]:
        return [
            (big / 2 + (x * c - y * s) * unit, big / 2 + (x * s + y * c) * unit) for x, y in points
        ]

    canvas = pygame.Surface((big, big), pygame.SRCALPHA)
    edge = 2 * _SUPERSAMPLE
    polygons = [place(_mirrored(shape.body))]
    for part in shape.parts:
        polygons.append(place(list(part)))
        polygons.append(place([(-x, y) for x, y in part]))
    # A dark edge first, then the color over it, so the shape reads on any background.
    for points in polygons:
        pygame.draw.polygon(canvas, OUTLINE, points, width=edge)
    for points in polygons:
        pygame.draw.polygon(canvas, color, points)

    if shape.rotor_radius:
        (center,) = place([(0.0, shape.rotor_center_y)])
        radius = shape.rotor_radius * unit
        pygame.draw.circle(canvas, OUTLINE, center, radius + _SUPERSAMPLE, width=3 * _SUPERSAMPLE)
        pygame.draw.circle(canvas, color, center, radius, width=_SUPERSAMPLE)
        for angle in (45, 135):
            dx, dy = cos(radians(heading + angle)) * radius, sin(radians(heading + angle)) * radius
            start = (center[0] - dx, center[1] - dy)
            end = (center[0] + dx, center[1] + dy)
            pygame.draw.line(canvas, color, start, end, width=_SUPERSAMPLE)

    return pygame.transform.smoothscale(canvas, (size, size))

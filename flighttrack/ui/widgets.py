"""Small in-house touch widgets."""

import time
from collections.abc import Callable
from functools import lru_cache
from math import asin, cos, pi, sin

import pygame

from flighttrack.ui import theme


class Button:
    """A tap target. Fires on release inside the rect, like a physical key."""

    HOLD_S = 0.6  # how long a press must last to count as a long one
    clock = staticmethod(time.monotonic)

    def __init__(self, rect: pygame.Rect, label: str, on_tap: Callable[[], None]) -> None:
        self.rect = rect
        self.label = label
        self.on_tap = on_tap
        self.on_hold: Callable[[], None] | None = None  # for a long press, if it has a use
        self.enabled = True
        self._pressed = False
        self._pressed_at = 0.0
        self._held = False  # the long press has done its work: the release does nothing

    def tick(self) -> None:
        """Called every frame. A long press acts while the finger is still down, as soon as
        it has been down long enough: waiting for the release would feel like nothing
        happened."""
        if self._pressed and self.on_hold is not None and not self._held:
            if self.clock() - self._pressed_at >= self.HOLD_S:
                self._held = True
                self.on_hold()

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if the event was consumed."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._pressed = self.enabled and self.rect.collidepoint(event.pos)
            self._pressed_at, self._held = self.clock(), False
            return self._pressed
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._pressed:
            self.tick()  # a long press released between frames still counts
            self._pressed = False
            if self.rect.collidepoint(event.pos) and not self._held:
                self.on_tap()
            return True
        return False

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        if not self.enabled:
            fill, text = theme.BUTTON_DISABLED, theme.TEXT_DIM
        elif self._pressed:
            fill, text = theme.BUTTON_PRESSED, theme.TEXT
        else:
            fill, text = theme.BUTTON_FILL, theme.TEXT
        pygame.draw.rect(surface, fill, self.rect, border_radius=8)
        pygame.draw.rect(surface, theme.EDGE, self.rect, width=1, border_radius=8)
        rendered = theme.text(font, self.label, text)
        surface.blit(rendered, rendered.get_rect(center=self.rect.center))


def _chip_text(active: bool, pressed: bool) -> theme.Color:
    """Text on a chip: on the lit fill, the accent's own text color."""
    if pressed:
        return theme.TEXT
    return theme.ON_ACCENT if active else theme.TEXT_DIM


class Toggle(Button):
    """An on/off chip. The whole rect is the tap target; the chip is drawn inset within it."""

    def __init__(
        self, rect: pygame.Rect, label: str, on_tap: Callable[[], None], active: bool
    ) -> None:
        super().__init__(rect, label, on_tap)
        self.active = active

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        chip = self.rect.inflate(-6, -14)
        if self._pressed:
            fill = theme.BUTTON_PRESSED
        else:
            fill = theme.TOGGLE_ON if self.active else theme.BUTTON_FILL
        pygame.draw.rect(surface, fill, chip, border_radius=6)
        pygame.draw.rect(surface, theme.EDGE, chip, width=1, border_radius=6)
        color = _chip_text(self.active, self._pressed)
        rendered = theme.text(font, self.label, color)
        surface.blit(rendered, rendered.get_rect(center=chip.center))


class FilterPill(Toggle):
    """A legend entry that is also a switch, drawn as the other chips in the top bar are, with
    a dot in the color it explains. Off, the dot is hollow and that traffic is hidden."""

    PADDING = 6
    DOT = 5  # radius

    def __init__(
        self, rect: pygame.Rect, label: str, color: theme.Color, on_tap: Callable[[], None]
    ) -> None:
        super().__init__(rect, label, on_tap, True)
        self.color = color

    @classmethod
    def width_for(cls, font: pygame.font.Font, label: str) -> int:
        return 3 + cls.PADDING + 2 * cls.DOT + 3 + font.size(label)[0] + cls.PADDING - 1 + 3

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        chip = self.rect.inflate(-6, -14)
        if self._pressed:
            fill = theme.BUTTON_PRESSED
        else:
            fill = theme.TOGGLE_ON if self.active else theme.BUTTON_FILL
        pygame.draw.rect(surface, fill, chip, border_radius=6)
        pygame.draw.rect(surface, theme.EDGE, chip, width=1, border_radius=6)
        dot = (chip.left + self.PADDING + self.DOT, chip.centery)
        if self.active:
            # A dark rim keeps the color distinct from the lit chip behind it.
            pygame.draw.circle(surface, theme.DOT_RIM, dot, self.DOT + 1)
            pygame.draw.circle(surface, self.color, dot, self.DOT)
        else:
            pygame.draw.circle(surface, self.color, dot, self.DOT, width=1)
        text = theme.text(font, self.label, _chip_text(self.active, self._pressed))
        surface.blit(text, text.get_rect(midleft=(dot[0] + self.DOT + 3, chip.centery)))


class Pill(Toggle):
    """A small rounded switch for a bar too shallow for a chip. Its rect, the tap target, may
    be taller than the bar; the pill is drawn within `face`."""

    def __init__(
        self, rect: pygame.Rect, face: pygame.Rect, label: str, on_tap: Callable[[], None]
    ) -> None:
        super().__init__(rect, label, on_tap, False)
        self.face = face

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        radius = self.face.height // 2
        if self._pressed or self.active:
            fill = theme.BUTTON_PRESSED if self._pressed else theme.TOGGLE_ON
            pygame.draw.rect(surface, fill, self.face, border_radius=radius)
        pygame.draw.rect(surface, theme.EDGE, self.face, width=1, border_radius=radius)
        text = theme.text(font, self.label, _chip_text(self.active, self._pressed))
        surface.blit(text, text.get_rect(center=self.face.center))


def draw_gear(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    hole: theme.Color | None = None,
) -> None:
    """A settings cog: a toothed ring. hole is the color of whatever it is drawn on (the
    bar's, unless given)."""
    hole = hole or theme.BAR_FILL
    cx, cy = center
    teeth, outer, inner = 8, 12.0, 8.5
    points = []
    for i in range(teeth * 4):
        radius = outer if i % 4 in (0, 1) else inner
        angle = (i - 0.5) * pi / (teeth * 2)
        points.append((cx + cos(angle) * radius, cy + sin(angle) * radius))
    pygame.draw.polygon(surface, color, points)
    pygame.draw.circle(surface, hole, center, 4)


@lru_cache(maxsize=32)
def glass_face(size: tuple[int, int], state: str) -> pygame.Surface:
    """The face of a button that sits on the map: tinted glass, so the map and the aircraft
    under it still show. A dark tint for contrast with the label, a little lighter toward
    the top as if lit from above, and a pale rim. state is "up", "down" or "off"."""
    width, height = size
    tint, strength = {
        "up": ((14, 30, 50), 120),
        "down": ((70, 125, 165), 185),
        "off": ((10, 18, 28), 85),
    }[state]
    face = pygame.Surface(size, pygame.SRCALPHA)
    for y in range(height):
        lift = round(34 * max(0.0, 1 - y / (height * 0.55)))  # the sheen, fading out half-way
        color = tuple(min(channel + lift, 255) for channel in tint)
        pygame.draw.line(face, (*color, strength), (0, y), (width - 1, y))
    # Round the corners by keeping only what a rounded rectangle covers.
    shape = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(shape, (255, 255, 255, 255), shape.get_rect(), border_radius=9)
    face.blit(shape, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    rim = (190, 220, 245, 120 if state != "off" else 60)
    pygame.draw.rect(face, rim, face.get_rect(), width=1, border_radius=9)
    return face


class GlassButton(Button):
    """A button on the map, see-through."""

    def _state(self) -> str:
        return "off" if not self.enabled else ("down" if self._pressed else "up")

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        surface.blit(glass_face(self.rect.size, self._state()), self.rect.topleft)
        color = theme.MAP_TEXT if self.enabled else theme.MAP_TEXT_DIM
        # Shadowed, as all text over the map is: the glass does not hide what is behind it.
        rendered = theme.shadowed_text(font, self.label, color)
        surface.blit(rendered, rendered.get_rect(center=self.rect.center))


@lru_cache(maxsize=4)
def gear_icon(color: theme.Color) -> pygame.Surface:
    """The settings cog with a real hole in the middle, for drawing over anything."""
    icon = pygame.Surface((30, 30), pygame.SRCALPHA)
    draw_gear(icon, (15, 15), (6, 10, 16))  # a dark edge, for contrast over a bright map
    shadow = pygame.transform.smoothscale(icon, (34, 34))
    out = pygame.Surface((34, 34), pygame.SRCALPHA)
    out.blit(shadow, (0, 0))
    top = pygame.Surface((30, 30), pygame.SRCALPHA)
    draw_gear(top, (15, 15), color)
    out.blit(top, (2, 2))
    pygame.draw.circle(out, (0, 0, 0, 0), (17, 17), 4)
    return out


class IconButton(GlassButton):
    """A glass button that shows the settings cog instead of a text label."""

    def draw(self, surface: pygame.Surface, font: pygame.font.Font) -> None:
        surface.blit(glass_face(self.rect.size, self._state()), self.rect.topleft)
        icon = gear_icon(theme.TEXT)
        surface.blit(icon, icon.get_rect(center=self.rect.center))


def draw_eye(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A cartoon eye, 30 px across at scale 1: a lens of two matching arcs round a big round
    iris, with a glint. The iris is solid when on, an outline when off."""
    cx, cy = center
    w, h = 15 * scale, 10 * scale
    radius = (w * w + h * h) / (2 * h)  # of the circle each lid is an arc of
    reach = asin(w / radius)
    lid = [
        (radius * sin(t), radius * cos(t) - (radius - h))
        for t in (-reach + 2 * reach * i / 16 for i in range(17))
    ]
    outline = [(cx + x, cy - y) for x, y in lid] + [(cx - x, cy + y) for x, y in lid]
    pygame.draw.polygon(surface, color, outline, width=2)
    iris = round(6 * scale)
    pygame.draw.circle(surface, color, center, iris, width=0 if on else 2)
    glint = (round(cx - iris * 0.35), round(cy - iris * 0.35))
    pygame.draw.circle(surface, theme.PANEL_FILL if on else color, glint, max(1, round(2 * scale)))


# A plastic push pin seen side on, point down, in its own units (about 24 tall): a broad flat
# head, a waisted grip, a flange, then the steel point. Drawn leaning, as a pin in a board.
_PIN_BODY = [
    (-6.5, -12.0), (6.5, -12.0), (6.5, -8.5), (4.0, -8.0), (3.5, -3.5), (7.0, -2.5),
    (7.0, 0.0), (-7.0, 0.0), (-7.0, -2.5), (-3.5, -3.5), (-4.0, -8.0), (-6.5, -8.5),
]  # fmt: skip
_PIN_POINT = ((0.0, 0.0), (0.0, 11.0))
_PIN_LEAN = 0.45  # radians, top to the right


def draw_pin(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A push pin, 24 px tall at scale 1. Always a solid silhouette, which reads at small
    sizes where an outline does not: on and off are told apart by color."""
    cx, cy = center
    c, s = cos(_PIN_LEAN), sin(_PIN_LEAN)

    def placed(x: float, y: float) -> tuple[float, float]:
        return cx + (x * c - y * s) * scale, cy + (x * s + y * c) * scale

    start, end = (placed(*p) for p in _PIN_POINT)
    pygame.draw.line(surface, color, start, end, 2)
    pygame.draw.polygon(surface, color, [placed(*p) for p in _PIN_BODY])


def draw_heart(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    filled: bool,
    scale: float = 1.0,
) -> None:
    """A heart, 26 px across at scale 1: solid for a favorite, an outline otherwise."""
    cx, cy = center
    k = 0.8 * scale
    points = []
    # The curve comes to its point in a sliver a pixel wide, which at small sizes reads as a
    # drip: the steps either side of it are left out, so the point is made by straight sides.
    for step in (*range(0, 168, 12), 180, *range(204, 360, 12)):
        angle = step * pi / 180
        # The classic heart curve, scaled to size.
        x = 16 * sin(angle) ** 3
        y = 13 * cos(angle) - 5 * cos(2 * angle) - 2 * cos(3 * angle) - cos(4 * angle)
        points.append((cx + x * k, cy - y * k - scale))
    pygame.draw.polygon(surface, color, points, width=0 if filled else max(2, round(3 * scale)))


def draw_home(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A house, 24 px across at scale 1: filled for home, an outline otherwise."""
    cx, cy = center

    def at(x: float, y: float) -> tuple[float, float]:
        return cx + x * scale, cy + y * scale

    outline = [at(0, -11), at(12, 0), at(8, 0), at(8, 11), at(-8, 11), at(-8, 0), at(-12, 0)]
    pygame.draw.polygon(surface, color, outline, width=0 if on else 2)


def draw_trash(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A waste bin, 22 px tall at scale 1: a lid with a handle, a tapering body with ribs."""
    cx, cy = center

    def at(x: float, y: float) -> tuple[float, float]:
        return cx + x * scale, cy + y * scale

    width = max(2, round(2 * scale))
    pygame.draw.line(surface, color, at(-10, -7), at(10, -7), width)  # the lid
    pygame.draw.lines(
        surface, color, False, [at(-3, -7), at(-3, -10), at(3, -10), at(3, -7)], width
    )
    body = [at(-8, -4), at(8, -4), at(6, 11), at(-6, 11)]
    pygame.draw.polygon(surface, color, body, width=0 if on else width)
    if not on:
        for x in (-3, 3):
            pygame.draw.line(surface, color, at(x, -1), at(x * 0.8, 8), 1)


def draw_plus(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A plus sign, 20 px across at scale 1."""
    cx, cy = center
    arm, width = round(10 * scale), max(3, round(3 * scale))
    pygame.draw.line(surface, color, (cx - arm, cy), (cx + arm, cy), width)
    pygame.draw.line(surface, color, (cx, cy - arm), (cx, cy + arm), width)


def draw_edit(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A pencil, 22 px at scale 1, writing down to the left: to type something new."""
    cx, cy = center

    def at(x: float, y: float) -> tuple[float, float]:
        return cx + x * scale, cy + y * scale

    body = [at(5, -11), at(11, -5), at(-4, 10), at(-10, 4)]
    pygame.draw.polygon(surface, color, body, width=0 if on else max(2, round(2 * scale)))
    pygame.draw.polygon(surface, color, [at(-10, 4), at(-4, 10), at(-12, 12)])  # the point
    pygame.draw.line(surface, color, at(1, -7), at(7, -1), max(1, round(2 * scale)))


def draw_save(
    surface: pygame.Surface,
    center: tuple[int, int],
    color: theme.Color,
    on: bool,
    scale: float = 1.0,
) -> None:
    """A floppy disk, 22 px at scale 1: to save (on the web page's rows, drawn the same)."""
    cx, cy = center

    def at(x: float, y: float) -> tuple[float, float]:
        return cx + x * scale, cy + y * scale

    width = max(2, round(2 * scale))
    pygame.draw.polygon(
        surface, color, [at(-10, -10), at(6, -10), at(10, -6), at(10, 10), at(-10, 10)], width
    )
    pygame.draw.rect(surface, color, (*at(-5, -10), 9 * scale, 6 * scale), width)
    pygame.draw.rect(surface, color, (*at(-6, 2), 12 * scale, 8 * scale), 0 if on else width)

"""The map view: range rings around home, with aircraft plotted over the map."""

from dataclasses import dataclass

import pygame

from flighttrack.aircraft_types import Kind, classify
from flighttrack.favorites import is_favorite
from flighttrack.feeds.base import Aircraft
from flighttrack.geo.mercator import TILE_PX, offset_px, projector, world_px
from flighttrack.state import TrackPoint
from flighttrack.ui import format as fmt
from flighttrack.ui import icons, theme
from flighttrack.ui.overlays import home_marker

# From this range up the picture is regional, with hundreds of aircraft: ground traffic is
# hidden, icons shrink, and only the nearest few aircraft get labels.
WIDE_RANGE_NM = 100
_WIDE_LABEL_LIMIT = 40
_LABEL_MIN_PX = 8  # no label is narrower than this
_ROTORCRAFT = (Kind.HELICOPTER, Kind.HEAVY_HELICOPTER)
# A tap this close to an aircraft selects it.
TAP_RADIUS_PX = 34


def icon_scale(range_nm: float) -> float:
    if range_nm < WIDE_RANGE_NM:
        return 1.0
    return 0.6 if range_nm < 250 else 0.45


@dataclass(slots=True)
class Drawn:
    """One aircraft, with everything about it the frame needs already worked out: where it
    is now, how far from the center of the view, and how it should look. The look depends
    only on the feed's last answer, so it is worked out when that arrives, not every frame."""

    aircraft: Aircraft
    distance_nm: float
    lat: float
    lon: float
    kind: Kind
    color: theme.Color
    favorite: bool
    label: str
    tag: str
    group: str | None  # the legend group it belongs to (theme.traffic_group)


def look(
    aircraft: Aircraft, favorites: frozenset[str], stale: bool
) -> tuple[Kind, theme.Color, bool, str, str, str | None]:
    """The parts of a Drawn that do not change between feed updates."""
    favorite = is_favorite(aircraft, favorites)
    color = theme.AIRCRAFT_STALE if stale else theme.aircraft_color(aircraft, favorites)
    kind = classify(aircraft.type_code, aircraft.category)
    group = theme.traffic_group(aircraft, favorite)
    return kind, color, favorite, aircraft.label, fmt.altitude_tag(aircraft), group


RIGHT, LEFT = 1, -1
# A label that has changed sides stays there at least this long, whatever happens round it.
LABEL_HOLD_S = 3.0
LABEL_SWEEP_S = 0.3  # how long the move from one side to the other takes
LABEL_FADE_S = 0.3  # how long a label takes to fade in, or out once it has lost its place
FADE_STEPS = 6


@dataclass(slots=True)
class LabelPlace:
    """Where one aircraft's label is: which side, and the move it is making, if any."""

    side: int
    changed_at: float = float("-inf")  # when it last changed sides
    sweep_from: float | None = None  # offset from the aircraft it set out from
    shown: bool = False  # whether it was drawn in the last frame
    # What was last drawn (name, tag, color, offset), so it can be faded out when hidden.
    last: tuple[str, str, theme.Color, int] | None = None
    # How solidly it is drawn, 0-1. It rises while the label has a place and falls while it
    # has none, so a label fades in when it appears and out when it has to go.
    strength: float = 0.0

    def sweeping(self, now: float) -> bool:
        return self.sweep_from is not None and now - self.changed_at < LABEL_SWEEP_S

    def offset(self, now: float, target: float) -> float:
        """Offset of the label's left edge from its aircraft now, given where it is going."""
        if not self.sweeping(now):
            return target
        t = (now - self.changed_at) / LABEL_SWEEP_S
        t = t * t * (3 - 2 * t)  # ease in and out
        return self.sweep_from + (target - self.sweep_from) * t


class LabelMemory:
    """Label placement carried from one frame to the next (see draw_scope)."""

    def __init__(self) -> None:
        self.labels: dict[str, LabelPlace] = {}
        self.busy_until = float("-inf")  # a label is in mid-sweep or mid-fade until then
        self.drawn_at: float | None = None  # when the last frame was laid out


@dataclass(frozen=True)
class Scene:
    """What the map view shows, apart from the aircraft themselves."""

    home: tuple[float, float]
    center: tuple[float, float]  # the middle of the view; differs from home when panned
    zoom: float
    range_nm: float
    ring_opacity: float = 1.0  # 0 invisible, 1 solid
    selected_hex: str | None = None
    favorites: frozenset[str] = frozenset()
    # Recorded paths, (hex, points): the selected aircraft's and those pinned.
    tracks: tuple[tuple[str, tuple[TrackPoint, ...]], ...] = ()
    background: pygame.Surface | None = None
    background_offset: tuple[int, int] = (0, 0)
    stale: bool = False  # the data is old: draw the traffic grayed, so it is not taken as live
    banner: tuple[str, theme.Color] | None = None  # a warning across the top of the map
    highlighted: frozenset[str] = frozenset()  # aircraft being offered in the "which one" list
    # Parts of the view labels must keep off: airport idents painted into the map, and the
    # buttons that sit over it.
    mark_home: bool = True  # False when an airport's own marker already stands at home
    feature_opacity: float = 1.0  # of what describes the ground; the home marker follows it
    obstacles: tuple[pygame.Rect, ...] = ()
    note: str | None = None  # a short notice at the top-left of the map, such as "FOLLOWING ..."


def screen_xy(
    center: tuple[float, float], lat: float, lon: float, zoom: float, view: pygame.Rect
) -> tuple[int, int]:
    """Screen position of a point, which may lie outside the view.

    Uses the map's own projection, so things sit exactly where the tiles put the ground.
    """
    dx, dy = offset_px(center, lat, lon, zoom)
    return round(view.centerx + dx), round(view.centery + dy)


def project(
    center: tuple[float, float], lat: float, lon: float, zoom: float, view: pygame.Rect
) -> tuple[int, int] | None:
    """Screen position of a point, or None if it falls outside the view."""
    x, y = screen_xy(center, lat, lon, zoom, view)
    return (x, y) if view.collidepoint(x, y) else None


# The rings never move within the view, so they are drawn once per range and opacity onto
# their own layer and that layer is blitted every frame. The opacity is baked into the layer's
# own pixels: blending a whole-surface alpha on top of per-pixel alpha, every frame, cost more
# than everything else in an idle frame put together.
_ring_layer: tuple[tuple, pygame.Surface] | None = None


def _rings(
    size: tuple[int, int], fonts: theme.Fonts, range_nm: float, radius: int, opacity: int
) -> pygame.Surface:
    global _ring_layer
    key = (size, id(fonts), range_nm, radius, opacity, theme.RING)
    if _ring_layer is None or _ring_layer[0] != key:
        layer = pygame.Surface(size, pygame.SRCALPHA)
        _paint_rings(layer, layer.get_rect(), fonts, range_nm, radius)
        layer.fill((255, 255, 255, opacity), special_flags=pygame.BLEND_RGBA_MULT)
        _ring_layer = (key, layer)
    return _ring_layer[1]


def _paint_rings(
    surface: pygame.Surface, view: pygame.Rect, fonts: theme.Fonts, range_nm: float, radius: int
) -> None:
    cx, cy = view.center
    pygame.draw.line(surface, theme.RING, (view.left, cy), (view.right, cy))
    pygame.draw.line(surface, theme.RING, (cx, view.top), (cx, view.bottom))
    for fraction in (0.5, 1.0):
        r = round(radius * fraction)
        pygame.draw.circle(surface, theme.RING, (cx, cy), r, width=2 if fraction == 1.0 else 1)
        label = theme.shadowed_text(fonts.small, f"{range_nm * fraction:g}", theme.RING)
        surface.blit(label, (cx + 5, cy - r + 4))
    for letter, anchor in (
        ("N", {"topright": (cx - 5, cy - radius + 4)}),
        ("S", {"bottomright": (cx - 5, cy + radius - 4)}),
        ("E", {"bottomright": (cx + radius - 5, cy - 3)}),
        ("W", {"bottomleft": (cx - radius + 5, cy - 3)}),
    ):
        rendered = theme.shadowed_text(fonts.small, letter, theme.RING)
        surface.blit(rendered, rendered.get_rect(**anchor))
    pygame.draw.circle(surface, theme.HOME, (cx, cy), 3)


def _draw_home(
    surface: pygame.Surface,
    view: pygame.Rect,
    fonts: theme.Fonts,
    at: tuple[int, int],
    opacity: float,
) -> None:
    """Mark where home is, once the map has been dragged away from it."""
    if view.collidepoint(at) and opacity > 0:
        picture, (x, y) = home_marker(fonts.small, "HOME", theme.HOME, round(opacity, 2))
        surface.blit(picture, (at[0] - x, at[1] - y))


_track_memo: dict[str, tuple[object, float, list[tuple[float, float, int | None]]]] = {}


def _track_on_world(
    hex_code: str, track: tuple, zoom: float
) -> list[tuple[float, float, int | None]]:
    """A track's points in world pixels at this zoom, kept until the track or zoom changes.
    (The store hands out the same tuple until the track grows, hence the test by identity.)"""
    memo = _track_memo.get(hex_code)
    if memo is None or memo[0] is not track or memo[1] != zoom:
        placed = [(*world_px(lat, lon, zoom), altitude) for lat, lon, altitude in track]
        memo = _track_memo[hex_code] = (track, zoom, placed)
    return memo[2]


def _draw_track(
    surface: pygame.Surface,
    view: pygame.Rect,
    scene: Scene,
    zoom: float,
    hex_code: str,
    track: tuple[TrackPoint, ...],
    head: tuple[int, int] | None,
) -> None:
    """An aircraft's recorded path, each stretch in the color of its altitude."""
    # Where the points are on the world map changes only when the track grows or the zoom
    # does; where the view is changes every frame while following. So the first is kept and
    # each frame only slides it.
    scale = TILE_PX * 2.0**zoom
    half = scale / 2
    home_x, home_y = world_px(*scene.center, zoom)
    cx, cy = view.center
    points = [
        ((round(cx + (x - home_x + half) % scale - half), round(cy + y - home_y)), altitude)
        for x, y, altitude in _track_on_world(hex_code, track, zoom)
    ]
    if head is not None and points:
        points.append((head, points[-1][1]))  # join the trail to where the icon is drawn
    segments = [
        (start, end, altitude)
        for (start, altitude), (end, _) in zip(points, points[1:], strict=False)
        if start != end and view.clipline(start, end)
    ]
    for start, end, _ in segments:
        pygame.draw.line(surface, icons.OUTLINE, start, end, width=5)
    for start, end, altitude in segments:
        pygame.draw.line(surface, theme.altitude_color(altitude), start, end, width=3)


def draw_scope(
    surface: pygame.Surface,
    view: pygame.Rect,
    ring_radius: int,
    fonts: theme.Fonts,
    scene: Scene,
    aircraft: list[Drawn],
    now: float,
    memory: "LabelMemory | None" = None,
) -> dict[str, tuple[int, int]]:
    """Draw the view. aircraft are nearest the center of the view first.

    memory is where the labels were put last time, kept by the caller from frame to frame;
    without it every frame is laid out afresh. Returns the screen position of every aircraft
    drawn, for tap hit-testing.
    """
    memory = memory if memory is not None else LabelMemory()
    previous_clip = surface.get_clip()
    surface.set_clip(view)
    covered = False
    if scene.background is not None:
        ox, oy = scene.background_offset
        placed = scene.background.get_rect(topleft=(view.left + ox, view.top + oy))
        covered = placed.contains(view)
    if not covered:
        surface.fill(theme.MAP_FILL, view)  # no map, or one that has slid short of an edge
    if scene.background is not None:
        surface.blit(scene.background, placed)

    range_nm, selected_hex, zoom = scene.range_nm, scene.selected_hex, scene.zoom
    # The rings are always in the middle of the view: everything is measured from there.
    opacity = round(255 * min(max(scene.ring_opacity, 0.0), 1.0))
    if opacity > 0:
        surface.blit(_rings(view.size, fonts, range_nm, ring_radius, opacity), view.topleft)
    if scene.center != scene.home and scene.mark_home:
        at = screen_xy(scene.center, *scene.home, zoom, view)
        _draw_home(surface, view, fonts, at, scene.feature_opacity)

    wide = range_nm >= WIDE_RANGE_NM
    scale = icon_scale(range_nm)
    to_screen = projector(scene.center, zoom, view.centerx, view.centery)
    left, right, top, bottom = view.left, view.right, view.top, view.bottom
    positions: dict[str, tuple[int, int]] = {}
    shown: list[tuple[Drawn, tuple[int, int]]] = []
    for d in aircraft:
        a = d.aircraft
        if wide and a.on_ground and a.hex != selected_hex:
            continue
        x, y = pos = to_screen(d.lat, d.lon)
        if left <= x < right and top <= y < bottom:
            positions[a.hex] = pos
            shown.append((d, pos))

    for hex_code, track in scene.tracks:
        if track:
            _draw_track(surface, view, scene, zoom, hex_code, track, positions.get(hex_code))
    for hex_code in _track_memo.keys() - {hex_code for hex_code, _ in scene.tracks}:
        del _track_memo[hex_code]

    # Icons first, so no label ever hides an aircraft.
    for d, pos in shown:
        a = d.aircraft
        if a.on_ground:
            pygame.draw.circle(surface, d.color, pos, 3)
        elif a.track_deg is None and d.kind not in _ROTORCRAFT:
            # No heading to point a silhouette along. (A rotor disc looks the same either way.)
            pygame.draw.circle(surface, icons.OUTLINE, pos, round(6 * scale) + 1)
            pygame.draw.circle(surface, d.color, pos, round(5 * scale))
        else:
            image = icons.sprite(d.kind, icons.heading_bucket(a.track_deg or 0.0), d.color, scale)
            surface.blit(image, image.get_rect(center=pos))
        if a.hex == selected_hex:
            pygame.draw.circle(surface, theme.AIRCRAFT_SELECTED, pos, 20, width=2)
        elif a.hex in scene.highlighted:
            pygame.draw.circle(surface, theme.AIRCRAFT_SELECTED, pos, 14, width=1)

    # Labels: the selected aircraft always gets one, then favorites; the rest are placed
    # nearest-first and skipped when they would overlap one already drawn or something the
    # map has painted there, which keeps busy skies readable. A label goes to the right of
    # its aircraft, or to the left when the right is taken or off the edge.
    #
    # Two aircraft flying side by side would otherwise make each other's labels jump from
    # side to side, frame by frame. So a label remembers its side and keeps it while it can;
    # labels already showing are placed before newcomers, so they keep their place; a label
    # that has just changed sides may not change again for a while; and a change is swept
    # across over a few frames, not jumped, so the eye can follow what it was reading.
    remembered = memory.labels
    # How far a fade moves this frame. On the very first frame there is nothing to fade
    # from: what is there is simply there.
    first_frame = memory.drawn_at is None
    # Never all in one frame, however long ago the last one was.
    fade = 1.0 if first_frame else min(max(now - memory.drawn_at, 0.0) / LABEL_FADE_S, 0.34)
    memory.drawn_at = now

    def order(item: tuple[Drawn, tuple[int, int]]) -> tuple[int, bool]:
        hex_code = item[0].aircraft.hex
        if hex_code == selected_hex:
            return 0, False
        was = remembered.get(hex_code)
        return (1 if item[0].favorite else 2), not (was is not None and was.shown)

    def room(box: pygame.Rect) -> bool:
        return box.left >= left and box.right <= right and box.collidelist(taken) == -1

    taken: list[pygame.Rect] = list(scene.obstacles)
    placed = 0
    labeled = set()
    for d, (x, y) in sorted(shown, key=order):
        a = d.aircraft
        selected = a.hex == selected_hex
        # Ground traffic is unlabeled unless selected: an airport would bury the map in text.
        if a.on_ground and not selected:
            continue
        if wide and placed >= _WIDE_LABEL_LIMIT and not selected:
            break  # in order of importance, so nothing after this would be drawn either
        # A sliver of where the label would start, either side. In a crowd most labels have
        # no room, and this finds that out without fetching their text to measure it.
        if (
            not selected
            and pygame.Rect(x + 15, y - 13, _LABEL_MIN_PX, 26).collidelist(taken) != -1
            and pygame.Rect(x - 15 - _LABEL_MIN_PX, y - 13, _LABEL_MIN_PX, 26).collidelist(taken)
            != -1
        ):
            continue
        color = theme.AIRCRAFT_SELECTED if selected else d.color
        name = theme.shadowed_text(fonts.small, d.label, color)
        tag = theme.shadowed_text(fonts.small, d.tag, color)
        width = max(name.get_width(), tag.get_width())
        offsets = {RIGHT: 15, LEFT: -15 - width}  # of the label's left edge from the aircraft

        was = remembered.get(a.hex)
        side = was.side if was is not None else RIGHT
        box = pygame.Rect(x + offsets[side], y - 13, width, 26)
        if not room(box):
            other = pygame.Rect(x + offsets[-side], y - 13, width, 26)
            settled = was is None or now - was.changed_at >= LABEL_HOLD_S
            if settled and room(other):
                if was is not None:
                    was.changed_at = now
                    # Sweep across from wherever it is now (it may be in mid-sweep already).
                    # One that is not showing just fades in on its new side.
                    was.sweep_from = was.offset(now, offsets[side]) if was.shown else None
                side, box = -side, other
            elif not selected:
                continue
        if was is None:
            was = remembered[a.hex] = LabelPlace(side)
        was.side = side
        labeled.add(a.hex)
        taken.append(box.inflate(6, 4))
        placed += 1
        at = x + round(was.offset(now, offsets[side]))
        if was.sweeping(now):
            memory.busy_until = max(memory.busy_until, was.changed_at + LABEL_SWEEP_S)
        was.last = (d.label, d.tag, color, at - x)
        was.strength = min(was.strength + fade, 1.0)
        step = round(was.strength * FADE_STEPS)
        if step < FADE_STEPS:
            memory.busy_until = max(memory.busy_until, now + LABEL_FADE_S * (1 - was.strength))
            if step == 0:
                continue
            name = theme.faded_text(fonts.small, d.label, color, step / FADE_STEPS)
            tag = theme.faded_text(fonts.small, d.tag, color, step / FADE_STEPS)
        surface.blit(name, (at, box.top))
        surface.blit(tag, (at, box.top + 13))
    # What is not labeled this frame is no longer "showing", and fades out where it was
    # over a few frames (as it fades in when it gets a place); what has left the map is
    # forgotten.
    for hex_code in list(remembered):
        was = remembered[hex_code]
        if hex_code not in positions:
            del remembered[hex_code]
        elif hex_code in labeled:
            was.shown = True
        else:
            was.shown, was.sweep_from = False, None
            was.strength = max(was.strength - fade, 0.0)
            step = round(was.strength * FADE_STEPS)
            if was.last is None or was.strength <= 0:
                was.last = None
                continue
            memory.busy_until = max(memory.busy_until, now + LABEL_FADE_S * was.strength)
            if step > 0:
                label, tag_text, color, offset = was.last
                x, y = positions[hex_code]
                strength = step / FADE_STEPS
                surface.blit(
                    theme.faded_text(fonts.small, label, color, strength), (x + offset, y - 13)
                )
                surface.blit(
                    theme.faded_text(fonts.small, tag_text, color, strength), (x + offset, y)
                )

    y = view.top
    if scene.banner is not None:
        message, color = scene.banner
        strip = pygame.Rect(view.left, view.top, view.width, 26)
        backing = pygame.Surface(strip.size, pygame.SRCALPHA)
        backing.fill((*color, 215))
        surface.blit(backing, strip.topleft)
        words = theme.text(fonts.body, message, theme.BANNER_TEXT)
        surface.blit(words, words.get_rect(center=strip.center))
        y = strip.bottom
    if scene.note:
        words = theme.text(fonts.body, scene.note, theme.ON_ACCENT)
        pill = words.get_rect(topleft=(view.left + 14, y + 9)).inflate(16, 8)
        pygame.draw.rect(surface, theme.ACCENT, pill, border_radius=pill.height // 2)
        surface.blit(words, words.get_rect(center=pill.center))

    surface.set_clip(previous_clip)
    return positions


AIRPORT_TAP_PX = 30
# A tap this close to a target picks it without asking, provided nothing else is nearly as close.
SURE_TAP_PX = 14
CLEARLY_NEARER = 1.6


def tap_radius(base_px: int, range_nm: float) -> int:
    """How close a tap must be to count, shrinking as the icons do at wide ranges: with
    hundreds of aircraft on screen, a fixed fingertip-sized radius takes in half a dozen."""
    return max(round(base_px * icon_scale(range_nm)), 14)


def resolve_tap(near: list[tuple[float, object]]) -> tuple[str, list[object]]:
    """Decide what a tap meant, from the targets within reach as (distance in pixels, target).

    Returns ("clear", []) when nothing is there, ("select", [target]) when it is plain which
    one was meant, and ("choose", targets nearest first) when it is not.
    """
    ordered = sorted(near, key=lambda item: item[0])
    if not ordered:
        return "clear", []
    nearest = ordered[0]
    if len(ordered) == 1:
        return "select", [nearest[1]]
    runner_up = ordered[1]
    if nearest[0] <= SURE_TAP_PX and runner_up[0] >= max(nearest[0], 2.0) * CLEARLY_NEARER:
        return "select", [nearest[1]]
    return "choose", [target for _, target in ordered]


def aircraft_near(
    positions: dict[str, tuple[int, int]], point: tuple[int, int], radius: int = TAP_RADIUS_PX
) -> list[tuple[float, str]]:
    """Every aircraft within reach of a tap, as (distance in pixels, hex), nearest first."""
    found = []
    for hex_code, (x, y) in positions.items():
        d2 = (x - point[0]) ** 2 + (y - point[1]) ** 2
        if d2 <= radius * radius:
            found.append((d2**0.5, hex_code))
    return sorted(found)

"""The side panel: the selected aircraft, the nearest-aircraft list, or diagnostics."""

from dataclasses import dataclass

import pygame

from flighttrack import metar as metar_module
from flighttrack import runways as runways_module
from flighttrack.aircraft_types import LABELS, classify
from flighttrack.airports import Airport
from flighttrack.favorites import is_favorite
from flighttrack.feeds.base import Aircraft
from flighttrack.ui import format as fmt
from flighttrack.ui import theme
from flighttrack.ui.widgets import draw_eye, draw_heart, draw_pin

PADDING = 10
HEADING_HEIGHT = 30
ROW_HEIGHT = theme.MIN_TOUCH_PX
FIELD_HEIGHT = 22
PHOTO_SIZE = (90, 60)  # of a thumbnail in the selected-aircraft pane; two fit side by side
PHOTO_GAP = 8
# The selected-aircraft pane's buttons: faces of one size, evenly spaced along the heading row
# (their touch targets are taller, and meet), each with its icon at this scale.
BUTTON_FACE_HEIGHT = 26
BUTTON_GAP = 8  # between neighboring faces
BUTTON_ICON_SCALE = 0.7


def draw_frame(
    surface: pygame.Surface, rect: pygame.Rect, fonts: theme.Fonts, heading: str
) -> pygame.Rect:
    """Panel background and heading. Returns the area left for content."""
    pygame.draw.rect(surface, theme.PANEL_FILL, rect)
    pygame.draw.line(surface, theme.EDGE, rect.topleft, rect.bottomleft)
    if heading:
        surface.blit(theme.text(fonts.body, heading, theme.ACCENT), (rect.x + PADDING, rect.y + 8))
    return pygame.Rect(rect.x, rect.y + HEADING_HEIGHT, rect.width, rect.height - HEADING_HEIGHT)


@dataclass(frozen=True)
class Details:
    """What has been looked up about the selected aircraft, beyond what the feed says. Any of
    it may be missing: it is asked for when the aircraft is selected and arrives when it does."""

    operator: str = ""
    origin: tuple[str, str] | None = None  # code and city
    destination: tuple[str, str] | None = None
    type_name: str = ""  # maker and model
    photos: tuple[pygame.Surface, ...] = ()  # thumbnails, at most two, side by side
    credit: str = ""  # for all of them, on one line underneath
    departure: str = ""  # with no route: where its track starts, e.g. "From KPAO"


def _fit(font: pygame.font.Font, text: str, width: int) -> str:
    while len(text) > 4 and font.size(text)[0] > width:
        text = text[:-4].rstrip() + "..."
    return text


def draw_selected(
    surface: pygame.Surface,
    body: pygame.Rect,
    fonts: theme.Fonts,
    aircraft: Aircraft,
    distance_nm: float,
    bearing_deg: float,
    now: float,
    favorites: frozenset[str] = frozenset(),
    following: bool = False,
    buttons: "AircraftButtons | None" = None,
    distance_label: str = "DST",
    details: Details | None = None,
    pinned: bool = False,
    silent_for: float | None = None,
    can_pin: bool = True,
) -> list[pygame.Rect]:
    """buttons is where to draw the heart, the pin and the eye, if anywhere; following and
    pinned are the state of the last two; can_pin, whether the pin works (only on a
    favorite: it is dimmed otherwise). silent_for is how long it has gone without an
    update, given once that is long enough to say so. distance_label names what the
    distance is measured from: the center of the view as a rule ("DST"), or home while the
    aircraft is being followed. body ends where the pane's content must: what does not fit
    above that (the photos, last) is left out. Returns where each photo was drawn, so that a
    tap on one can open it."""
    details = details or Details()
    x, y = body.x + PADDING, body.y + 2
    width = body.width - 2 * PADDING
    if buttons is not None:
        # The buttons sit in the heading's row, so that the name below stays where it was:
        # the pane has no height to spare above the photos.
        favorite = is_favorite(aircraft, favorites)
        for rect, on, draw, lit in (
            (buttons.heart, favorite, draw_heart, theme.FAVORITE),
            (buttons.pin, pinned, draw_pin, theme.ACCENT),
            (buttons.eye, following, draw_eye, theme.ACCENT),
        ):
            face = button_face(rect)
            pygame.draw.rect(surface, theme.BUTTON_FILL, face, border_radius=6)
            pygame.draw.rect(surface, lit if on else theme.EDGE, face, width=1, border_radius=6)
            color = theme.legible(lit) if on else theme.TEXT_DIM
            if draw is draw_pin and not can_pin:
                color = theme.EDGE
            scale = BUTTON_ICON_SCALE * (1.05 if draw is draw_pin else 1.0)
            draw(surface, face.center, color, on, scale)
    color = (
        theme.AIRCRAFT_STALE
        if silent_for is not None
        else theme.aircraft_color(aircraft, favorites)
    )
    surface.blit(theme.text(fonts.large, aircraft.label, color), (x, y))
    y += 32
    if silent_for is not None:
        # Kept selected when it stops being heard (often on the ground at a small airfield,
        # below the receivers), but shown for what it is: where it was last heard of.
        banner = pygame.Rect(body.x + PADDING, y, width, 24)
        pygame.draw.rect(surface, theme.STATUS_STALE, banner, border_radius=4)
        words = theme.text(fonts.body, f"NOT UPDATING  {fmt.age(silent_for)}", theme.BANNER_TEXT)
        surface.blit(words, words.get_rect(center=banner.center))
        y += 28
    if aircraft.emergency:
        banner = pygame.Rect(body.x + PADDING, y, width, 24)
        pygame.draw.rect(surface, theme.EMERGENCY, banner, border_radius=4)
        label = theme.text(
            fonts.body, f"EMERGENCY {aircraft.squawk or ''}".strip(), theme.BANNER_TEXT
        )
        surface.blit(label, label.get_rect(center=banner.center))
        y += 28

    # Who is flying it, and from where to where.
    if details.operator:
        name = _fit(fonts.small, details.operator, width)
        surface.blit(theme.text(fonts.small, name, theme.TEXT_DIM), (x, y))
        y += 17
    if details.origin and details.destination:
        start = theme.text(fonts.title, details.origin[0], theme.TEXT)
        surface.blit(start, (x, y))
        ax = x + start.get_width() + 8
        mid = y + start.get_height() // 2
        pygame.draw.line(surface, theme.ACCENT, (ax, mid), (ax + 22, mid), 2)
        pygame.draw.polygon(
            surface, theme.ACCENT, [(ax + 28, mid), (ax + 20, mid - 5), (ax + 20, mid + 5)]
        )
        surface.blit(theme.text(fonts.title, details.destination[0], theme.TEXT), (ax + 36, y))
        y += 23
        cities = " - ".join(c for c in (details.origin[1], details.destination[1]) if c)
        if cities:
            surface.blit(
                theme.text(fonts.small, _fit(fonts.small, cities, width), theme.TEXT_DIM), (x, y)
            )
            y += 17
    elif details.departure:
        words = _fit(fonts.small, details.departure, width)
        surface.blit(theme.text(fonts.small, words, theme.TEXT_DIM), (x, y))
        y += 17
    y += 3

    # The figures, two to a row: a small label and its value.
    half = width // 2
    rows = [
        (("ALT", fmt.altitude(aircraft)), ("GS", fmt.speed(aircraft.ground_speed_kt))),
        (
            ("HDG", fmt.heading(aircraft.track_deg)),
            ("V/S", fmt.vertical_rate(aircraft.vertical_rate_fpm, short=True)),
        ),
        (("SQK", aircraft.squawk or fmt.MISSING), ("AGE", fmt.age(now - aircraft.seen_at))),
        ((distance_label, f"{fmt.distance(distance_nm)}  {fmt.heading(bearing_deg)}"),),
    ]
    for row in rows:
        for column, (label, value) in enumerate(row):
            cx = x + column * (half + 4)
            name = theme.text(fonts.small, label, theme.TEXT_DIM)
            surface.blit(name, (cx, y + 4))
            surface.blit(
                theme.text(fonts.body, value, theme.TEXT), (cx + name.get_width() + 5, y + 1)
            )
        y += FIELD_HEIGHT

    y += 3
    pygame.draw.line(surface, theme.EDGE, (x, y), (body.right - PADDING, y))
    y += 6
    kind = LABELS.get(classify(aircraft.type_code, aircraft.category))
    what = details.type_name or kind
    marks = "  ".join(m for m in (aircraft.type_code, aircraft.registration) if m)
    for line in (what, marks):
        if line and y + 18 <= body.bottom:
            surface.blit(
                theme.text(fonts.small, _fit(fonts.small, line, width), theme.TEXT_DIM), (x, y)
            )
            y += 18

    # The photos, if there are any and there is room for them and for who took them
    # underneath. Two of the usual shape fit side by side.
    drawn: list[pygame.Rect] = []
    photos = details.photos
    if photos and y + 3 + photos[0].get_height() + 15 <= body.bottom:
        y += 3
        px = x
        for photo in photos:
            if px + photo.get_width() > body.right - PADDING + 1:
                break
            placed = photo.get_rect(topleft=(px, y))
            surface.blit(photo, placed)
            pygame.draw.rect(surface, theme.EDGE, placed, width=1)
            drawn.append(placed)
            px = placed.right + PHOTO_GAP
        y += photos[0].get_height() + 1
        credit = _fit(fonts.small, f"Photo: {details.credit}", width)
        surface.blit(theme.text(fonts.small, credit, theme.TEXT_DIM), (x, y))
    return drawn


def draw_nearest(
    surface: pygame.Surface,
    body: pygame.Rect,
    fonts: theme.Fonts,
    rows: list[tuple[Aircraft, float]],
    favorites: frozenset[str] = frozenset(),
) -> list[tuple[pygame.Rect, str]]:
    """List the nearest aircraft. Returns each row's tap area and aircraft hex."""
    if not rows:
        surface.blit(
            theme.text(fonts.body, "No aircraft in range", theme.TEXT_DIM),
            (body.x + PADDING, body.y + 8),
        )
        return []
    targets = []
    y = body.y
    for aircraft, dist in rows[: body.height // ROW_HEIGHT]:
        row = pygame.Rect(body.x, y, body.width, ROW_HEIGHT)
        pygame.draw.line(surface, theme.EDGE, (row.x + PADDING, row.top), (row.right, row.top))
        surface.blit(
            theme.text(fonts.title, aircraft.label, theme.aircraft_color(aircraft, favorites)),
            (row.x + PADDING, row.y + 6),
        )
        detail = f"{fmt.distance(dist)}   {fmt.altitude(aircraft)}"
        surface.blit(theme.text(fonts.small, detail, theme.TEXT_DIM), (row.x + PADDING, row.y + 28))
        if aircraft.type_code:
            kind = theme.text(fonts.small, aircraft.type_code, theme.TEXT_DIM)
            surface.blit(kind, kind.get_rect(topright=(row.right - PADDING, row.y + 10)))
        targets.append((row, aircraft.hex))
        y += ROW_HEIGHT
    return targets


def draw_lines(
    surface: pygame.Surface, body: pygame.Rect, fonts: theme.Fonts, lines: list[str]
) -> None:
    y = body.y + 2
    for line in lines:
        surface.blit(theme.text(fonts.small, line, theme.TEXT), (body.x + PADDING, y))
        y += fonts.small.get_linesize() + 3


def _wrap(font: pygame.font.Font, text: str, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.size(trial)[0] > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return [*lines, current] if current else lines


def corner_rect(rect: pygame.Rect) -> pygame.Rect:
    """The panel's top-right corner, where its one button goes: the heart for an airport, the
    eye for an aircraft."""
    return pygame.Rect(
        rect.right - theme.MIN_TOUCH_PX - 2, rect.top, theme.MIN_TOUCH_PX, theme.MIN_TOUCH_PX
    )


heart_rect = corner_rect
eye_rect = corner_rect


@dataclass(frozen=True)
class AircraftButtons:
    """The selected-aircraft pane's buttons, along its top: favorite, keep the track, follow."""

    heart: pygame.Rect
    pin: pygame.Rect
    eye: pygame.Rect


def aircraft_buttons(rect: pygame.Rect) -> AircraftButtons:
    """Touch targets: the heading row's width, padding to padding, shared three ways, each
    48 px tall."""
    left, span = rect.left + PADDING, rect.width - 2 * PADDING
    edges = [left + round(span * i / 3) for i in range(4)]
    targets = [
        pygame.Rect(edges[i], rect.top, edges[i + 1] - edges[i], theme.MIN_TOUCH_PX)
        for i in range(3)
    ]
    return AircraftButtons(*targets)


def button_face(target: pygame.Rect) -> pygame.Rect:
    """The visible button within a touch target: its width less a gap, in the heading row."""
    face = pygame.Rect(0, 0, target.width - BUTTON_GAP, BUTTON_FACE_HEIGHT)
    face.center = (target.centerx, target.top + HEADING_HEIGHT // 2 + 1)
    return face


def draw_airport(
    surface: pygame.Surface,
    rect: pygame.Rect,
    body: pygame.Rect,
    fonts: theme.Fonts,
    airport: Airport,
    distance_nm: float,
    bearing_deg: float,
    favorite: bool,
    is_home: bool,
    report: metar_module.Metar | str,
    now_unix: float,
    confirm_remove: bool = False,
) -> None:
    """Details of a selected airport: where it is, its runways, and its current weather.
    confirm_remove is set after a first tap on the home airport's heart: the pane then asks
    for a second before home is given up."""
    width = body.width - 2 * PADDING
    x, y = body.x + PADDING, body.y + 2
    color = theme.FAVORITE_AIRPORT if favorite else theme.TEXT
    surface.blit(theme.text(fonts.large, airport.ident, color), (x, y))
    heart = heart_rect(rect)
    if confirm_remove:
        pygame.draw.rect(surface, theme.STATUS_DOWN, heart.inflate(-6, -6), border_radius=6)
        draw_heart(surface, heart.center, theme.TEXT, True)
    else:
        heart_color = theme.FAVORITE_AIRPORT if favorite else theme.TEXT_DIM
        draw_heart(surface, heart.center, heart_color, favorite)
    y += 32
    for line in _wrap(fonts.small, airport.name, width)[:2]:
        surface.blit(theme.text(fonts.small, line, theme.TEXT), (x, y))
        y += 15
    if confirm_remove:
        for line in ("This is home. Tap the heart", "again to remove it."):
            surface.blit(theme.text(fonts.small, line, theme.STATUS_STALE), (x, y))
            y += 15
        y += 5
    else:
        place = airport.place + ("  (home)" if is_home else "")
        surface.blit(theme.text(fonts.small, place, theme.TEXT_DIM), (x, y))
        y += 20

    facts = [("DST", f"{fmt.distance(distance_nm)}  {fmt.heading(bearing_deg)}")]
    if airport.elevation_ft is not None:
        facts.append(("ELEV", f"{airport.elevation_ft:,} ft"))
    runways = runways_module.at(airport.ident)
    for i, runway in enumerate(runways[:2]):
        length = f"  {runway.length_ft:,} ft" if runway.length_ft else ""
        facts.append(("RWY" if i == 0 else "", f"{runway.le_ident}/{runway.he_ident}{length}"))
    for label, value in facts:
        surface.blit(theme.text(fonts.small, label, theme.TEXT_DIM), (x, y + 2))
        surface.blit(theme.text(fonts.body, value, theme.TEXT), (x + 40, y))
        y += 21
    if len(runways) > 2:
        more = theme.text(fonts.small, f"and {len(runways) - 2} more", theme.TEXT_DIM)
        surface.blit(more, (x + 40, y))
        y += 16

    y += 3
    pygame.draw.line(surface, theme.EDGE, (x, y), (body.right - PADDING, y))
    y += 6
    if isinstance(report, str):
        message = {
            metar_module.LOADING: "Getting the weather...",
            metar_module.NONE: "No weather report here.",
            metar_module.FAILED: "Weather unavailable.",
        }[report]
        surface.blit(theme.text(fonts.body, message, theme.TEXT_DIM), (x, y))
        return

    heading = theme.text(fonts.body, "METAR", theme.ACCENT)
    surface.blit(heading, (x, y))
    if report.category:
        badge = theme.text(fonts.body, report.category, theme.TEXT)
        box = badge.get_rect(topleft=(x + 62, y)).inflate(12, 2)
        pygame.draw.rect(surface, theme.FLIGHT_CATEGORY[report.category], box, border_radius=4)
        surface.blit(badge, badge.get_rect(center=box.center))
    if report.observed_at is not None:
        minutes = max(0, round((now_unix - report.observed_at) / 60))
        age = theme.text(fonts.small, f"{minutes} min", theme.TEXT_DIM)
        surface.blit(age, age.get_rect(topright=(body.right - PADDING, y + 3)))
    y += 23

    sky = "Clear" if report.sky in ("CLR", "SKC", "CAVOK", "NSC", "NCD") else (report.sky or "---")
    ceiling = f"{report.ceiling_ft:,} ft" if report.ceiling_ft is not None else sky
    temperature = "---"
    if report.temp_c is not None:
        temperature = f"{round(report.temp_c)}°C"
        if report.dewpoint_c is not None:
            temperature += f" / {round(report.dewpoint_c)}°"
    weather = [
        ("WIND", report.wind),
        ("VIS", f"{report.visibility} SM" if report.visibility else "---"),
        ("CIG", ceiling),
        ("TEMP", temperature),
        ("ALT", report.altimeter),
    ]
    for label, value in weather:
        surface.blit(theme.text(fonts.small, label, theme.TEXT_DIM), (x, y + 2))
        surface.blit(theme.text(fonts.body, value, theme.TEXT), (x + 44, y))
        y += 20
    # The report itself, as much of it as there is room for above the button.
    limit = rect.bottom - theme.MIN_TOUCH_PX - 2 * PADDING
    for line in _wrap(fonts.small, report.raw, width):
        if y + 14 > limit:
            break
        surface.blit(theme.text(fonts.small, line, theme.TEXT_DIM), (x, y + 2))
        y += 14


def draw_candidates(
    surface: pygame.Surface,
    body: pygame.Rect,
    fonts: theme.Fonts,
    candidates: list[Aircraft | Airport],
    favorites: frozenset[str] = frozenset(),
    favorite_airports: frozenset[str] = frozenset(),
) -> list[tuple[pygame.Rect, Aircraft | Airport]]:
    """What was under a tap, when it was more than one thing: a row for each to choose from.
    body is the room there is; what does not fit is counted in a last line, not left out
    unsaid. Returns each row's tap area and what it stands for."""
    targets = []
    y = body.y
    fits = body.height // ROW_HEIGHT
    if len(candidates) > fits:
        fits = max((body.height - 22) // ROW_HEIGHT, 1)
        more = theme.text(
            fonts.small, f"+{len(candidates) - fits} more. Zoom in to separate.", theme.TEXT_DIM
        )
        surface.blit(more, (body.x + PADDING, body.y + fits * ROW_HEIGHT + 4))
    for candidate in candidates[:fits]:
        row = pygame.Rect(body.x, y, body.width, ROW_HEIGHT)
        pygame.draw.line(surface, theme.EDGE, (row.x + PADDING, row.top), (row.right, row.top))
        if isinstance(candidate, Airport):
            favorite = candidate.ident in favorite_airports
            color = theme.FAVORITE_AIRPORT if favorite else theme.AIRPORT
            title, note, detail = candidate.ident, "Airport", candidate.name
        else:
            color = theme.aircraft_color(candidate, favorites)
            title, note = candidate.label, candidate.type_code or ""
            detail = fmt.altitude(candidate)
        surface.blit(theme.text(fonts.title, title, color), (row.x + PADDING, row.y + 6))
        if note:
            tag = theme.text(fonts.small, note, theme.TEXT_DIM)
            surface.blit(tag, tag.get_rect(topright=(row.right - PADDING, row.y + 10)))
        room = row.width - 2 * PADDING
        while len(detail) > 4 and fonts.small.size(detail)[0] > room:
            detail = detail[:-4].rstrip() + "..."
        surface.blit(theme.text(fonts.small, detail, theme.TEXT_DIM), (row.x + PADDING, row.y + 28))
        targets.append((row, candidate))
        y += ROW_HEIGHT
    return targets

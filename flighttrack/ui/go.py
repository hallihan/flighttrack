"""The GO page: type an airport or a flight, pick from suggestions as you type, and go there.

On the left, the text being typed with suggestions under it; on the right, from the top of
the screen down to the keyboard, the favorites in two columns, airports on the left and
aircraft on the right, under "Current Location" across both, all scrolling together. Tapping
a suggestion or a favorite only chooses it; the keyboard's GO key then goes there and changes
nothing else. (Making a favorite, or home, is done with the hearts and the settings page.)
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import hypot

import pygame

from flighttrack import airports
from flighttrack import favorites as favorites_module
from flighttrack.airports import Airport
from flighttrack.feeds.base import Aircraft
from flighttrack.geo import distance_nm
from flighttrack.location import Location
from flighttrack.ui import format as fmt
from flighttrack.ui import theme
from flighttrack.ui.keyboard import DONE, KEY_HEIGHT, Keyboard

SUGGESTIONS = 3
ROW_PX = 60
FAVORITE_ROW_PX = 48
FAVORITE_GAP_PX = 6  # between the two columns of favorites
SCROLL_GUTTER_PX = 8  # at the right of the favorites, for their scroll bar
TAP_SLOP_PX = 14


@dataclass(frozen=True)
class Everywhere:
    """A suggestion that stands for "look this flight up among all flights, not just those
    on the map"."""

    query: str


@dataclass(frozen=True)
class FavoriteAircraft:
    """One of the favorite aircraft, as the user wrote it: a registration, callsign or hex."""

    entry: str


Suggestion = Airport | Aircraft | Everywhere | Location | FavoriteAircraft


def looks_like_a_flight(text: str) -> bool:
    """Whether what was typed could be a callsign, registration or hex code worth looking up:
    a few letters and digits together, such as BAW117, N123AB or 4CA1D3."""
    plain = text.upper().replace("-", "").replace(" ", "")
    return 3 <= len(plain) <= 8 and plain.isalnum() and any(ch.isdigit() for ch in plain)


def match_flights(text: str, flights: list[Aircraft]) -> tuple[list[Aircraft], list[Aircraft]]:
    """Aircraft on the map whose callsign, registration or hex is what was typed (exactly),
    and those that merely start with it."""
    query = text.upper().replace(" ", "")
    if not query:
        return [], []
    exact, partial = [], []
    for aircraft in flights:
        names = [n.upper() for n in (aircraft.callsign, aircraft.registration, aircraft.hex) if n]
        loose = [n.replace("-", "") for n in names]
        if query in names or query in loose:
            exact.append(aircraft)
        elif any(n.startswith(query) for n in names + loose):
            partial.append(aircraft)
    return exact, sorted(partial, key=lambda a: a.label)


def _wrap(font: pygame.font.Font, text: str, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and font.size(f"{lines[-1]} {word}")[0] <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


class GoScreen:
    def __init__(
        self,
        size: tuple[int, int],
        close: Callable[[], None],
        near: tuple[float, float],
        favorites: Callable[[], list[Airport]],
        home: Callable[[], str | None],
        go: Callable[[Airport, bool], None],
        here: Callable[[], Location | None] | None = None,
        go_here: Callable[[bool], None] | None = None,
        flights: Callable[[], list[Aircraft]] | None = None,
        go_flight: Callable[[Aircraft, bool], None] | None = None,
        find_flight: Callable[[str], Aircraft | None] | None = None,
        favorite_aircraft: Callable[[], list[str]] | None = None,
        done_label: str = "GO",
        scope: str = "all",
        add_aircraft: Callable[[str], None] | None = None,
    ) -> None:
        """near is where to measure suggestions' distance from. favorites returns the favorite
        airports and home the ident of the home one. go(airport, make_home) is called for GO,
        with make_home False: making home is done on the settings pages.

        here returns where the display's internet address says it is (None until known).
        When given, "Current Location" is always the first of the favorites, and
        go_here(make_home) is called for it.

        flights returns the aircraft on the map now, to be searched as well; go_flight is
        called for one of them. find_flight looks a flight up everywhere (it may block: it is
        run on a worker thread) and is offered when what was typed could be one.
        favorite_aircraft returns the favorite aircraft as written, for the right-hand column:
        one on the map is gone to at once, any other is looked up like a typed flight.
        done_label names the keyboard's key: ADD when the page is opened to add a favorite.
        scope limits the search to "airports" or "aircraft" ("all" is the GO page itself, the
        only one that shows the favorites). add_aircraft, for adding a favorite aircraft, is
        given what to add: the registration of one chosen from the suggestions, or what was
        typed."""
        self.size = size
        self._close = close
        self.closed = False
        self.near = near
        self._favorites = favorites
        self._home = home
        self._go = go
        self._here = here
        self._go_here = go_here
        self._flights = flights or (lambda: [])
        self._go_flight = go_flight
        self._find_flight = find_flight
        self._favorite_aircraft = favorite_aircraft or (lambda: [])
        self.scope = scope
        self._add_aircraft = add_aircraft
        self.keyboard = Keyboard(size, "", "", lambda _: self.go(False), self.close)
        self.keyboard.done_label = done_label  # on this page that is what the key does
        self.keyboard.cancel_rect = None  # the cross at the top right is this page's way out
        self.results: tuple[Suggestion, ...] = ()
        self.chosen: Suggestion | None = None
        self.scroll = 0
        self.searching: str | None = None  # the flight being looked up everywhere, if any
        self.message = ""  # how the last such lookup went, when it found nothing
        self.not_found: Suggestion | None = None  # the choice the last lookup found nothing for
        self._lookup: tuple[str, Aircraft | None | Exception, bool] | None = None
        self._press: tuple[int, int] | None = None
        self._scrolling = False
        self._searched: str | None = None

        width, height = size
        top = height - 4 * KEY_HEIGHT  # where the keyboard starts
        # What is typed, at the top left, and a way out that needs no choice made, top right.
        # GO is the keyboard's key.
        self.close_button = pygame.Rect(width - 56, 6, 48, 48)
        self.box = pygame.Rect(8, 6, width // 2, 48)
        column = self.box.width
        self.suggestion_area = pygame.Rect(8, 60, column, top - 64)
        left = self.box.right + 8
        self.favorite_area = pygame.Rect(left, 6, self.close_button.left - 8 - left, top - 10)

    def close(self) -> None:
        """Leave the page. Nothing is selected and nothing changes unless GO did it first."""
        if not self.closed:
            self.closed = True
            self._close()

    # ---- state ----------------------------------------------------------------------------

    def _update_results(self) -> None:
        text = self.keyboard.text
        if text == self._searched:
            return
        self._searched = text
        self.message = ""
        self.not_found = None
        query = text.upper().strip()
        found_airports = (
            [] if self.scope == "aircraft" else airports.search(text, self.near, SUGGESTIONS)
        )
        exact_flights, partial_flights = match_flights(text, self._flights())
        exact_airports = [a for a in found_airports if query in (a.ident, a.iata)]
        other_airports = [a for a in found_airports if a not in exact_airports]
        # What was typed in full comes first, flights ahead of airports; then near misses.
        ranked: list[Suggestion] = [
            *exact_flights,
            *exact_airports,
            *partial_flights[:2],
            *other_airports,
        ]
        shown = ranked[:SUGGESTIONS]
        offer = self._find_flight is not None and not exact_flights and looks_like_a_flight(text)
        if self._add_aircraft is not None:
            # What was typed, to add as it is, if it could be a registration, callsign or hex.
            entries, rejected = favorites_module.parse(text)
            offer = len(entries) == 1 and not rejected and not exact_flights
            query = entries[0] if offer else query
        if offer:
            # Always in sight when it applies: the flight wanted may be nowhere near the map.
            shown = [*shown[: SUGGESTIONS - 1], Everywhere(query)]
        self.results = tuple(shown)
        # What GO will act on: the best match, until the user picks another.
        self.chosen = self.results[0] if self.results else None

    def suggestion_rects(self) -> list[tuple[pygame.Rect, Suggestion]]:
        area = self.suggestion_area
        return [
            (pygame.Rect(area.left, area.top + i * ROW_PX, area.width, ROW_PX), suggestion)
            for i, suggestion in enumerate(self.results)
        ]

    @property
    def can_go(self) -> bool:
        """Whether GO has something to do: a choice, not being looked up, and not one just
        looked up and not found. The key is grayed out otherwise."""
        chosen = self.chosen
        return chosen is not None and self.searching is None and chosen != self.not_found

    @property
    def go_key(self) -> pygame.Rect:
        """The keyboard's GO key: the one way to go."""
        return next(rect for rect, key in self.keyboard.keys if key == DONE)

    def here(self) -> Location | None:
        return self._here() if self._here is not None else None

    def _favorite_rows(self) -> int:
        here = 1 if self._here is not None else 0
        return here + max(len(self._favorites()), len(self._favorite_aircraft()))

    def favorite_rects(
        self,
    ) -> list[tuple[pygame.Rect, Airport | Location | FavoriteAircraft | None]]:
        """Where each favorite is: the current location across the top (None while it is not
        known, which still gets its row), then airports on the left and aircraft on the
        right, row by row. Only on the GO page itself, not when adding a favorite."""
        if self.scope != "all":
            return []
        area = self.favorite_area
        width = area.width - SCROLL_GUTTER_PX
        half = (width - FAVORITE_GAP_PX) // 2
        right = area.left + width - half
        cells: list[tuple[pygame.Rect, Airport | Location | FavoriteAircraft | None]] = []
        row = 0

        def at(x: int, width: int) -> pygame.Rect:
            return pygame.Rect(
                x, area.top + row * FAVORITE_ROW_PX - self.scroll, width, FAVORITE_ROW_PX
            )

        if self._here is not None:
            cells.append((at(area.left, width), self.here()))
            row += 1
        airports_ = list(self._favorites())
        planes = [FavoriteAircraft(e) for e in self._favorite_aircraft()]
        for i in range(max(len(airports_), len(planes))):
            if i < len(airports_):
                cells.append((at(area.left, half), airports_[i]))
            if i < len(planes):
                cells.append((at(right, half), planes[i]))
            row += 1
        return cells

    def _clamp_scroll(self) -> None:
        content = self._favorite_rows() * FAVORITE_ROW_PX if self.scope == "all" else 0
        self.scroll = max(0, min(self.scroll, content - self.favorite_area.height))

    def go(self, add_to_favorites: bool) -> None:
        self._update_results()
        chosen = self.chosen
        if chosen is None or self.searching is not None or self.closed:
            return
        if chosen == self.not_found:
            return  # looked for, and not found: GO is grayed out until something changes
        if self._add_aircraft is not None:
            if isinstance(chosen, Aircraft):
                self._add_aircraft(chosen.registration or chosen.label)
            elif isinstance(chosen, Everywhere):
                self._add_aircraft(chosen.query)
            self.close()
            return
        if isinstance(chosen, Everywhere):
            self._look_up(chosen.query, add_to_favorites)
            return
        if isinstance(chosen, Location):
            if self._go_here is not None:
                self._go_here(add_to_favorites)
        elif isinstance(chosen, FavoriteAircraft):
            # On the map, gone to at once; anywhere else, looked up like a typed flight.
            exact, _ = match_flights(chosen.entry, self._flights())
            if not exact:
                self._look_up(chosen.entry, add_to_favorites)
                return
            if self._go_flight is not None:
                self._go_flight(exact[0], add_to_favorites)
        elif isinstance(chosen, Aircraft):
            if self._go_flight is not None:
                self._go_flight(chosen, add_to_favorites)
        else:
            self._go(chosen, add_to_favorites)
        self.close()

    def _look_up(self, query: str, add_to_favorites: bool) -> None:
        """Ask for the flight everywhere, in the background. The page stays up meanwhile;
        _finish_lookup, on the main thread, acts on the answer."""
        self.searching, self.message = query, ""

        def work() -> None:
            try:
                answer: Aircraft | None | Exception = self._find_flight(query)
            except Exception as exc:  # network trouble: said on the page, not raised here
                answer = exc
            self._lookup = (query, answer, add_to_favorites)

        threading.Thread(target=work, name="find-flight", daemon=True).start()

    def _finish_lookup(self) -> None:
        if self._lookup is None:
            return
        (query, answer, add_to_favorites), self._lookup = self._lookup, None
        self.searching = None
        if isinstance(answer, Aircraft):
            if self._go_flight is not None:
                self._go_flight(answer, add_to_favorites)
            self.close()
        elif answer is None:
            self.not_found = self.chosen
            self.message = f"No sign of {query} now or in the last few days."
        else:
            self.message = "Could not search: no connection?"

    # ---- input ----------------------------------------------------------------------------

    def tap(self, pos: tuple[int, int]) -> None:
        if self.close_button.collidepoint(pos):
            self.close()
        elif self.box.collidepoint(pos):
            self.keyboard.tap_field(pos)
        elif self.suggestion_area.collidepoint(pos):
            for rect, suggestion in self.suggestion_rects():
                if rect.collidepoint(pos):
                    self.chosen = suggestion
        elif self.favorite_area.collidepoint(pos):
            for rect, airport in self.favorite_rects():
                if rect.collidepoint(pos) and self.searching is None and airport is not None:
                    # Chosen, like a suggestion; nothing happens until a button is tapped.
                    self.chosen = airport

    def handle_event(self, event: pygame.event.Event) -> None:
        self._finish_lookup()
        self.keyboard.done_enabled = self.can_go
        keys_top = self.size[1] - 4 * KEY_HEIGHT
        pointer = event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION)
        if not pointer or (event.pos[1] >= keys_top and self._press is None):
            self.keyboard.handle_event(event)
            self._update_results()
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._press, self._scrolling = event.pos, False
        elif event.type == pygame.MOUSEMOTION and event.buttons[0] and self._press is not None:
            dy = event.pos[1] - self._press[1]
            started_in_list = self.favorite_area.collidepoint(self._press)
            if self._scrolling:
                self.scroll -= dy
                self._press = event.pos
                self._clamp_scroll()
            elif started_in_list and abs(dy) > TAP_SLOP_PX:
                self._scrolling = True
                self._press = event.pos
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._press is not None:
            press, self._press = self._press, None
            moved = hypot(event.pos[0] - press[0], event.pos[1] - press[1])
            if not self._scrolling and moved <= TAP_SLOP_PX:
                self.tap(event.pos)
            self._scrolling = False

    # ---- drawing --------------------------------------------------------------------------

    def _button(self, surface, fonts, rect, label, enabled, primary, edge=None) -> None:
        edge = edge or theme.EDGE
        if not enabled:
            fill, color = theme.BUTTON_DISABLED, theme.TEXT_DIM
        elif primary:
            fill, color = theme.TOGGLE_ON, theme.ON_ACCENT
        else:
            fill, color = theme.BUTTON_FILL, theme.TEXT
        pygame.draw.rect(surface, fill, rect, border_radius=8)
        pygame.draw.rect(surface, edge if enabled else theme.EDGE, rect, width=1, border_radius=8)
        text = theme.text(fonts.title, label, color)
        surface.blit(text, text.get_rect(center=rect.center))

    @staticmethod
    def _fit(font: pygame.font.Font, text: str, width: int) -> str:
        while len(text) > 4 and font.size(text)[0] > width:
            text = text[:-4].rstrip() + "..."
        return text

    def _describe(self, suggestion: Suggestion) -> tuple[str, str, str]:
        """Title, a note for the right-hand end, and a line of detail for one suggestion."""
        if isinstance(suggestion, Location):  # never among the suggestions; for completeness
            return "Current Location", "", suggestion.place
        if isinstance(suggestion, Everywhere):
            if self._add_aircraft is not None:
                return f"Add {suggestion.query}", "", "As typed: registration, callsign or hex"
            return f"Find {suggestion.query}", "FLIGHT", "Among all flights now, or where last seen"
        if isinstance(suggestion, Aircraft):
            miles = distance_nm(*self.near, suggestion.lat, suggestion.lon)
            kind = suggestion.type_code or "Flight"
            ago = time.monotonic() - suggestion.seen_at
            when = "on the map now" if ago < 60 else f"seen {fmt.age(ago)} ago"
            name = suggestion.label
            if self._add_aircraft is not None and suggestion.registration:
                name = suggestion.registration  # what will be added
            return name, f"{miles:,.0f} NM", f"{kind}  {fmt.altitude(suggestion)}  {when}"
        code = suggestion.ident + (f" / {suggestion.iata}" if suggestion.iata else "")
        miles = distance_nm(*self.near, suggestion.lat, suggestion.lon)
        return code, f"{miles:,.0f} NM", f"{suggestion.name}  {suggestion.place}"

    def draw(self, surface: pygame.Surface, fonts: theme.Fonts, now: float) -> None:
        self._finish_lookup()
        self._update_results()
        self._clamp_scroll()
        surface.fill(theme.BACKGROUND)

        # What has been typed.
        pygame.draw.rect(surface, theme.PANEL_FILL, self.box, border_radius=8)
        pygame.draw.rect(surface, theme.ACCENT, self.box, width=2, border_radius=8)
        typed = self.keyboard.text
        self.keyboard.draw_field(
            surface, self.box, fonts.large, now, (fonts.title, "Airport or flight")
        )
        # Close: a cross, drawn rather than typed so it is unmistakable at any font.
        self._button(surface, fonts, self.close_button, "", True, False)
        cx, cy = self.close_button.center
        for dx in (-9, 9):
            pygame.draw.line(surface, theme.TEXT, (cx - dx, cy - 9), (cx + dx, cy + 9), width=3)

        # Suggestions, or what is going on in their place.
        area = self.suggestion_area
        if self.searching is not None:
            words = theme.text(fonts.body, f"Looking for {self.searching}...", theme.TEXT)
            surface.blit(words, (area.left + 6, area.top + 10))
        else:
            for rect, suggestion in self.suggestion_rects():
                face = rect.inflate(0, -6)
                chosen = suggestion == self.chosen
                fill = theme.BUTTON_PRESSED if chosen else theme.PANEL_FILL
                pygame.draw.rect(surface, fill, face, border_radius=6)
                edge = theme.ACCENT if chosen else theme.EDGE
                pygame.draw.rect(surface, edge, face, width=1, border_radius=6)
                title, note, detail = self._describe(suggestion)
                is_flight = not isinstance(suggestion, Airport)
                # On the lit row everything is in the plain text color: green and gray do
                # not stand out against it.
                color = theme.TEXT if chosen else theme.AIRCRAFT_MID if is_flight else theme.TEXT
                dim = theme.TEXT if chosen else theme.TEXT_DIM
                surface.blit(theme.text(fonts.title, title, color), (face.left + 10, face.top + 6))
                tag = theme.text(fonts.small, note, dim)
                surface.blit(tag, tag.get_rect(topright=(face.right - 10, face.top + 10)))
                detail = self._fit(fonts.small, detail, face.width - 20)
                surface.blit(theme.text(fonts.small, detail, dim), (face.left + 10, face.top + 31))
            if self.message:
                # Under whatever rows there are, wrapped to the column so none of it runs
                # under the favorites.
                lines = _wrap(fonts.body, self.message, area.width - 12)
                height = fonts.body.get_linesize()
                y = area.top + len(self.results) * ROW_PX + 4
                y = min(y, area.bottom - height * len(lines))
                for line in lines:
                    words = theme.text(fonts.body, line, theme.STATUS_STALE)
                    surface.blit(words, (area.left + 6, y))
                    y += height
            elif typed and not self.results:
                none = theme.text(fonts.body, "No airport or flight matches that.", theme.TEXT_DIM)
                surface.blit(none, (area.left + 6, area.top + 10))
            elif not typed:
                lines = {
                    "airports": ("Type an airport's code, name or city,", "then press ADD."),
                    "aircraft": (
                        "Type a registration, callsign or hex code,",
                        "or part of one to pick from what is",
                        "on the map. Then press ADD.",
                    ),
                }.get(
                    self.scope,
                    (
                        "Type an airport's code, name or city,",
                        "or a flight's callsign or registration.",
                        "Or choose a favorite. Then press GO.",
                    ),
                )
                for i, line in enumerate(lines):
                    tip = theme.text(fonts.body, line, theme.TEXT_DIM)
                    surface.blit(tip, (area.left + 6, area.top + 10 + i * 26))

        # Favorites: short codes only, airports left, aircraft right, current location across.
        area = self.favorite_area
        surface.set_clip(area)
        home = self._home()
        rows = self.favorite_rects()
        for rect, item in rows:
            if rect.bottom < area.top or rect.top > area.bottom:
                continue
            face = rect.inflate(0, -6)
            chosen = item is not None and item == self.chosen
            fill = theme.BUTTON_PRESSED if chosen else theme.PANEL_FILL
            pygame.draw.rect(surface, fill, face, border_radius=6)
            edge = theme.ACCENT if chosen else theme.EDGE
            pygame.draw.rect(surface, edge, face, width=1, border_radius=6)
            if isinstance(item, Airport):
                title, color, is_home = item.ident, theme.TEXT, item.ident == home
            elif isinstance(item, FavoriteAircraft):
                title, color, is_home = item.entry, theme.FAVORITE, False
            else:  # the current location, None while it is not known
                where = f" ({item.place})" if item is not None and item.place else ""
                title = "Current Location" + (where if item is not None else " (not known yet)")
                color = theme.TEXT if item is not None else theme.TEXT_DIM
                is_home = item is not None and home is None
            if chosen:
                color = theme.TEXT  # against the highlight, only the plain color reads
            room = face.width - (64 if is_home else 20)
            words = theme.text(fonts.title, self._fit(fonts.title, title, room), color)
            surface.blit(words, words.get_rect(midleft=(face.left + 10, face.centery)))
            if is_home:
                tag = theme.text(fonts.small, "HOME", theme.TEXT if chosen else theme.ACCENT)
                surface.blit(tag, tag.get_rect(midright=(face.right - 10, face.centery)))
        surface.set_clip(None)
        total = self._favorite_rows() * FAVORITE_ROW_PX
        if not rows and self.scope == "all":
            empty = theme.text(fonts.small, "No favorites yet.", theme.TEXT_DIM)
            surface.blit(empty, (area.left, area.top + 8))
        elif total > area.height:
            # A scroll bar, so it is plain there is more than fits.
            bar_height = max(24, round(area.height * area.height / total))
            travel = area.height - bar_height
            offset = round(travel * self.scroll / (total - area.height))
            pygame.draw.rect(
                surface,
                theme.EDGE,
                (area.right - 4, area.top + offset, 4, bar_height),
                border_radius=2,
            )

        self.keyboard.done_enabled = self.can_go
        self.keyboard.draw_keys(surface, fonts, now)

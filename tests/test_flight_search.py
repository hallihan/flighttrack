import json
import time

import pygame
import pytest
import requests

from flighttrack import airports
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.feeds.base import Aircraft
from flighttrack.feeds.http import find_flight, flight_lookups
from flighttrack.ui import keyboard as kb
from flighttrack.ui import theme
from flighttrack.ui.go import Everywhere, GoScreen, looks_like_a_flight, match_flights

SIZE = (800, 480)
EGLL = airports.find("EGLL")


def plane(hex_code, callsign=None, registration=None, lat=51.5, lon=-0.4, **fields):
    return Aircraft(
        hex_code, lat, lon, time.monotonic(), callsign=callsign, registration=registration, **fields
    )


BAW = plane("400abc", "BAW117", "G-XWBA", altitude_ft=12000, type_code="A35K")
BAW2 = plane("400abd", "BAW12", "G-STBA", altitude_ft=30000)
CESSNA = plane("a1b2c3", None, "N123AB", altitude_ft=2500, type_code="C172")
ON_MAP = [BAW, BAW2, CESSNA]


def test_flights_on_the_map_are_matched_by_callsign_registration_or_hex():
    assert match_flights("baw117", ON_MAP) == ([BAW], [])
    exact, partial = match_flights("BAW1", ON_MAP)
    assert exact == [] and [a.callsign for a in partial] == ["BAW117", "BAW12"]
    assert match_flights("n123ab", ON_MAP) == ([CESSNA], [])
    assert match_flights("GXWBA", ON_MAP) == ([BAW], []), "a registration typed without its dash"
    assert match_flights("A1B2C3", ON_MAP) == ([CESSNA], [])
    assert match_flights("", ON_MAP) == ([], []) and match_flights("ZZZ", ON_MAP) == ([], [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [("BAW117", True), ("n123ab", True), ("4CA1D3", True), ("G-XWBA1", True), ("LHR", False),
     ("london", False), ("BA", False), ("san francisco 1", False), ("", False)],
)  # fmt: skip
def test_what_looks_like_a_flight(text, expected):
    assert looks_like_a_flight(text) is expected


def test_lookup_urls_try_callsign_then_registration_and_hex_first_when_it_could_be_one():
    assert flight_lookups("baw117") == [
        "https://opendata.adsb.fi/api/v2/callsign/BAW117",
        "https://opendata.adsb.fi/api/v2/registration/BAW117",
    ]
    assert flight_lookups("g-xwba")[1].endswith("/registration/G-XWBA")
    assert flight_lookups("4ca1d3")[0].endswith("/hex/4ca1d3")
    assert flight_lookups("ab") == [], "too short to be worth asking"
    assert flight_lookups("BAW117/../../x")[0].endswith("/callsign/BAW117X"), (
        "only letters and digits"
    )


class LookupSession:
    def __init__(self, answers):
        self.answers = list(answers)
        self.urls = []
        self.headers = {}

    def get(self, url, timeout):
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"ac": answer}).encode()
        return response


RECORD = {
    "hex": "400abc",
    "flight": "BAW117  ",
    "r": "G-XWBA",
    "lat": 40.1,
    "lon": -30.2,
    "alt_baro": 38000,
}


def test_find_flight_tries_each_kind_of_name_and_spaces_its_requests():
    pauses = []
    session = LookupSession([[], [RECORD]])
    found = find_flight("g-xwba", session, clock=lambda: 50.0, pause=pauses.append)
    assert found.hex == "400abc" and (found.lat, found.lon) == (40.1, -30.2)
    assert len(session.urls) == 2 and len(pauses) == 1 and pauses[0] >= 1.0

    assert find_flight("BAW117", LookupSession([[], []]), pause=pauses.append) is None
    assert find_flight("ab", LookupSession([])) is None
    with pytest.raises(requests.ConnectionError):
        find_flight("BAW117", LookupSession([requests.ConnectionError("down")]))


def _tap(target, pos):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        target.handle_event(pygame.event.Event(kind, pos=pos, button=1))


def _type(page, text):
    for char in text:
        board = page.keyboard
        want = char.lower() if char.isalpha() else char
        if not any(key == want for _, key in board.keys):
            switch = kb.SYMBOLS if char.isdigit() else kb.LETTERS
            _tap(page, next(rect.center for rect, key in board.keys if key == switch))
        _tap(page, next(rect.center for rect, key in page.keyboard.keys if key == want))


def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


@pytest.fixture
def go():
    pygame.display.init()
    pygame.font.init()
    surface = pygame.display.set_mode(SIZE)
    state = {"went": [], "flew": [], "closed": 0, "find": lambda query: None, "asked": []}

    def close():
        state["closed"] += 1

    def find(query):
        state["asked"].append(query)
        answer = state["find"](query)
        if isinstance(answer, Exception):
            raise answer
        return answer

    page = GoScreen(
        SIZE,
        close,
        (51.0, 0.0),
        lambda: [EGLL],
        lambda: "EGLL",
        lambda airport, favorite: state["went"].append((airport.ident, favorite)),
        flights=lambda: ON_MAP,
        go_flight=lambda aircraft, favorite: state["flew"].append((aircraft.hex, favorite)),
        find_flight=find,
        favorite_aircraft=lambda: state.get("planes", []),
    )
    yield page, state, surface, theme.Fonts.load()
    pygame.quit()


def test_a_flight_on_the_map_is_suggested_first_and_go_goes_to_it(go):
    page, state, surface, fonts = go
    _type(page, "baw117")
    assert page.results[0] == BAW and page.chosen == BAW
    assert not any(isinstance(r, Everywhere) for r in page.results), (
        "found: no need to look further"
    )
    page.draw(surface, fonts, 0.0)
    _tap(page, page.go_key.center)
    assert state["flew"] == [("400abc", False)] and state["closed"] == 1 and state["went"] == []


def test_airports_and_flights_share_the_suggestions(go):
    page, state, surface, fonts = go
    _type(page, "ba")
    kinds = [type(r).__name__ for r in page.results]
    assert "Aircraft" in kinds and "Airport" in kinds and len(page.results) == 3
    page.draw(surface, fonts, 0.0)

    # An airport's own code still puts the airport first.
    for _ in range(2):
        _tap(page, next(rect.center for rect, key in page.keyboard.keys if key == kb.BACKSPACE))
    _type(page, "lhr")
    assert page.results[0] == EGLL
    _tap(page, page.go_key.center)
    assert state["went"] == [("EGLL", False)]


def test_a_flight_not_on_the_map_can_be_looked_up_everywhere(go):
    page, state, surface, fonts = go
    far = plane("71be05", "KAL082", "HL7636", lat=48.0, lon=170.0, altitude_ft=36000)
    state["find"] = lambda query: far
    _type(page, "kal082")
    assert page.results[-1] == Everywhere("KAL082")
    page.draw(surface, fonts, 0.0)
    rect = next(r for r, s in page.suggestion_rects() if isinstance(s, Everywhere))
    _tap(page, rect.center)
    page.go(False)  # the GO key (a tap would also collect the answer on lifting)
    assert page.searching == "KAL082" and state["closed"] == 0, "the page waits for the answer"
    assert not page.can_go, "grayed out while it looks"
    page.draw(surface, fonts, 0.0)
    page.go(False)  # the GO key (a tap would also collect the answer on lifting)
    assert state["asked"] == ["KAL082"], "a second press does not ask again"

    assert _wait(lambda: page._lookup is not None or state["closed"] == 1)
    page.draw(surface, fonts, 0.0)
    assert state["flew"] == [("71be05", False)] and state["closed"] == 1
    page.go(False)  # the GO key (a tap would also collect the answer on lifting)
    assert state["asked"] == ["KAL082"] and len(state["flew"]) == 1, "inert once it has closed"


def test_a_lookup_that_finds_nothing_or_fails_says_so_and_stays_open(go):
    page, state, surface, fonts = go
    _type(page, "zzz999")
    assert page.chosen == Everywhere("ZZZ999"), "nothing else matches, so it is what GO will do"
    page.go(False)  # the GO key (a tap would also collect the answer on lifting)
    assert _wait(lambda: page._lookup is not None)
    page.draw(surface, fonts, 0.0)
    assert page.message == "No sign of ZZZ999 now or in the last few days."
    assert not page.can_go, "GO is grayed out: there is nothing to go to"
    page.draw(surface, fonts, 0.0)
    assert not page.keyboard.done_enabled
    page.go(False)
    assert state["asked"] == ["ZZZ999"], "and pressing it does not ask again"
    assert state["closed"] == 0 and state["flew"] == [] and page.searching is None
    _type(page, "1")
    assert page.message == "" and page.can_go, "typing again clears it, and GO is back"

    # A failed lookup is not "not found": GO stays, to try again.
    state["find"] = lambda query: requests.ConnectionError("down")
    page.go(False)  # the GO key (a tap would also collect the answer on lifting)
    assert _wait(lambda: page._lookup is not None)
    page.draw(surface, fonts, 0.0)
    assert page.message == "Could not search: no connection?" and page.can_go


def test_go_to_a_flight_centers_selects_and_follows_it_wherever_it_is(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=51.47, home_lon=-0.46), tmp_path / "settings.json"
    )
    far = plane(
        "71be05",
        "KAL082",
        "HL7636",
        lat=48.0,
        lon=170.0,
        altitude_ft=36000,
        track_deg=80.0,
        ground_speed_kt=500.0,
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, find_flight=lambda q: far)
    try:
        page = app.go_page(lambda: None)
        assert page._find_flight is not None and page._flights() == []

        app.go_to_flight(far, True)
        assert app.selected_hex == "71be05" and app.following
        assert app.view_center == pytest.approx((48.0, 170.0), abs=0.05)
        assert app.settings.favorites == ["HL7636"], "saved by registration, which lasts"
        assert settings_module.load(tmp_path / "settings.json").favorites == ["HL7636"]
        app.draw(time.monotonic())
        assert app.selected_hex == "71be05", "it is on the map at once, before the feed catches up"
        assert app.positions["71be05"] == app.layout.view.center
        assert app.public_plan().lon == pytest.approx(170.0, abs=0.2), (
            "and the feed is asked about there"
        )

        app.go_to_flight(far, True)
        assert app.settings.favorites == ["HL7636"], "not added twice"
    finally:
        pygame.quit()


def test_the_go_page_can_be_left_without_choosing_anything(go):
    page, state, surface, fonts = go
    page.draw(surface, fonts, 0.0)
    close = page.close_button
    assert close.width >= theme.MIN_TOUCH_PX and close.height >= theme.MIN_TOUCH_PX
    assert not close.colliderect(page.favorite_area) and not close.colliderect(page.box)
    assert pygame.Rect(0, 0, *SIZE).contains(close)
    assert page.keyboard.done_label == "GO", "the key says what it does here"

    # With text typed and a suggestion highlighted, closing still does nothing but close.
    _type(page, "lhr")
    assert page.chosen is not None
    _tap(page, close.center)
    assert state["closed"] == 1 and state["went"] == [] and state["flew"] == []
    _tap(page, close.center)
    _tap(page, page.go_key.center)
    assert state["closed"] == 1 and state["went"] == [], "closed once, and nothing after"


def test_closing_the_go_page_in_the_app_leaves_everything_as_it_was(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=51.47, home_lon=-0.46), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        before = (
            app.view_center,
            app.selected_hex,
            app.selected_airport,
            app.settings.home_airport,
        )
        app.open_go()
        _type(app.page, "lax")
        app.draw(time.monotonic())
        _tap(app, app.page.close_button.center)
        assert app.page is None
        after = (app.view_center, app.selected_hex, app.selected_airport, app.settings.home_airport)
        assert after == before
        app.draw(time.monotonic())
    finally:
        pygame.quit()


def test_a_flight_not_heard_now_is_found_where_it_was_last_seen_selected_and_followed(tmp_path):
    from dataclasses import replace

    from flighttrack.state import MAX_REMEMBERED, Store

    settings_module.save(
        settings_module.Settings(home_lat=51.47, home_lon=-0.46), tmp_path / "settings.json"
    )
    asked = []

    def nowhere(query):
        asked.append(query)
        return None  # the public feed has nothing: it is not broadcasting now

    app = App(fullscreen=False, data_dir=tmp_path, replay=False, find_flight=nowhere)
    try:
        # Seen here an hour ago, and gone from the feed since.
        landed = replace(CESSNA, lat=51.60, lon=-0.20, seen_at=time.monotonic() - 3600)
        app.store.set_aircraft("feed", [landed], time.monotonic())
        app.store.set_aircraft("feed", [], time.monotonic())
        assert app.store.feed().aircraft == ()

        found = app.find_flight_anywhere("N123AB")
        assert asked == ["N123AB"], "the public feed is asked first"
        assert found is not None and found.hex == CESSNA.hex
        assert app.find_flight_anywhere("ZZZ999") is None

        app.go_to_flight(found, False)
        app.draw(time.monotonic())
        assert app.selected_hex == CESSNA.hex and app.following
        assert app.view_center == pytest.approx((51.60, -0.20))
        assert app.positions[CESSNA.hex] == app.layout.view.center, "drawn where it was last seen"
        assert CESSNA.hex in app._lost, "and shown as not updating"

        # Heard again, it is followed from there.
        moved = replace(CESSNA, lat=51.62, lon=-0.21, seen_at=time.monotonic())
        app.store.set_aircraft("feed", [moved], time.monotonic())
        app.draw(time.monotonic())
        assert app.view_center == pytest.approx((51.62, -0.21)) and CESSNA.hex not in app._lost
    finally:
        pygame.quit()

    store = Store()
    for i in range(MAX_REMEMBERED + 5):
        store.set_aircraft("feed", [plane(f"{i:06x}")], time.monotonic())
    remembered = store.remembered()
    assert len(remembered) == MAX_REMEMBERED and remembered[-1].hex == f"{MAX_REMEMBERED + 4:06x}"


def test_find_takes_the_newest_sighting_from_memory_the_receiver_or_adsb_lol(tmp_path):
    from dataclasses import replace

    settings_module.save(
        settings_module.Settings(
            home_lat=51.47,
            home_lon=-0.46,
            local_receiver_url="http://rx.local/tar1090/data/aircraft.json",
        ),
        tmp_path / "settings.json",
    )
    now = time.monotonic()
    asked = []

    def history(text, receiver, known_hex):
        asked.append((text, receiver, known_hex))
        return [replace(CESSNA, lat=52.0, seen_at=now - 600)]  # heard by the receiver lately

    app = App(
        fullscreen=False,
        data_dir=tmp_path,
        replay=False,
        find_flight=lambda q: None,
        find_history=history,
    )
    try:
        app.store.set_aircraft("feed", [replace(CESSNA, seen_at=now - 7200)], now)
        app.store.set_aircraft("feed", [], now)
        found = app.find_flight_anywhere("N123AB")
        assert found.lat == 52.0, "the newer of the two"
        assert asked == [("N123AB", "http://rx.local/tar1090/data/aircraft.json", CESSNA.hex)]

        def broken(text, receiver, known_hex):
            raise requests.ConnectionError("down")

        app.find_history = broken
        assert app.find_flight_anywhere("N123AB").seen_at == now - 7200, "memory still answers"
        with pytest.raises(requests.ConnectionError):
            app.find_flight_anywhere("ZZZ999")
    finally:
        pygame.quit()


def test_favorite_aircraft_sit_beside_the_airports_and_go_or_are_looked_up(go):
    from flighttrack.ui.go import FavoriteAircraft

    page, state, surface, fonts = go
    state["planes"] = ["N123AB", "N456QX", "DAL1234"]
    page.draw(surface, fonts, 0.0)
    cells = page.favorite_rects()
    assert [item for _, item in cells] == [
        EGLL,
        FavoriteAircraft("N123AB"),
        FavoriteAircraft("N456QX"),
        FavoriteAircraft("DAL1234"),
    ]
    (egll, _), (first, _) = cells[0], cells[1]
    assert egll.top == first.top and egll.right < first.left, "airports left, aircraft right"
    for rect, _ in cells:
        assert rect.height >= theme.MIN_TOUCH_PX and page.favorite_area.contains(
            rect.clip(page.favorite_area)
        )
    assert page.favorite_area.top < 20, "from the top of the screen"

    # One on the map is gone to at once; any other is looked up.
    _tap(page, first.center)
    assert page.chosen == FavoriteAircraft("N123AB")
    page.go(False)
    assert state["flew"] == [(CESSNA.hex, False)] and state["closed"] == 1
    page.closed = False
    _tap(page, cells[2][0].center)
    page.go(False)
    assert page.searching == "N456QX" or state["asked"] == ["N456QX"]

import time

import pygame
import pytest
import requests

from flighttrack import airports, metar, runways
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.metar import MetarStore
from flighttrack.ui import keyboard as kb
from flighttrack.ui import panel, theme
from flighttrack.ui.go import GoScreen

EGLL = airports.find("EGLL")
KLAX = airports.find("KLAX")
KPAO = airports.find("KPAO")


def idents(found):
    return [a.ident for a in found]


def test_search_ranks_exact_codes_then_prefixes_then_names():
    assert idents(airports.search("lhr"))[0] == "EGLL", "an IATA code typed in full"
    assert idents(airports.search("EGLL"))[0] == "EGLL"
    assert idents(airports.search("PAO"))[0] == "KPAO", "a US code without its K"
    assert idents(airports.search("heath"))[0] == "EGLL", "a word of the name"
    assert "EGLL" in idents(airports.search("london", limit=6)), "the city"
    assert idents(airports.search("san fr"))[0] == "KSFO", "several words"
    assert all(a.ident.startswith("EG") or a.iata.startswith("EG") for a in airports.search("EG"))
    assert airports.search("") == () and airports.search("   ") == ()
    assert airports.search("zzzzqq") == ()


def test_search_prefers_big_airports_and_then_near_ones():
    big_first = airports.search("lon", limit=5)
    assert big_first[0].size == airports.LARGE
    sizes = [a.size for a in airports.search("spring", limit=8)]
    assert sizes == sorted(sizes, key="LMSW".index)
    # Among airports of one size matching equally well, the nearer comes first.
    for here in ((39.8, -89.6), (37.2, -93.4)):
        found = [a for a in airports.search("springfield", here, limit=8) if a.size == "M"]
        distances = [(a.lat - here[0]) ** 2 + (a.lon - here[1]) ** 2 for a in found]
        assert len(found) >= 2 and distances == sorted(distances)


def test_airports_near_a_point_by_size():
    everything = airports.near(51.47, -0.45, 25)
    large = airports.near(51.47, -0.45, 25, "L")
    assert "EGLL" in idents(large) and len(large) < len(everything)
    assert all(a.size == "L" for a in large)
    assert idents(everything)[: len(large)] == idents(large), "biggest first"
    assert airports.near(0.0, -30.0, 20) == ()


def test_airport_details_and_runways():
    assert (EGLL.size, EGLL.iata, EGLL.elevation_ft) == ("L", "LHR", 83)
    two = runways.at("EGLL")
    assert sorted(r.le_ident for r in two) == ["09L", "09R"]
    assert all(r.length_ft and r.length_ft > 10_000 for r in two)
    assert two[0].length_ft >= two[1].length_ft, "longest first"
    assert runways.at("NOPE") == ()


RECORD = {
    "icaoId": "KLAX",
    "obsTime": 1_800_000_000,
    "temp": 23.9,
    "dewp": 18.3,
    "wdir": 260,
    "wspd": 13,
    "wgst": 22,
    "visib": "10+",
    "altim": 1007.2,
    "rawOb": "METAR KLAX 092253Z 26013G22KT 10SM FEW010 BKN035 OVC110 24/18 A2974 RMK AO2",
    "cover": "OVC",
    "clouds": [
        {"cover": "FEW", "base": 1000},
        {"cover": "BKN", "base": 3500},
        {"cover": "OVC", "base": 11000},
    ],
    "fltCat": "VFR",
}


def test_metar_is_parsed_into_what_the_panel_shows():
    report = metar.parse(RECORD)
    assert report.station == "KLAX" and report.category == "VFR"
    assert report.raw.startswith("KLAX 092253Z"), "the word METAR is dropped to save room"
    assert report.wind == "260° 13 G22 kt"
    assert report.ceiling_ft == 3500, "the lowest broken or overcast layer"
    assert report.visibility == "10+" and report.temp_c == 23.9
    assert report.altimeter == "29.74 inHg", "in the units the station reports"

    metric = metar.parse(
        {**RECORD, "rawOb": "EGLL 092250Z AUTO 25007KT 9999 NCD 12/08 Q1012", "altim": 1012}
    )
    assert metric.altimeter == "1012 hPa"
    assert metar.parse({**RECORD, "wspd": 0}).wind == "Calm"
    assert metar.parse({**RECORD, "wdir": "VRB", "wspd": 3, "wgst": None}).wind == "VRB 3 kt"
    clear = metar.parse({**RECORD, "clouds": [], "cover": "CLR", "fltCat": "nonsense"})
    assert clear.ceiling_ft is None and clear.category is None
    assert metar.parse({"icaoId": "KLAX"}) is None and metar.parse({"rawOb": "x"}) is None


def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_metar_store_fetches_once_caches_and_survives_failures():
    calls, now, fail = [], [0.0], [False]

    def fetch(station):
        calls.append(station)
        if fail[0]:
            raise requests.ConnectionError("down")
        return [RECORD] if station == "KLAX" else []

    store = MetarStore(fetch=fetch, clock=lambda: now[0])
    store.start()
    try:
        assert store.lookup("klax") == metar.LOADING
        assert _wait(lambda: isinstance(store.lookup("KLAX"), metar.Metar))
        assert store.lookup("KPAO") == metar.LOADING
        assert _wait(lambda: store.lookup("KPAO") == metar.NONE), "no report for this station"
        assert calls == ["KLAX", "KPAO"], "asked once each while fresh"

        # When it has aged it is asked for again; a failure keeps the report we had.
        now[0] += metar.FRESH_FOR_S + 1
        fail[0] = True
        assert isinstance(store.lookup("KLAX"), metar.Metar), "the old one meanwhile"
        assert _wait(lambda: len(calls) == 3)
        time.sleep(0.05)
        assert isinstance(store.lookup("KLAX"), metar.Metar), "an old report beats an error"

        assert store.lookup("EGLL") == metar.LOADING
        assert _wait(lambda: store.lookup("EGLL") == metar.FAILED)
    finally:
        store.stop()


SIZE = (800, 480)


def _tap(target, pos):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        target.handle_event(pygame.event.Event(kind, pos=pos, button=1))


def _drag(target, start, end, steps=5):
    target.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=start, button=1))
    for i in range(1, steps + 1):
        pos = (start[0], round(start[1] + (end[1] - start[1]) * i / steps))
        target.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1, 0, 0))
        )
    target.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=end, button=1))


def _press(board, name):
    _tap(board, next(rect.center for rect, key in board.keyboard.keys if key == name))


def _type(page, text):
    for char in text:
        _press(page, char)


@pytest.fixture
def go():
    pygame.display.init()
    pygame.font.init()
    surface = pygame.display.set_mode(SIZE)
    state = {"favorites": [EGLL, KLAX], "home": "EGLL", "went": [], "closed": 0}

    def close():
        state["closed"] += 1

    page = GoScreen(
        SIZE,
        close,
        (51.0, 0.0),
        lambda: state["favorites"],
        lambda: state["home"],
        lambda airport, favorite: state["went"].append((airport.ident, favorite)),
    )
    yield page, state, surface, theme.Fonts.load()
    pygame.quit()


def test_go_page_suggests_as_you_type_and_goes_to_the_best_match(go):
    page, state, surface, fonts = go
    page.draw(surface, fonts, 0.0)
    assert page.results == () and page.chosen is None
    _tap(page, page.go_key.center)
    assert state["went"] == [] and state["closed"] == 0, "nothing to go to yet"

    _type(page, "lh")
    assert page.keyboard.text == "lh"
    assert 0 < len(page.results) <= 3 and page.chosen == page.results[0]
    _type(page, "r")
    assert page.chosen == EGLL
    page.draw(surface, fonts, 0.0)
    for rect, _ in page.suggestion_rects():
        assert rect.height >= theme.MIN_TOUCH_PX and rect.bottom <= page.keyboard.keys[0][0].top
    _tap(page, page.go_key.center)
    assert state["went"] == [("EGLL", False)] and state["closed"] == 1


def test_go_page_lets_you_pick_another_suggestion(go):
    page, state, surface, fonts = go
    _type(page, "london")
    assert len(page.results) == 3
    second_rect, second = page.suggestion_rects()[1]
    _tap(page, second_rect.center)
    assert page.chosen == second and page.keyboard.text == "london", "typing is left alone"
    page.draw(surface, fonts, 0.0)
    _tap(page, page.go_key.center)
    assert state["went"] == [(second.ident, False)], "GO only looks: nothing is made home"

    # The keyboard's own Done key is GO; typing more goes back to the best match.
    page.closed = False  # (as if the page had been opened afresh)
    _type(page, "x")
    assert page.results == () and page.chosen is None
    _press(page, kb.BACKSPACE)
    assert page.chosen == page.results[0]
    _press(page, kb.DONE)
    assert state["went"][-1] == (page.results[0].ident, False)


def test_go_page_lists_favorites_which_scroll_and_a_tap_only_chooses_one(go):
    page, state, surface, fonts = go
    page.draw(surface, fonts, 0.0)
    rows = page.favorite_rects()
    assert [a.ident for _, a in rows] == ["EGLL", "KLAX"]
    assert all(rect.height >= theme.MIN_TOUCH_PX for rect, _ in rows)
    # A tap on a favorite does what a tap on a suggestion does: chooses it, no more. Home
    # does not move and the page stays, until GO.
    _tap(page, rows[1][0].center)
    assert page.chosen == KLAX and state["went"] == [] and state["closed"] == 0
    page.draw(surface, fonts, 0.0)
    _tap(page, page.go_key.center)
    assert state["went"] == [("KLAX", False)] and state["closed"] == 1, "GO only looks"
    page.closed = False  # (as if the page had been opened afresh)
    _tap(page, rows[0][0].center)

    # With more than fit, the list scrolls by dragging and stops at its ends.
    state["favorites"] = list(airports.near(51.47, -0.45, 40, "LM"))[:9]
    assert len(state["favorites"]) == 9
    page.draw(surface, fonts, 0.0)
    area = page.favorite_area
    assert page.favorite_rects()[-1][0].top > area.bottom, "the last one starts out of sight"
    _drag(page, (area.centerx, area.bottom - 10), (area.centerx, area.bottom - 2000))
    last_rect, last = page.favorite_rects()[-1]
    assert area.contains(last_rect), "scrolled to the end, and no further"
    page.draw(surface, fonts, 0.0)
    assert page.chosen == EGLL, "a drag is not a tap"
    _tap(page, last_rect.center)
    assert page.chosen == last
    _drag(page, (area.centerx, area.top + 10), (area.centerx, area.top + 3000))
    assert page.scroll == 0


def test_go_page_closes_from_its_cross_only_and_its_buttons_say_what_they_do(go):
    page, state, surface, fonts = go
    assert kb.CANCEL not in [key for _, key in page.keyboard.keys], "no Cancel among the keys"
    assert page.keyboard.cancel_rect is None
    # GO is the keyboard's key; there are no other buttons along the top.
    assert not hasattr(page, "go_button") and not hasattr(page, "fav_button")
    assert page.go_key.bottom == page.size[1]
    _type(page, "lhr")
    assert isinstance(page.chosen, type(EGLL))
    page.draw(surface, fonts, 0.0)
    _tap(page, page.close_button.center)
    assert state["closed"] == 1 and state["went"] == []


def test_the_text_on_the_go_page_can_be_edited_in_the_middle(go):
    page, _, surface, fonts = go
    _type(page, "lax")
    page.draw(surface, fonts, 0.0)
    _press(page, kb.LEFT)
    _press(page, kb.LEFT)
    _press(page, kb.BACKSPACE)
    _type(page, "k")
    assert page.keyboard.text == "kax" and page.keyboard.cursor == 1
    # A touch on the text puts the cursor there: here, at its very end.
    page.draw(surface, fonts, 0.0)
    _tap(page, (page.box.right - 20, page.box.centery))
    assert page.keyboard.cursor == 3
    _press(page, kb.BACKSPACE)
    _type(page, "s")
    assert page.keyboard.text == "kas"


@pytest.fixture
def app(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=51.4707, home_lon=-0.4599, range_nm=25),
        tmp_path / "settings.json",
    )
    calls = []

    def fetch(station):
        calls.append(station)
        return [{**RECORD, "icaoId": station}] if station == "EGLL" else []

    app = App(fullscreen=False, data_dir=tmp_path, replay=False, metar=MetarStore(fetch=fetch))
    app.metar.start()
    app.metar_calls = calls
    yield app
    app.metar.stop()
    pygame.quit()


def _xy(app, airport):
    from flighttrack.ui.scope import screen_xy

    return screen_xy(app.center, airport.lat, airport.lon, app.zoom(), app.layout.view)


def test_go_button_opens_the_page_and_go_moves_the_view_without_moving_home(app):
    # GO and the cog sit at the lower right of the map, mirroring the range buttons.
    view = app.layout.view
    assert app.go_button in app.buttons and app.settings_button in app.buttons
    for button, twin in ((app.go_button, app.range_up), (app.settings_button, app.range_down)):
        assert view.contains(button.rect) and button.rect.size == twin.rect.size
        assert button.rect.top == twin.rect.top
        assert view.right - button.rect.right == twin.rect.left - view.left
    _tap(app, app.go_button.rect.center)
    page = app.page
    assert isinstance(page, GoScreen)
    app.draw(time.monotonic())
    _type(page, "lax")
    _tap(app, page.go_key.center)

    assert app.page is None
    assert app.view_center == KLAX.point and app.center == KLAX.point
    assert app.home == (51.4707, -0.4599), "home has not moved"
    assert app.settings.favorite_airports == [] and app.selected_airport == KLAX
    assert app.public_plan().lat == KLAX.lat, "traffic is fetched for where we went"
    assert app.home_button in app.buttons
    app.draw(time.monotonic())
    saved = settings_module.load(app.settings_path)
    assert (saved.view_lat, saved.view_lon) == KLAX.point, "and it is where we come back to"


def test_making_an_airport_home_saves_it_as_a_favorite_and_moves_home(app):
    app.go_to(KPAO, True)
    assert app.settings.favorite_airports == ["KPAO"] and app.settings.home_airport == "KPAO"
    assert app._reconfigure.is_set()
    app._reconfigure.clear()
    app.configure_feeds()
    assert app.home == KPAO.point and app.view_center is None
    assert app.selected_airport == KPAO, "its details are shown on arrival"

    # A second favorite becomes home in turn; choosing the first again moves home back.
    app.go_to(KLAX, True)
    assert app.settings.favorite_airports == ["KPAO", "KLAX"]
    assert app.settings.home_airport == "KLAX"
    app.choose_favorite_airport(KPAO)
    assert app.settings.home_airport == "KPAO"
    assert app.settings.favorite_airports == ["KPAO", "KLAX"], "order is kept, nothing doubled"
    saved = settings_module.load(app.settings_path)
    assert saved.home_airport == "KPAO" and saved.favorite_airports == ["KPAO", "KLAX"]


def test_tapping_an_airport_on_the_map_selects_it_and_fetches_its_weather(app):
    app.draw(time.monotonic())
    assert EGLL in app.visible_airports()
    _tap(app, _xy(app, EGLL))
    assert app.selected_airport == EGLL and app.selected_hex is None
    assert app.back_button in app.buttons
    app.draw(time.monotonic())
    assert _wait(lambda: isinstance(app.metar.lookup("EGLL"), metar.Metar))
    assert app.metar_calls == ["EGLL"]
    app.draw(time.monotonic())
    for _ in range(5):
        app.draw(time.monotonic())
    assert app.metar_calls == ["EGLL"], "drawing does not ask again"

    # Another airport nearby, with no weather station.
    other = next(a for a in app.visible_airports() if a.ident != "EGLL")
    _tap(app, _xy(app, other))
    assert app.selected_airport == other
    app.draw(time.monotonic())

    _tap(app, app.back_button.rect.center)
    assert app.selected_airport is None


def test_the_heart_adds_and_removes_a_favorite_without_moving_home(app):
    app.select_airport(EGLL)
    app.draw(time.monotonic())
    heart = panel.heart_rect(app.layout.panel)
    assert heart.width >= theme.MIN_TOUCH_PX and app.layout.panel.contains(heart)

    def gold():
        app.draw(time.monotonic())
        return pygame.mask.from_threshold(
            app.screen.subsurface(heart), theme.FAVORITE_AIRPORT, (2, 2, 2, 255)
        ).count()

    outline = gold()
    assert outline == 0, "an outline in gray while it is not a favorite"
    _tap(app, heart.center)
    assert app.settings.favorite_airports == ["EGLL"]
    assert app.settings.home_airport is None and not app._reconfigure.is_set()
    assert gold() > 150, "filled in once it is"
    assert settings_module.load(app.settings_path).favorite_airports == ["EGLL"]

    _tap(app, heart.center)
    assert app.settings.favorite_airports == [] and gold() == 0

    # Taking the heart off the home airport hands home to another favorite.
    app.go_to(KLAX, True)
    app.go_to(EGLL, True)
    app._reconfigure.clear()
    app.toggle_favorite_airport(EGLL)
    assert app.settings.home_airport == "KLAX" and app._reconfigure.is_set()
    app.toggle_favorite_airport(KLAX)
    assert app.settings.home_airport is None and app.settings.favorite_airports == []


def test_airports_shown_thin_out_with_range_but_favorites_always_show(app):
    def shown(range_nm):
        app.settings.range_nm = range_nm
        return app.visible_airports()

    close, mid, wide = shown(10), shown(50), shown(250)
    assert {a.size for a in close} >= {"L", "S"}
    assert {a.size for a in mid} <= {"L", "M"}
    assert {a.size for a in wide} == {"L"}

    assert {a.size for a in shown(25)} <= {"L", "M"}  # small fields would crowd the labels
    small = next(a for a in shown(10) if a.size == "S")
    assert small not in shown(250)
    app.toggle_favorite_airport(small)
    assert small in shown(250), "a favorite is marked at any range"
    overlay = next(o for o in app.overlays() if o.key[0] == "airports")
    assert small.ident in overlay.key[2]
    app.draw(time.monotonic())


def test_a_tap_on_several_things_at_once_offers_a_list_to_choose_from(app):
    from flighttrack.feeds.base import Aircraft
    from flighttrack.geo.mercator import shifted

    now = time.monotonic()
    # Somewhere on the map with no airport marker within reach, for an aircraft on its own.
    view = app.layout.view
    spot = next(
        (x, y)
        for x in range(view.left + 60, view.right - 60, 40)
        for y in range(view.top + 60, view.bottom - 60, 40)
        if not app.airports_near((x, y)) and abs(x - view.centerx) > 80
    )
    alone = shifted(app.center, spot[0] - view.centerx, spot[1] - view.centery, app.zoom())
    # Two aircraft and the airport itself, all within a fingertip of each other.
    planes = [
        Aircraft(
            "abc123",
            EGLL.lat + 0.004,
            EGLL.lon,
            now,
            callsign="TST1",
            altitude_ft=3000,
            track_deg=90,
            type_code="B738",
        ),
        Aircraft(
            "def456",
            EGLL.lat - 0.003,
            EGLL.lon + 0.004,
            now,
            callsign="TST2",
            altitude_ft=900,
            track_deg=270,
        ),
        Aircraft("777777", *alone, now, callsign="FAR1", altitude_ft=20000, track_deg=0),
    ]
    app.store.set_aircraft("public", planes, now)
    app.draw(time.monotonic())
    x, y = _xy(app, EGLL)

    _tap(app, (x, y))
    assert app.selected_hex is None and app.selected_airport is None, "nothing chosen for you"
    assert sorted(str(t if k == "aircraft" else t.ident) for k, t in app.candidates) == [
        "EGLL",
        "abc123",
        "def456",
    ]
    app.draw(time.monotonic())
    rows = app.candidate_targets
    assert len(rows) == 3 and all(rect.height >= theme.MIN_TOUCH_PX for rect, _ in rows)
    assert all(app.layout.panel.contains(rect) for rect, _ in rows)
    assert app.back_button in app.buttons

    # Choosing a row selects that thing and closes the list.
    rect = next(r for r, target in rows if getattr(target, "hex", None) == "def456")
    _tap(app, rect.center)
    assert app.selected_hex == "def456" and app.candidates == []
    app.draw(time.monotonic())

    _tap(app, (x, y))
    app.draw(time.monotonic())
    rect = next(r for r, target in app.candidate_targets if target == EGLL)
    _tap(app, rect.center)
    assert app.selected_airport == EGLL and app.selected_hex is None and app.candidates == []

    # Back dismisses the list; a tap on something alone still selects it at once.
    _tap(app, (x, y))
    assert len(app.candidates) == 3
    _tap(app, app.back_button.rect.center)
    assert app.candidates == [] and app.selected_airport is None
    far = app.positions["777777"]
    assert not app.airports_near(far)
    _tap(app, far)
    assert app.selected_hex == "777777" and app.candidates == []

    # An aircraft that leaves while the list is up simply drops out of it.
    _tap(app, (x, y))
    app.store.set_aircraft("public", planes[1:], time.monotonic())
    app.draw(time.monotonic())
    assert len(app.candidate_targets) == 2

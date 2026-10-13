"""Milestone 4: routes, operators, airframes and photos for the selected aircraft."""

import dataclasses
import io
import json
import time

import pygame
import pytest
import requests

from flighttrack import enrich
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.enrich import Airframe, Enricher, Photo, Place, Route
from flighttrack.feeds.base import Aircraft
from flighttrack.settings import Settings
from flighttrack.ui import panel, scope, theme

LHR = {"iata_code": "LHR", "icao_code": "EGLL", "latitude": 51.4706, "longitude": -0.4619,
       "municipality": "London", "name": "London Heathrow Airport"}  # fmt: skip
JFK = {"iata_code": "JFK", "icao_code": "KJFK", "latitude": 40.6398, "longitude": -73.7789,
       "municipality": "New York", "name": "John F Kennedy International Airport"}  # fmt: skip
ROUTE = {"response": {"flightroute": {"callsign": "BAW117", "airline": {"name": "British Airways"},
                                      "origin": LHR, "destination": JFK}}}  # fmt: skip
THUMB = "https://airport-data.com/images/aircraft/thumbnails/001/598/001598299.jpg"
AIRFRAME = {"response": {"aircraft": {"type": "777-236ER", "manufacturer": "Boeing",
                                      "registered_owner": "British Airways",
                                      "url_photo_thumbnail": THUMB}}}  # fmt: skip


def _jpeg(color=(200, 60, 40), size=(200, 133)) -> bytes:
    pygame.display.init()
    surface = pygame.Surface(size)
    surface.fill(color)
    buffer = io.BytesIO()
    pygame.image.save(surface, buffer, "photo.jpg")
    return buffer.getvalue()


class Web:
    """Stands in for the two services: answers by address, and notes what was asked."""

    def __init__(self):
        self.asked = []
        self.json = {}
        self.bytes = {}

    def get_json(self, url):
        self.asked.append(url)
        answer = self.json.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_bytes(self, url, limit=None):
        self.asked.append(url)
        answer = self.bytes.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def service(tmp_path):
    web = Web()
    made = []

    def make(**more):
        one = Enricher(tmp_path, web.get_json, web.get_bytes, spacing_s=0.0, **more)
        one.start()
        made.append(one)
        return one

    yield make, web, tmp_path
    for one in made:
        one.stop()


def _wait(ask, timeout=3.0):
    deadline = time.monotonic() + timeout
    answer = ask()
    while answer == enrich.LOADING and time.monotonic() < deadline:
        time.sleep(0.01)
        answer = ask()
    return answer


# ---- reading the services' answers ---------------------------------------------------------


def test_a_route_is_read_from_the_answer_and_nonsense_is_refused():
    route = enrich.parse_route(ROUTE)
    assert route == Route(
        "British Airways",
        Place("LHR", "London Heathrow Airport", "London", 51.4706, -0.4619),
        Place("JFK", "John F Kennedy International Airport", "New York", 40.6398, -73.7789),
    )
    assert enrich.parse_route({"response": "unknown callsign"}) is None
    assert enrich.parse_route(None) is None and enrich.parse_route([1, 2]) is None
    no_iata = {**LHR, "iata_code": ""}
    answer = {"response": {"flightroute": {"origin": no_iata, "destination": JFK}}}
    assert enrich.parse_route(answer).origin.code == "EGLL", "the ICAO code when there is no IATA"
    for bad in (
        {**LHR, "latitude": 123.0},
        {**LHR, "latitude": "north"},
        {k: v for k, v in LHR.items() if k != "longitude"},
        {**LHR, "iata_code": "", "icao_code": ""},
        "Heathrow",
    ):
        with pytest.raises(ValueError):
            enrich.parse_route({"response": {"flightroute": {"origin": bad, "destination": JFK}}})


def test_only_airline_callsigns_are_asked_about():
    for callsign in ("BAW117", "DAL5", "EZY12AB", "ual2401"):
        assert enrich.could_have_a_route(callsign), callsign
    for callsign in ("N123AB", "GABCD", "", None, "BAW", "12345", "A1", "TOOLONGCALLSIGN1"):
        assert not enrich.could_have_a_route(callsign), callsign


def test_an_airframe_is_read_and_its_photo_address_must_be_one_we_trust():
    assert enrich.parse_airframe(AIRFRAME) == Airframe("Boeing 777-236ER", "British Airways", THUMB)
    cessna = {"response": {"aircraft": {"type": "Cessna 172S", "manufacturer": "Cessna"}}}
    assert enrich.parse_airframe(cessna).type_name == "Cessna 172S", "the maker is not said twice"
    assert enrich.parse_airframe({"response": "unknown aircraft"}) is None
    shouting = {"response": {"aircraft": {"type": "737-8200", "manufacturer": "BOEING"}}}
    assert enrich.parse_airframe(shouting).type_name == "Boeing 737-8200"
    atr = {"response": {"aircraft": {"type": "72-600", "manufacturer": "ATR"}}}
    assert enrich.parse_airframe(atr).type_name == "ATR 72-600"

    assert enrich.allowed_photo_url(THUMB) == THUMB
    assert enrich.allowed_photo_url("https://image.airport-data.com/aircraft/1.jpg")
    for bad in (
        "http://airport-data.com/a.jpg",  # not https
        "https://evil.example/airport-data.com/a.jpg",
        "https://airport-data.com.evil.example/a.jpg",
        "https://notairport-data.com/a.jpg",
        "file:///etc/passwd",
        "https://192.168.1.1/a.jpg",
        None,
        "https://airport-data.com/" + "a" * 400,
    ):
        assert enrich.allowed_photo_url(bad) == "", bad
    elsewhere = {
        "response": {"aircraft": {"type": "X", "url_photo_thumbnail": "https://evil.example/x.jpg"}}
    }
    assert enrich.parse_airframe(elsewhere).photo_url == ""
    two = {
        "data": [
            {"image": THUMB, "photographer": "wijken"},
            {"image": "https://evil.example/x.jpg"},
        ]
    }
    assert enrich.parse_thumbs(two) == [(THUMB, "wijken")], "only addresses we trust"
    assert enrich.parse_thumbs({"status": 404, "error": "not found"}) == []
    assert enrich.full_size_url(THUMB) == "https://image.airport-data.com/aircraft/001598299.jpg"
    assert enrich.full_size_url("https://airport-data.com/somewhere/else.jpg") == ""
    assert enrich.photo_page(THUMB) == "airport-data.com/aircraft/photo/001598299"


# ---- the service ---------------------------------------------------------------------------


def test_a_route_is_asked_for_once_and_remembered_across_a_restart(service):
    make, web, tmp_path = service
    web.json[enrich.ROUTE_URL.format("BAW117")] = ROUTE
    one = make()
    assert one.route("BAW117") == enrich.LOADING, "asked in the background, not waited for"
    route = _wait(lambda: one.route("baw117 "))
    assert isinstance(route, Route) and route.destination.code == "JFK"
    for _ in range(5):
        assert one.route("BAW117") == route
    assert web.asked == [enrich.ROUTE_URL.format("BAW117")]
    assert one.route("N123AB") == enrich.NONE and len(web.asked) == 1, "never asked"
    one.stop()

    again = make()
    assert again.route("BAW117") == route, "there at once, from disk"
    assert len(web.asked) == 1


def test_what_the_service_does_not_know_and_what_fails_are_told_apart(service):
    make, web, _ = service
    one = make()
    assert _wait(lambda: one.route("ZZZ999")) == enrich.NONE, "a 404 is an answer"
    web.json[enrich.ROUTE_URL.format("DAL5")] = requests.ConnectionError("offline")
    assert _wait(lambda: one.route("DAL5")) == enrich.FAILED
    web.json[enrich.ROUTE_URL.format("UAL1")] = {"response": {"flightroute": {"origin": {}}}}
    assert _wait(lambda: one.route("UAL1")) == enrich.FAILED, "nonsense is a failure, not a crash"
    # A failure is asked about again soon; "not known" is left for an hour.
    asked = len(web.asked)
    assert one.route("ZZZ999") == enrich.NONE and one.route("DAL5") == enrich.FAILED
    time.sleep(0.05)
    assert len(web.asked) == asked


SECOND = "https://airport-data.com/images/aircraft/thumbnails/001/805/001805296.jpg"


def test_photos_come_from_both_services_different_ones_only_and_are_kept(service):
    make, web, tmp_path = service
    hex_code = "400a0b"
    first, second = _jpeg((200, 60, 40)), _jpeg((40, 60, 200))
    web.json[enrich.AIRFRAME_URL.format(hex_code)] = AIRFRAME
    # airport-data offers the one adsbdb already pointed at, with its photographer, and another.
    web.json[enrich.THUMB_URL.format(hex_code)] = {
        "data": [
            {"image": THUMB, "photographer": "wijken"},
            {"image": SECOND, "photographer": "A. Spotter"},
        ]
    }
    web.bytes[THUMB], web.bytes[SECOND] = first, second
    one = make()
    found = _wait(lambda: one.photos(hex_code))
    assert [p.data for p in found] == [first, second], "adsbdb's first, then the different one"
    assert found[0].credit == "wijken / airport-data.com", "the same picture, credited once known"
    assert found[1].credit == "A. Spotter / airport-data.com"
    assert found[0].page == "airport-data.com/aircraft/photo/001598299"
    assert found[0].attribution == (
        "Photo by wijken, airport-data.com (airport-data.com/aircraft/photo/001598299)"
    )
    assert web.asked.count(THUMB) == 1, "each picture fetched once"
    assert isinstance(one.airframe(hex_code), Airframe), "and the airframe came with it"
    asked = len(web.asked)
    assert one.photos(hex_code) is found and len(web.asked) == asked
    one.stop()
    again = make()
    kept = _wait(lambda: again.photos(hex_code))
    assert [p.data for p in kept] == [first, second] and len(web.asked) == asked, "from disk"
    assert kept[1].credit == found[1].credit and kept[0].full_url == found[0].full_url

    # Only one service has one: one picture.
    other = "a1b2c3"
    web.json[enrich.AIRFRAME_URL.format(other)] = {
        "response": {"aircraft": {"type": "172", "manufacturer": "Cessna"}}
    }
    web.json[enrich.THUMB_URL.format(other)] = {
        "data": [{"image": SECOND, "photographer": "A. Spotter"}]
    }
    assert len(_wait(lambda: again.photos(other))) == 1

    # Two addresses for what turns out to be the same file: shown once.
    twin = "b00b00"
    web.json[enrich.THUMB_URL.format(twin)] = {"data": [{"image": THUMB}, {"image": SECOND}]}
    web.bytes[SECOND] = first
    assert len(_wait(lambda: again.photos(twin))) == 1
    web.bytes[SECOND] = second

    # Neither has one: no photo, and that is remembered, not asked again each frame.
    nobody = "ffffff"
    assert _wait(lambda: again.photos(nobody)) == enrich.NONE
    asked = len(web.asked)
    for _ in range(5):
        assert again.photos(nobody) == enrich.NONE
    time.sleep(0.05)
    assert len(web.asked) == asked


def test_the_larger_picture_is_fetched_only_when_asked_for_and_kept_in_memory_only(service):
    make, web, tmp_path = service
    hex_code = "400a0b"
    big = _jpeg((200, 60, 40), (1024, 683))
    web.json[enrich.AIRFRAME_URL.format(hex_code)] = AIRFRAME
    web.bytes[THUMB] = _jpeg()
    web.bytes[enrich.full_size_url(THUMB)] = big
    one = make()
    assert len(_wait(lambda: one.photos(hex_code))) == 1
    assert enrich.full_size_url(THUMB) not in web.asked, "not until someone opens the picture"
    assert _wait(lambda: one.full_photo(hex_code, 0)) == big
    assert one.full_photo(hex_code, 0) is not enrich.LOADING
    assert web.asked.count(enrich.full_size_url(THUMB)) == 1
    assert _wait(lambda: one.full_photo(hex_code, 1)) == enrich.NONE, "there is no second"
    assert not [p for p in (tmp_path / "enrich").rglob("*") if p.stat().st_size > len(big) - 10]
    # A larger picture that will not download: there just is none (the thumbnail is shown).
    web.json[enrich.AIRFRAME_URL.format("c0ffee")] = AIRFRAME
    web.bytes[enrich.full_size_url(THUMB)] = b"<html>busy</html>"
    _wait(lambda: one.photos("c0ffee"))
    assert _wait(lambda: one.full_photo("c0ffee", 0)) == enrich.NONE


def test_what_is_downloaded_must_be_a_picture_from_a_trusted_address(service):
    make, web, _ = service
    one = make()
    web.json[enrich.AIRFRAME_URL.format("111111")] = AIRFRAME
    web.bytes[THUMB] = b"<html>not a picture</html>"
    assert _wait(lambda: one.photos("111111")) == enrich.NONE

    evil = "https://evil.example/x.jpg"
    web.json[enrich.AIRFRAME_URL.format("222222")] = {"response": {"aircraft": {"type": "X"}}}
    web.json[enrich.THUMB_URL.format("222222")] = {"data": [{"image": evil}]}
    web.bytes[evil] = _jpeg()
    assert _wait(lambda: one.photos("222222")) == enrich.NONE
    assert evil not in web.asked, "never fetched"


def test_a_picture_of_your_own_wins_matched_by_registration_hex_or_type(service):
    make, web, tmp_path = service
    images = tmp_path / "images"
    images.mkdir()
    mine, typical = _jpeg((1, 2, 3)), _jpeg((9, 9, 9))
    (images / "n123ab.jpg").write_bytes(mine)
    (images / "B738.JPG").write_bytes(typical)
    (images / "notes.txt").write_text("not a picture")
    (images / "C172.png").write_bytes(b"this is not a PNG")
    web.json[enrich.AIRFRAME_URL.format("a00001")] = AIRFRAME
    web.bytes[THUMB] = _jpeg()
    one = make()

    (by_registration,) = _wait(lambda: one.photos("a00001", "N-123AB", "B738"))
    assert by_registration.data == mine and by_registration.credit == "Your image"
    assert by_registration.attribution == "Photo: Your image"
    assert web.asked == [], "nothing was looked up: yours is the only one shown"
    (by_type,) = _wait(lambda: one.photos("a00002", "N999ZZ", "B738"))
    assert by_type.data == typical
    assert _wait(lambda: one.full_photo("a00001", 0)) == mine, "full screen, it is the file itself"
    # A file that is not a picture is passed over, and the services are asked as usual.
    assert _wait(lambda: one.photos("a00003", None, "C172")) == enrich.NONE


# ---- on the display ------------------------------------------------------------------------


class FakeEnricher:
    def __init__(self):
        self.routes, self.airframes, self.pictures, self.larger = {}, {}, {}, {}
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def route(self, callsign):
        return self.routes.get(callsign, enrich.NONE)

    def airframe(self, hex_code):
        return self.airframes.get(hex_code, enrich.NONE)

    def photos(self, hex_code, registration=None, type_code=None):
        return self.pictures.get(hex_code, enrich.NONE)

    def full_photo(self, hex_code, index=0):
        return self.larger.get((hex_code, index), enrich.NONE)


@pytest.fixture
def app(tmp_path):
    settings_module.save(
        Settings(home_lat=51.6, home_lon=-1.5, range_nm=25), tmp_path / "settings.json"
    )
    fake = FakeEnricher()
    app = App(fullscreen=False, data_dir=tmp_path, replay=False, enrich=fake)
    yield app, fake
    pygame.quit()


def _select(app, **fields):
    lat, lon = app.center[0] + 0.05, app.center[1] - 0.1
    plane = Aircraft("400a0b", lat, lon, time.monotonic(), **fields)
    app.store.set_aircraft("public", [plane], time.monotonic())
    app._select("400a0b")
    return plane


def _watch(monkeypatch):
    seen = {}
    real_pane, real_scope = panel.draw_selected, scope.draw_scope
    from flighttrack import app as app_module

    def pane(*args, **kwargs):
        seen["details"] = args[11] if len(args) > 11 else kwargs.get("details")
        return real_pane(*args, **kwargs)

    def the_map(surface, view, radius, fonts, scene, *rest):
        seen["scene"] = scene
        return real_scope(surface, view, radius, fonts, scene, *rest)

    monkeypatch.setattr(app_module.panel, "draw_selected", pane)
    monkeypatch.setattr(app_module, "draw_scope", the_map)
    return seen


def test_the_pane_shows_operator_route_type_and_photo_as_they_arrive(app, monkeypatch):
    app, fake = app
    seen = _watch(monkeypatch)
    _select(app, callsign="BAW117", registration="G-VIIA", type_code="B772", altitude_ft=31000)

    # Nothing known yet: the pane is as it was, and nothing is drawn on the map.
    app.draw(time.monotonic())
    assert dataclasses.replace(seen["details"], departure="") == panel.Details()
    assert seen["details"].departure.startswith(("From ", "First seen ")), (
        "no route: where it started"
    )

    fake.routes["BAW117"] = enrich.parse_route(ROUTE)
    fake.airframes["400a0b"] = enrich.parse_airframe(AIRFRAME)
    fake.pictures["400a0b"] = (Photo(_jpeg(), "wijken / airport-data.com"),)
    app.draw(time.monotonic())
    details = seen["details"]
    assert details.operator == "British Airways"
    assert details.origin == ("LHR", "London") and details.destination == ("JFK", "New York")
    assert details.type_name == "Boeing 777-236ER"
    assert [p.get_size() for p in details.photos] == [panel.PHOTO_SIZE]
    assert details.credit == "wijken / airport-data.com"
    # The photo really is on the screen, inside the pane and above the Back button.
    body_bottom = app.back_button.rect.top
    reds = [
        (x, y)
        for x in range(app.layout.panel.left, app.layout.panel.right, 3)
        for y in range(app.layout.panel.top, app.layout.panel.bottom, 3)
        if app.screen.get_at((x, y))[0] > 170 and app.screen.get_at((x, y))[2] < 90
    ]
    assert len(reds) > 100 and max(y for _, y in reds) < body_bottom
    # Decoded once, not every frame.
    first = details.photos[0]
    app.draw(time.monotonic())
    assert seen["details"].photos[0] is first

    # A private aircraft has an owner where an airline would be, and no route.
    fake.routes.clear()
    fake.airframes["400a0b"] = Airframe("Cessna 172S", "A. Pilot")
    app.draw(time.monotonic())
    assert seen["details"].operator == "A. Pilot" and seen["details"].origin is None


def _tap(app, pos):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        app.handle_event(pygame.event.Event(kind, pos=pos, button=1))


def test_two_pictures_sit_side_by_side_and_a_tap_on_one_opens_it_full_screen(app, monkeypatch):
    from flighttrack.ui.photo import PhotoScreen

    app, fake = app
    seen = _watch(monkeypatch)
    red, blue = _jpeg((200, 60, 40)), _jpeg((40, 60, 200))
    fake.pictures["400a0b"] = (
        Photo(red, "wijken / airport-data.com", page="airport-data.com/aircraft/photo/1"),
        Photo(blue, "A. Spotter / airport-data.com", page="airport-data.com/aircraft/photo/2"),
    )
    _select(app, callsign="BAW117", registration="G-VIIA", type_code="B772", altitude_ft=31000)
    app.draw(time.monotonic())
    left, right = app.photo_targets
    side = app.layout.panel
    assert left.size == right.size == panel.PHOTO_SIZE and left.top == right.top
    assert left.right < right.left and side.contains(left) and side.contains(right)
    assert right.bottom < app.back_button.rect.top
    assert app.screen.get_at(left.center)[0] > 170 and app.screen.get_at(right.center)[2] > 170
    assert seen["details"].credit == "wijken, A. Spotter / airport-data.com"

    # A tap on the second opens that one, filling the screen, with a cross over it.
    _tap(app, right.center)
    page = app.page
    assert isinstance(page, PhotoScreen) and page.index == 1
    app.draw(time.monotonic())
    width, height = app.screen.get_size()
    assert app.screen.get_at((width // 2, height // 2 - 20))[2] > 170, "the blue one, enlarged"
    close = page.close_button
    assert close.width >= theme.MIN_TOUCH_PX and close.height >= theme.MIN_TOUCH_PX
    assert close.right <= width and close.top >= 0 and close.left > width * 0.8
    crossed = [app.screen.get_at((close.centerx + d, close.centery + d))[:3] for d in (-8, 0, 8)]
    assert all(sum(c) > 600 for c in crossed), "a white cross, drawn over the picture"

    # The whole credit is along the bottom: who, where from, and the picture's page.
    shown = []
    real = theme.text
    monkeypatch.setattr(
        "flighttrack.ui.photo.theme.text", lambda f, s, c: (shown.append(s), real(f, s, c))[1]
    )
    app.draw(time.monotonic())
    assert "Photo by A. Spotter, airport-data.com (airport-data.com/aircraft/photo/2)" in shown

    # A tap on the picture shows the other; the larger version replaces the thumbnail when
    # it arrives; the cross goes back to the map with the aircraft still selected.
    _tap(app, (width // 2, height // 2))
    assert app.page is page and page.index == 0
    app.draw(time.monotonic())
    assert app.screen.get_at((width // 2, height // 2 - 20))[0] > 170
    fake.larger[("400a0b", 0)] = _jpeg((20, 200, 60), (1024, 683))
    app.draw(time.monotonic())
    assert app.screen.get_at((width // 2, height // 2 - 20))[1] > 170, "the larger picture"
    _tap(app, close.center)
    assert app.page is None and app.selected_hex == "400a0b"
    app.draw(time.monotonic())

    # With one picture, a tap on it changes nothing; only the cross (or Escape) closes.
    fake.pictures["400a0b"] = (Photo(red, "airport-data.com"),)
    app.draw(time.monotonic())
    (only,) = app.photo_targets
    _tap(app, only.center)
    page = app.page
    _tap(app, (width // 2, height // 2))
    assert app.page is page
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode=""))
    assert app.page is None


def test_the_panes_distance_is_from_the_center_of_the_display(app, monkeypatch):
    app, fake = app
    seen = {}
    real = panel.draw_selected
    from flighttrack import app as app_module
    from flighttrack.geo import bearing_deg, distance_nm

    def pane(*args, **kwargs):
        seen["args"] = args
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module.panel, "draw_selected", pane)
    plane = _select(app, callsign="TST1", altitude_ft=9000)
    for moved in (False, True):
        if moved:
            app.pan(150, 80)  # the map dragged well away from home
        app.draw(time.monotonic())
        args = seen["args"]
        assert args[4] == pytest.approx(distance_nm(*app.center, plane.lat, plane.lon), abs=0.05)
        assert args[5] == pytest.approx(bearing_deg(*app.center, plane.lat, plane.lon), abs=0.5)
        assert "DST" in args and "HOME" not in args
    assert app.center != app.home
    from_home = distance_nm(*app.home, plane.lat, plane.lon)
    assert abs(seen["args"][4] - from_home) > 1, "not from home once the map has been moved"

    # Following puts the aircraft at the center, where the distance would always be zero:
    # only then is it given from home, and labeled HOME.
    app.toggle_following()
    app.draw(time.monotonic())
    assert seen["args"][4] == pytest.approx(from_home, abs=0.05) and "HOME" in seen["args"]


def test_the_expected_start_and_end_are_shown_wherever_the_aircraft_is(app, monkeypatch):
    app, fake = app
    seen = _watch(monkeypatch)
    far = Route(
        "Somewhere Air",
        Place("SYD", "Sydney", "Sydney", -33.95, 151.18),
        Place("AKL", "Auckland", "Auckland", -37.01, 174.79),
    )
    fake.routes["BAW117"] = far
    _select(app, callsign="BAW117", altitude_ft=31000)
    app.draw(time.monotonic())
    details = seen["details"]
    assert details.origin == ("SYD", "Sydney") and details.destination == ("AKL", "Auckland")
    assert not hasattr(details, "route_doubtful") and not hasattr(seen["scene"], "route")


def test_everything_still_fits_with_long_names_an_emergency_and_no_room_for_the_photo(app):
    app, fake = app
    long_place = Place("XXXX", "W" * 60, "Llanfairpwllgwyngyllgogerychwyrndrobwll", 51.0, -1.0)
    fake.routes["BAW117"] = Route(
        "The Very Long Name Of An Airline Company Limited", long_place, long_place
    )
    fake.airframes["400a0b"] = Airframe("McDonnell Douglas MD-11F Freighter Conversion", "Owner")
    fake.pictures["400a0b"] = (
        Photo(_jpeg(), "A Photographer With A Long Name / airport-data.com"),
        Photo(_jpeg((1, 2, 3)), "Another One With A Long Name / airport-data.com"),
    )
    _select(
        app,
        callsign="BAW117",
        registration="G-VIIA",
        type_code="B772",
        altitude_ft=31000,
        ground_speed_kt=512,
        track_deg=287,
        vertical_rate_fpm=-1800,
        squawk="7700",
        emergency=True,
    )
    app.draw(time.monotonic())
    side = app.layout.panel
    # Nothing is drawn under the Back button, and nothing outside the pane's width.
    strip = pygame.Rect(side.left, app.back_button.rect.top - 5, side.width, 5)
    assert all(
        app.screen.get_at((x, y))[:3] == theme.PANEL_FILL
        for x in range(strip.left + 2, strip.right, 4)
        for y in range(strip.top, strip.bottom)
    )
    app.following = True
    app.draw(time.monotonic())


def test_without_the_service_the_pane_is_plain_and_with_it_the_app_starts_and_stops_it(tmp_path):
    plain = App(fullscreen=False, data_dir=tmp_path, replay=True)
    try:
        plain.draw(time.monotonic())
        assert plain.enrich is None
    finally:
        pygame.quit()
    fake = FakeEnricher()
    app = App(fullscreen=False, data_dir=tmp_path, replay=True, enrich=fake)
    app.run(max_frames=2)
    assert fake.started and fake.stopped


def test_the_two_services_are_credited():
    from flighttrack import credits

    names = [name for name, _, _ in credits.SOURCES]
    assert "adsbdb.com" in names and "airport-data.com" in names
    notice = (settings_module.Path(__file__).parent.parent / "NOTICE").read_text()
    assert "adsbdb.com" in notice and "airport-data.com" in notice
    assert json.dumps(names)  # plain strings

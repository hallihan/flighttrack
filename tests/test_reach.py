"""How low the feeds hear aircraft, place by place, and what it says about landings."""

import time

import pygame

from flighttrack import airports, legs, reach
from flighttrack.feeds.base import Aircraft
from flighttrack.geo import destination

DAY = 20_000 * 86400.0


def heard(hex_code, lat, lon, altitude, now, ground=False):
    return Aircraft(hex_code, lat, lon, now, altitude_ft=altitude, on_ground=ground)


def test_the_floor_is_the_second_lowest_of_enough_aircraft():
    r = reach.Reach(wall=lambda: DAY)
    now = 100.0
    r.add("adsb.fi", [heard("a1", 35.01, -80.61, 900, now)], now)
    r.add("adsb.fi", [heard("a2", 35.01, -80.61, 1400, now)], now)
    assert r.floor(35.01, -80.61) is None, "two aircraft are too few to say"
    r.add("adsb.fi", [heard("a3", 35.01, -80.61, 2000, now)], now)
    assert r.floor(35.01, -80.61) == 1400, "one low report does not make the floor"
    r.add("adsb.fi", [heard("a3", 35.01, -80.61, 1000, now)], now)
    assert r.floor(35.01, -80.61) == 1000, "an aircraft counts once, at its lowest"
    stale = heard("a4", 35.01, -80.61, 100, now - 60)
    r.add("adsb.fi", [stale], now)
    assert r.floor(35.01, -80.61) == 1000, "a repeated old report is not counted"
    for name in ("g1", "g2", "g3"):
        r.add("adsb.fi", [heard(name, 35.01, -80.61, None, now, ground=True)], now)
    assert r.floor(35.01, -80.61) == reach.GROUND
    assert r.floor(36.0, -80.0) is None


def test_each_source_has_its_own_floor_and_the_map_shows_the_lowest():
    r = reach.Reach(wall=lambda: DAY)
    for i, h in enumerate((1500, 1600, 1800)):
        r.add("adsb.fi", [heard(f"p{i}", 35.01, -80.61, h, 10.0)], 10.0)
    for i in range(3):
        r.add("local", [heard(f"q{i}", 35.01, -80.61, None, 10.0, ground=True)], 10.0)
    assert r.floor(35.01, -80.61, {"adsb.fi"}) == 1600
    assert r.floor(35.01, -80.61, {"local"}) == reach.GROUND
    assert r.floor(35.01, -80.61) == reach.GROUND, "the lowest of all"
    assert r.floor(35.01, -80.61, {"adsb.lol"}) is None, "nothing heard by that one"
    assert r.source_counts() == {"adsb.fi": 1, "local": 1}


def test_whether_it_went_down_out_of_hearing_depends_on_who_was_listening():
    # The local receiver hears KFLO's runway; the public network only down to 1,600 ft. An
    # aircraft lost at 1,700 ft went below the public network's hearing; but had the local
    # receiver been listening, it would have heard it further down.
    kflo = airports.find("KFLO")
    near = destination(kflo.lat, kflo.lon, 270, 1.5)
    r = reach.Reach(wall=lambda: DAY)
    for i, h in enumerate((1500, 1600, 1800)):
        r.add("adsb.fi", [heard(f"p{i}", *near, h, 10.0)], 10.0)
    for i in range(3):
        r.add("local", [heard(f"q{i}", *near, None, 10.0, ground=True)], 10.0)
    assert reach.low_beside(r, *near, 1700, {"adsb.fi"}).ident == "KFLO"
    assert reach.low_beside(r, *near, 1700, {"adsb.fi", "local"}) is None


def test_the_provider_that_answered_reaches_the_map(tmp_path):
    from flighttrack.feeds.coverage import QueryPlan
    from flighttrack.feeds.poller import FeedPoller
    from flighttrack.state import Store

    class Answering:
        name = "public"
        attribution = "adsb.lol"  # adsb.fi was down: the fallback answered

        def fetch(self, lat, lon, radius_nm):
            return [heard("r1", 35.0, -80.6, 900, time.monotonic())]

    store, told = Store(), []
    store.on_answer = lambda provider, aircraft, now: told.append((provider, len(aircraft)))
    FeedPoller(Answering(), store, lambda: QueryPlan(35.0, -80.6, 10, 5)).poll_once()
    assert told == [("adsb.lol", 1)]


def test_the_map_is_kept_and_old_reports_let_go(tmp_path):
    r = reach.Reach(wall=lambda: DAY)
    for i, h in enumerate((1200, 1500, 1800)):
        r.add("adsb.fi", [heard(f"b{i}", 35.2, -80.9, h, 10.0)], 10.0)
    r.save(tmp_path / "reach.json.gz")
    again = reach.Reach(wall=lambda: DAY)
    again.load(tmp_path / "reach.json.gz")
    assert again.floor(35.2, -80.9) == 1500
    much_later = reach.Reach(wall=lambda: DAY + (reach.MAX_AGE_DAYS + 1) * 86400)
    much_later.load(tmp_path / "reach.json.gz")
    assert much_later.floor(35.2, -80.9) is None


def test_low_beside_an_airport_depends_on_how_low_the_feeds_hear_there():
    kflo = airports.find("KFLO")
    field = kflo.elevation_ft or 0
    near = destination(kflo.lat, kflo.lon, 270, 1.5)
    # Nothing learned yet: within 1,500 ft of the field.
    assert reach.low_beside(None, *near, field + 1200).ident == "KFLO"
    assert reach.low_beside(None, *near, field + 4000) is None
    # Heard down to 2,500 ft there: a track ending at 3,000 ft went out of hearing.
    r = reach.Reach(wall=lambda: DAY)
    for i, h in enumerate((2400, 2500, 2700)):
        r.add("adsb.fi", [heard(f"c{i}", *near, h, 10.0)], 10.0)
    assert reach.low_beside(r, *near, 3000).ident == "KFLO"
    assert reach.low_beside(r, *near, 5000) is None, "well above the floor: not landing"
    # Heard on the ground there: a landing would have been heard, so only a point near the
    # ground counts.
    for name in ("d1", "d2", "d3"):
        r.add("adsb.fi", [heard(name, *near, None, 10.0, ground=True)], 10.0)
    assert reach.low_beside(r, *near, field + 1200) is None
    assert reach.low_beside(r, *near, field + 200).ident == "KFLO"


def test_a_flight_first_heard_climbing_low_beside_an_airport_took_off_there():
    kflo = airports.find("KFLO")
    field = kflo.elevation_ft or 0

    def low(point):
        return reach.low_beside(None, point[0], point[1], point[2]) is not None

    start = destination(kflo.lat, kflo.lon, 315, 1)
    climb = [
        (*destination(*start, 315, 90 * s / 3600), field + 600 + s * 5, 1000.0 + s)
        for s in range(0, 301, 10)
    ]
    assert legs.took_off(climb, low), "first heard in the climb out of KFLO"
    assert not legs.took_off(climb), "without knowing where hearing ends: not to be said"
    level = [(lat, lon, field + 600, t) for lat, lon, _, t in climb]
    assert not legs.took_off(level, low), "low but not climbing: no sign of a takeoff"
    high = [(lat, lon, alt + 6000, t) for lat, lon, alt, t in climb]
    assert not legs.took_off(high, low), "well above where hearing ends: just passing"


def test_first_heard_high_over_an_airport_is_not_from_it(tmp_path):
    from flighttrack import settings as settings_module
    from flighttrack.app import App

    settings_module.save(
        settings_module.Settings(home_lat=35.0, home_lon=-80.6), tmp_path / "settings.json"
    )
    keqy = airports.find("KEQY")
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        now = time.monotonic()
        high = Aircraft("e00001", keqy.lat, keqy.lon, now, altitude_ft=35000)
        low = Aircraft("e00002", keqy.lat + 0.01, keqy.lon, now, altitude_ft=1100)
        app.store.set_aircraft("public", [high, low], now)
        assert app.departure("e00001").startswith("First seen"), "an airliner overhead"
        assert app.departure("e00002") == "From KEQY", "climbing out, first heard low"
    finally:
        pygame.quit()


def test_floors_are_shown_above_the_ground_once_its_height_is_known(tmp_path):
    r = reach.Reach(wall=lambda: DAY)
    for i, h in enumerate((1800, 1900, 2100)):
        r.add("adsb.fi", [heard(f"h{i}", 35.01, -80.61, h, 10.0)], 10.0)
    box = (34.9, -80.7, 35.1, -80.5)
    assert r.floors(*box) == [], "not shown until the ground under it is known"
    assert r.missing_ground() == [reach.cell_of(35.01, -80.61)]
    r.set_ground({reach.cell_of(35.01, -80.61): 600})
    assert r.floors(*box) == [(reach.cell_of(35.01, -80.61), 1300)], "1,900 MSL, 600 ground"
    assert r.missing_ground() == []
    assert r.counts() == (1, 1, 1)
    r.save(tmp_path / "reach.json.gz")
    again = reach.Reach(wall=lambda: DAY)
    again.load(tmp_path / "reach.json.gz")
    assert again.floors(*box) == r.floors(*box), "the ground is kept with the map"


def test_ground_heights_come_from_open_meteo_a_hundred_at_a_time():
    class Session:
        def __init__(self):
            self.headers, self.asked = {}, []

        def get(self, url, params=None, timeout=None):
            self.asked.append((url, params))
            count = len(params["latitude"].split(","))

            class Answer:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"elevation": [100.0] * count}

            return Answer()

    cells = [(i, -1600) for i in range(150)]
    session = Session()
    found = reach.open_meteo_ground(cells, session)
    assert len(session.asked) == 2, "100, then 50"
    assert session.asked[0][0] == reach.ELEVATION_URL
    assert found[cells[0]] == 328, "metres to feet"


def test_diagnostics_show_the_coverage_tint_and_its_key(tmp_path):
    from flighttrack import settings as settings_module
    from flighttrack.app import App

    settings_module.save(
        settings_module.Settings(home_lat=35.0, home_lon=-80.6), tmp_path / "settings.json"
    )
    asked = []

    def ground(cells):
        asked.append(list(cells))
        return {c: 500 for c in cells}

    app = App(fullscreen=False, data_dir=tmp_path, replay=False, find_ground=ground)
    try:
        now = time.monotonic()
        planes = [heard(f"f{i}", 35.0, -80.6, h, now) for i, h in enumerate((600, 700, 800))]
        app.store.set_aircraft("public", planes, now)
        app.draw(now)  # learns from the feed, and asks for the ground under it
        deadline = time.monotonic() + 2
        while not app.reach.counts()[2] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert asked == [[reach.cell_of(35.0, -80.6)]]
        assert app.reach.floors(34.9, -80.7, 35.1, -80.5)[0][1] == 200, "700 MSL, 500 ground"
        assert any("Coverage: 1 of 1, 1 AGL" in line for line in app.diagnostics(now))
        assert not any(o.key[0] == "reach" for o in app.overlays())
        app.show_diagnostics = True
        overlay = next(o for o in app.overlays() if o.key[0] == "reach")
        assert overlay is next(o for o in app.overlays() if o.key[0] == "reach"), "cached"
        surface = pygame.Surface((400, 400), pygame.SRCALPHA)
        overlay.paint(surface, (35.0, -80.6), app.zoom())
        assert surface.get_at((200, 200)).a > 0, "the cell under the center is tinted"
        app.draw(now)  # with the key along the bottom of the map
    finally:
        pygame.quit()


def test_the_app_judges_by_the_sources_that_have_answered_lately(tmp_path):
    from flighttrack import settings as settings_module
    from flighttrack.app import App
    from flighttrack.feeds.base import STALE_AFTER_S

    settings_module.save(
        settings_module.Settings(home_lat=34.2, home_lon=-79.7), tmp_path / "settings.json"
    )
    kflo = airports.find("KFLO")
    near = destination(kflo.lat, kflo.lon, 270, 1.5)
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    try:
        now = time.monotonic()
        for i, h in enumerate((1500, 1600, 1800)):
            app._on_answer("adsb.fi", [heard(f"p{i}", *near, h, now)], now)
        for i in range(3):
            app._on_answer("local", [heard(f"q{i}", *near, None, now, ground=True)], now)
        point = (*near, 1700, now)
        assert not app._low_beside(point), "the local receiver would have heard it lower"
        app._answered["local"] = now - STALE_AFTER_S - 1  # the receiver has gone quiet
        assert app._low_beside(point), "only the public network listening: out of hearing"
    finally:
        pygame.quit()

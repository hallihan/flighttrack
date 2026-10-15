"""Follow mode, label placement, airspace styling, airport search speed, and the work done
per aircraft."""

import random
import time

import pygame
import pytest

from flighttrack import airports
from flighttrack import app as app_module
from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.feeds.base import Aircraft
from flighttrack.geo import bearing_deg, distance_nm
from flighttrack.geo.mercator import offset_px, projector, shifted
from flighttrack.ui import overlays, panel, scope, theme

HOME = (51.0, -1.0)


@pytest.fixture
def app(tmp_path):
    settings_module.save(
        settings_module.Settings(home_lat=HOME[0], home_lon=HOME[1], range_nm=10),
        tmp_path / "settings.json",
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    yield app
    pygame.quit()


def _plane(app, hex_code, dx, dy, **more):
    lat, lon = shifted(app.center, dx, dy, app.zoom())
    fields = {"callsign": hex_code.upper(), "altitude_ft": 20000, "track_deg": 90} | more
    return Aircraft(hex_code, lat, lon, time.monotonic(), **fields)


def _show(app, planes):
    app.store.set_aircraft("public", planes, time.monotonic())
    app.draw(time.monotonic())


# --- following ---------------------------------------------------------------------------


def test_while_following_distance_is_from_home_and_the_map_says_who(app, monkeypatch):
    lat, lon = HOME[0] + 0.1, HOME[1] + 0.1
    plane = Aircraft("abc123", lat, lon, time.monotonic(), callsign="TST1", altitude_ft=9000)
    app.store.set_aircraft("public", [plane], time.monotonic())
    app._select("abc123")

    seen = {}
    drawn = panel.draw_selected

    def spy(*args, **kwargs):
        seen["panel"] = (args, kwargs)
        return drawn(*args, **kwargs)

    scenes = []
    draw_scope = app_module.draw_scope
    monkeypatch.setattr(app_module.panel, "draw_selected", spy)
    monkeypatch.setattr(
        app_module,
        "draw_scope",
        lambda surface, view, radius, fonts, scene, *rest: (
            scenes.append(scene),
            draw_scope(surface, view, radius, fonts, scene, *rest),
        )[1],
    )

    app.draw(time.monotonic())
    assert scenes[-1].note is None
    assert "DST" in seen["panel"][0] and "HOME" not in seen["panel"][0]

    app.toggle_following()
    app.draw(time.monotonic())
    assert scenes[-1].note == "FOLLOWING TST1"
    args, kwargs = seen["panel"]
    assert "HOME" in args and "DST" not in args
    values = [v for v in (*args, *kwargs.values()) if isinstance(v, float)]
    expected = distance_nm(*HOME, lat, lon)
    assert expected > 5 and any(abs(v - expected) < 0.05 for v in values), values
    assert any(abs(v - bearing_deg(*HOME, lat, lon)) < 0.5 for v in values), values

    app.stop_following()
    app.draw(time.monotonic())
    assert scenes[-1].note is None


# --- labels ------------------------------------------------------------------------------


class _Recording(pygame.Surface):
    """A surface that notes where each image was put on it."""

    def blit(self, image, dest, *rest, **more):
        self.put.append((image, image.get_rect(topleft=tuple(dest)[:2])))
        return super().blit(image, dest, *rest, **more)


def _label_boxes(app, planes, monkeypatch, obstacles=None):
    """Draw, and return where each aircraft's label was put, by its text."""
    labels = {p.callsign for p in planes}
    rendered = {}
    shadowed = theme.shadowed_text

    def remember(font, text, color):
        image = shadowed(font, text, color)
        if text in labels:
            rendered[id(image)] = text
        return image

    monkeypatch.setattr(scope.theme, "shadowed_text", remember)
    if obstacles is not None:
        monkeypatch.setattr(app, "_label_obstacles", lambda: tuple(obstacles))

    canvas = _Recording(app.screen.get_size())
    canvas.put = []
    real_draw_scope = app_module.draw_scope
    monkeypatch.setattr(
        app_module, "draw_scope", lambda surface, *rest: real_draw_scope(canvas, *rest)
    )
    _show(app, planes)
    return {rendered[id(image)]: box for image, box in canvas.put if id(image) in rendered}


def test_a_label_goes_left_when_the_right_is_off_the_edge_or_taken(app, monkeypatch):
    view = app.layout.view
    middle = _plane(app, "mid001", 0, -100)
    edge = _plane(app, "edge01", view.width // 2 - 12, -40)
    blocked = _plane(app, "blk001", -150, 60)
    bx, by = view.centerx - 150, view.centery + 60
    wall = pygame.Rect(bx + 10, by - 20, 120, 40)  # something painted just to its right
    boxes = _label_boxes(app, [middle, edge, blocked], monkeypatch, obstacles=[wall])

    assert boxes["MID001"].left > view.centerx, "to the right when there is room"
    edge_x = app.positions["edge01"][0]
    assert boxes["EDGE01"].right < edge_x and boxes["EDGE01"].right <= view.right
    assert boxes["BLK001"].right < bx and not boxes["BLK001"].colliderect(wall)


def test_a_label_with_nowhere_to_go_is_left_out_unless_its_aircraft_is_selected(app, monkeypatch):
    view = app.layout.view
    plane = _plane(app, "box001", 0, -100)
    x, y = view.centerx, view.centery - 100
    walls = [pygame.Rect(x - 200, y - 30, 400, 60)]
    assert "BOX001" not in _label_boxes(app, [plane], monkeypatch, obstacles=walls)
    app._select("box001")
    app.draw(time.monotonic())


def test_labels_keep_clear_of_airport_names_and_the_buttons_on_the_map(app):
    rects = app._label_obstacles()
    on_the_map = (app.range_down, app.range_up, app.go_button, app.settings_button)
    assert all(button.rect in rects for button in on_the_map)
    assert app.home_button.rect not in rects
    marked = [
        a
        for a in app.visible_airports()
        if app.layout.view.collidepoint(
            scope.screen_xy(app.center, a.lat, a.lon, app.zoom(), app.layout.view)
        )
    ]
    assert marked and len(rects) == len(on_the_map) + len(marked)
    assert app._label_obstacles() is rects, "worked out once for a view that has not moved"

    app.pan(40, 0)
    moved = app._label_obstacles()
    assert moved is not rects and app.home_button.rect in moved


def test_only_the_bigger_airports_are_marked_beyond_ten_miles(app):
    def sizes(range_nm):
        app.settings.range_nm = range_nm
        return {a.size for a in app.visible_airports()}

    assert "S" in sizes(10)
    assert sizes(15) <= {"L", "M"} and sizes(25) <= {"L", "M"}


# --- colors -----------------------------------------------------------------------------


def _apart(one, other):
    return sum(abs(a - b) for a, b in zip(one[:3], other[:3], strict=True))


def test_airspace_lines_cannot_be_mistaken_for_aircraft():
    from flighttrack.themes import THEMES

    for name, colors in THEMES.items():
        traffic = [
            colors[role] for role in ("AIRCRAFT_LOW", "AIRCRAFT_MID", "AIRCRAFT_HIGH", "FAVORITE")
        ] + [theme.AIRCRAFT_SELECTED]
        for cls, (color, _width, _dashed) in theme.AIRSPACE.items():
            nearest = min(_apart(color, c) for c in traffic)
            assert nearest >= 150, f"{name}: class {cls} is {nearest} from an aircraft color"
            assert sum(color[:3]) < 420, f"class {cls} should be duller than the traffic"
    assert all(_apart(theme.AIRSPACE_LABEL, c) >= 90 for c in traffic)
    assert theme.AIRSPACE["D"][2] and not theme.AIRSPACE["B"][2] and not theme.AIRSPACE["C"][2]


def test_a_dashed_line_has_gaps_and_keeps_its_rhythm_round_corners():
    pygame.init()
    surface = pygame.Surface((200, 60), pygame.SRCALPHA)
    # One straight run, cut into short pieces as a curve's outline would be.
    points = [(10 + i * 4, 30) for i in range(46)]
    overlays._dashes(surface, (255, 255, 255, 255), points, 1, dash=9.0, gap=6.0)
    row = [surface.get_at((x, 30))[3] > 0 for x in range(10, 190)]
    runs, current = [], 1
    for before, after in zip(row, row[1:], strict=False):
        if after == before:
            current += 1
        else:
            runs.append((before, current))
            current = 1
    inked = [length for on, length in runs if on]
    gaps = [length for on, length in runs if not on]
    assert len(inked) >= 10 and len(gaps) >= 10
    assert all(8 <= n <= 11 for n in inked[1:]), inked
    assert all(4 <= n <= 7 for n in gaps), gaps


# --- airport search ----------------------------------------------------------------------


def _slow_search(query, near=None, limit=5):
    """The search as first written: every airport looked at, then sorted."""
    text = " ".join(query.upper().split())
    if not text:
        return ()
    first_word = text.split()[0]
    scored = []
    for entry in airports._entries():
        airport = entry.airport
        if text in entry.codes:
            score = 0
        elif any(code.startswith(text) for code in entry.codes):
            score = 1
        elif entry.text.startswith(text) or (
            " " not in text and any(word.startswith(text) for word in entry.words)
        ):
            score = 2
        elif len(text) >= 3 and text in entry.text:
            score = 3
        elif len(text) >= 3 and first_word in entry.words:
            score = 4
        else:
            continue
        distance = distance_nm(*near, airport.lat, airport.lon) if near else 0.0
        scored.append((score, airports._SIZE_RANK.get(airport.size, 2), distance, airport.ident))
    scored.sort()
    return tuple(item[3] for item in scored[:limit])


QUERIES = (
    "L LH LHR EGLL EG K KJ KJF JFK O OR ORD A AI AIR AIRP AIRPORT INT INTER INTERNATIONAL "
    "LON LOND LONDON HEATH HEATHROW SAN SAN F SAN FRANCISCO NEW NEW YORK ST ST LOUIS MUN "
    "MUNICIPAL REGIONAL FIELD LAKE SEAPLANE HELI 1 12 0A 0A1 X XX ZZZZ Q QUEEN PARIS CHARLES "
    "DE GAULLE TOKYO NARITA SYD SYDNEY B BO BOS BOST"
).split(" ")
PHRASES = ("SAN F", "SAN FRANCISCO", "NEW YORK", "ST LOUIS", "DE GAULLE", "lake  placid")


@pytest.mark.parametrize("near", [None, (40.6, -73.8)])
def test_the_fast_search_finds_exactly_what_looking_at_every_airport_would(near):
    for query in (*QUERIES, *PHRASES):
        for limit in (5, 8):
            fast = tuple(a.ident for a in airports.search(query, near, limit))
            assert fast == _slow_search(query, near, limit), (query, near, limit)


def test_a_keystroke_of_airport_search_is_quick():
    airports.warm()
    airports.search.cache_clear()
    worst = 0.0
    for query in QUERIES:
        started = time.perf_counter()
        airports.search(query, (40.6, -73.8), 5)
        worst = max(worst, time.perf_counter() - started)
    # About 10 ms here; the scan of every airport took 35. Loose, for a slow test machine.
    assert worst < 0.06, f"the slowest search took {worst * 1000:.0f} ms"


def test_the_airport_table_is_read_once(monkeypatch):
    import gzip

    opened = []
    real = gzip.GzipFile

    def counting(*args, **kwargs):
        opened.append(1)
        return real(*args, **kwargs)

    for table in (airports._entries, airports._by_code, airports._haystack):
        table.cache_clear()
    airports._by_initial.cache_clear()
    airports._grid.cache_clear()
    monkeypatch.setattr(gzip, "GzipFile", counting)
    airports.warm()
    assert airports.find("EGLL") and airports.search("heath") and airports.near(51.5, -0.5, 10)
    assert len(opened) == 1


# --- work per aircraft -------------------------------------------------------------------


def test_the_quick_projection_agrees_with_the_careful_one():
    rng = random.Random(3)
    for _ in range(20):
        center = (rng.uniform(-70, 70), rng.uniform(-179, 179))
        zoom = rng.uniform(4, 13)
        to_screen = projector(center, zoom, 300, 240)
        for _ in range(50):
            lat = center[0] + rng.uniform(-3, 3)
            lon = center[1] + rng.uniform(-3, 3)
            dx, dy = offset_px(center, lat, lon, zoom)
            assert to_screen(lat, lon) == (round(300 + dx), round(240 + dy))


def _many(app, count=300):
    rng = random.Random(5)
    planes = [
        _plane(
            app,
            f"{i:06x}",
            rng.uniform(-250, 250),
            rng.uniform(-180, 180),
            callsign=f"T{i}",
            ground_speed_kt=400,
        )
        for i in range(count)
    ]
    app.store.set_aircraft("public", planes, time.monotonic())
    return planes


def test_how_an_aircraft_looks_is_worked_out_once_for_each_answer_from_the_feed(app, monkeypatch):
    calls = []
    real = app_module.look
    monkeypatch.setattr(app_module, "look", lambda *a: (calls.append(1), real(*a))[1])
    planes = _many(app)
    base = time.monotonic()
    for frame in range(10):
        app.draw(base + frame / 30)
    assert len(calls) == len(planes)

    app.store.set_aircraft("public", planes[:100], time.monotonic())  # the feed answers again
    app.draw(base + 1)
    assert len(calls) == len(planes) + 100

    app.favorite_keys = frozenset({"T7"})  # so does a change to the favorites
    app.draw(base + 1.1)
    assert len(calls) == len(planes) + 200
    drawn = {d.aircraft.callsign: d for d in app._moving}
    assert drawn["T7"].favorite and drawn["T7"].color == theme.FAVORITE
    assert not drawn["T8"].favorite


def test_positions_are_reworked_every_frame_close_in_but_held_briefly_at_regional_ranges(
    app, monkeypatch
):
    _many(app)
    base = 1000.0 + time.monotonic()

    def lists_over_a_second(range_nm):
        app.settings.range_nm = range_nm
        seen = []
        for frame in range(15):
            app.draw(base + frame / 15)
            if not seen or seen[-1] is not app._moving:
                seen.append(app._moving)
        return seen

    assert len(lists_over_a_second(10)) == 15
    held = lists_over_a_second(250)
    assert 2 <= len(held) <= 4, "about twice a second"
    assert all(a.distance_nm <= b.distance_nm for a, b in zip(held[-1], held[-1][1:], strict=False))


def test_a_wide_view_stops_laying_out_labels_once_it_has_enough(app, monkeypatch):
    app.settings.range_nm = 250
    planes = _many(app, 400)
    rendered = []
    real = theme.shadowed_text
    monkeypatch.setattr(
        scope.theme,
        "shadowed_text",
        lambda font, text, c: (rendered.append(text), real(font, text, c))[1],
    )
    app.draw(time.monotonic())
    names = [text for text in rendered if text in {p.callsign for p in planes}]
    assert 0 < len(names) < len(planes) / 2, len(names)


# --- the legend's pills ------------------------------------------------------------------


def _tap(app, pos):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        app.handle_event(pygame.event.Event(kind, pos=pos, button=1))


def _mixed(app):
    planes = [
        _plane(app, "low001", -120, -60, altitude_ft=4000),
        _plane(app, "mid001", -40, -60, altitude_ft=20000),
        _plane(app, "high01", 40, -60, altitude_ft=36000),
        _plane(app, "fav001", 120, -60, altitude_ft=36000),
        _plane(app, "gnd001", 0, 60, altitude_ft=0, on_ground=True),
        _plane(app, "mayday", 80, 60, altitude_ft=20000, emergency=True),
    ]
    app.favorite_keys = frozenset({"FAV001"})
    _show(app, planes)
    return planes


def test_each_legend_pill_hides_and_shows_its_own_traffic_and_is_remembered(app, tmp_path):
    _mixed(app)
    everything = {"low001", "mid001", "high01", "fav001", "gnd001", "mayday"}
    assert set(app.positions) == everything
    mine = {"low": "low001", "mid": "mid001", "high": "high01", "fav": "fav001"}
    assert list(app.filter_pills) == list(mine)

    for group, hex_code in mine.items():
        pill = app.filter_pills[group]
        assert pill in app.buttons and pill.active
        assert pill.rect.height >= theme.MIN_TOUCH_PX and pill.rect.width >= 44
        _tap(app, pill.rect.center)
        app.draw(time.monotonic())
        assert not pill.active and app.settings.hidden_traffic == [group]
        assert set(app.positions) == everything - {hex_code}
        assert hex_code not in {listed for _, listed in app.list_targets}
        assert settings_module.load(tmp_path / "settings.json").hidden_traffic == [group]
        _tap(app, pill.rect.center)
        app.draw(time.monotonic())
        assert pill.active and set(app.positions) == everything

    # All four off: what has no pill is still there. An emergency is never hidden.
    for pill in app.filter_pills.values():
        _tap(app, pill.rect.center)
    app.draw(time.monotonic())
    assert set(app.positions) == {"gnd001", "mayday"}
    assert app.settings.hidden_traffic == ["low", "mid", "high", "fav"]


def test_a_favorite_belongs_to_the_favorites_pill_whatever_its_altitude(app):
    _mixed(app)
    app.toggle_traffic("high")
    app.draw(time.monotonic())
    assert "fav001" in app.positions and "high01" not in app.positions


def test_the_selected_aircraft_stays_when_its_group_is_hidden(app):
    _mixed(app)
    app._select("mid001")
    app.toggle_traffic("mid")
    app.draw(time.monotonic())
    assert app.selected_hex == "mid001" and "mid001" in app.positions
    app.show_list()
    app.draw(time.monotonic())
    assert "mid001" not in app.positions


def test_the_pills_and_the_traffic_summary_never_overlap(app):
    from flighttrack.ui import bars

    top = app.layout.top
    pills = list(app.filter_pills.values())
    assert all(a.rect.right <= b.rect.left for a, b in zip(pills, pills[1:], strict=False))
    # Set apart from the chips that say what the map shows.
    assert pills[0].rect.left - app.airspace_toggle.rect.right >= 16
    assert all(pill.rect.width >= 48 and top.contains(pill.rect) for pill in pills)
    room = top.right - bars.PADDING - app.controls_right - 6
    for count, age, demo in [
        (9, 3.0, False),
        (250, 15.0, False),
        (12, 3.0, True),
        (250, 59.0, True),
    ]:
        ways = bars.top_summaries(count, age, demo)
        assert any(app.fonts.body.size(way)[0] <= room for way in ways), (ways, room)
    assert app.fonts.body.size(bars.top_summaries(99, 9.0, False)[0])[0] <= room, (
        "the usual case in full"
    )


# --- labels that stay put ----------------------------------------------------------------


class _Watch:
    """Draws the app's map onto a recording surface and reports where each aircraft's name
    was put in that frame, and how strongly (1.0 unless it is fading out)."""

    def __init__(self, app, monkeypatch, planes):
        self.app, self.walls = app, []
        self.names = {p.callsign for p in planes}
        self.rendered = {}
        shadowed, faded = theme.shadowed_text, theme.faded_text

        def remember(font, text, color):
            image = shadowed(font, text, color)
            if text in self.names:
                self.rendered[id(image)] = (text, 1.0, image)
            return image

        def remember_faded(font, text, color, strength):
            image = faded(font, text, color, strength)
            if text in self.names:
                self.rendered[id(image)] = (text, strength, image)
            return image

        monkeypatch.setattr(scope.theme, "shadowed_text", remember)
        monkeypatch.setattr(scope.theme, "faded_text", remember_faded)
        monkeypatch.setattr(app, "_label_obstacles", lambda: tuple(self.walls))
        self.canvas = _Recording(app.screen.get_size())
        real = app_module.draw_scope
        monkeypatch.setattr(
            app_module, "draw_scope", lambda surface, *rest: real(self.canvas, *rest)
        )
        app.store.set_aircraft("public", planes, time.monotonic())

    def draw(self, now):
        self.canvas.put = []
        self.app.draw(now)
        found = {}
        for image, box in self.canvas.put:
            if id(image) in self.rendered:
                text, strength, _ = self.rendered[id(image)]
                found[text] = (box, strength)
        return found


def _right_of(app, dx, dy, width=120):
    """Something painted on the map just to the right of the point dx, dy from the middle."""
    view = app.layout.view
    return pygame.Rect(view.centerx + dx + 10, view.centery + dy - 20, width, 40)


def _left_of(app, dx, dy, width=120):
    view = app.layout.view
    return pygame.Rect(view.centerx + dx - 10 - width, view.centery + dy - 20, width, 40)


def test_a_label_keeps_the_side_it_has_and_does_not_go_back_when_the_other_frees_up(
    app, monkeypatch
):
    plane = _plane(app, "one001", -100, -80)
    watch = _Watch(app, monkeypatch, [plane])
    x = app.layout.view.centerx - 100
    base = time.monotonic()

    assert watch.draw(base)["ONE001"][0].left > x, "to the right to begin with"
    watch.walls = [_right_of(app, -100, -80)]
    for frame in range(1, 20):
        watch.draw(base + frame / 30)
    assert watch.draw(base + 1)["ONE001"][0].right < x, "the right is taken: it is on the left"
    # The right frees up again. Nothing is wrong with the left, so there it stays.
    watch.walls = []
    for frame in range(60):
        box, strength = watch.draw(base + 1 + frame / 10)["ONE001"]
        assert box.right < x and strength == 1.0


def test_a_change_of_side_is_swept_across_and_not_repeated_for_a_while(app, monkeypatch):
    plane = _plane(app, "one001", -100, -80)
    watch = _Watch(app, monkeypatch, [plane])
    x = app.layout.view.centerx - 100
    base = time.monotonic()
    start = watch.draw(base)["ONE001"][0].left
    assert start > x

    # Something takes the right. The label sets out from where it is, not from the far side.
    watch.walls = [_right_of(app, -100, -80)]
    t0 = base + 1.0
    lefts = [watch.draw(t0 + step * scope.LABEL_SWEEP_S / 6)["ONE001"][0].left for step in range(8)]
    assert lefts[0] == start, "the first frame of the move is where it already was"
    assert all(a >= b for a, b in zip(lefts, lefts[1:], strict=False)), "one steady sweep"
    assert len(set(lefts)) >= 5, "over several frames, not in a jump"
    end = lefts[-1]
    assert end < x and lefts[-2] == end, "and it arrives"
    # The screen keeps its full frame rate until the sweep is over, touched or not.
    assert app._active_until >= t0 + scope.LABEL_SWEEP_S
    assert app.frame_rate(t0 + 0.1) == app_module.FRAME_RATE

    # Now the left is taken and the right is free. It changed a moment ago, so it does not
    # swing straight back: it gives up its place (fading, see below) until the hold is over.
    watch.walls = [_left_of(app, -100, -80)]
    moment = 1.0
    while moment < scope.LABEL_HOLD_S - 0.1:
        seen = watch.draw(t0 + moment).get("ONE001")
        assert seen is None or (seen[0].right < x and seen[1] < 1.0), "fading, still on the left"
        moment += 0.1
    assert seen is None, "and gone well before the hold is over"
    for frame in range(12):  # it comes back, fading in over a few frames
        back = watch.draw(t0 + scope.LABEL_HOLD_S + 0.1 + frame / 30)["ONE001"]
    assert back[0].left > x and back[1] == 1.0, "after the hold, to the side that is free"


def test_a_label_that_loses_its_place_fades_out_over_a_few_frames(app, monkeypatch):
    plane = _plane(app, "one001", -100, -80)
    watch = _Watch(app, monkeypatch, [plane])
    base = time.monotonic()
    box, strength = watch.draw(base)["ONE001"]
    assert strength == 1.0

    watch.walls = [_right_of(app, -100, -80), _left_of(app, -100, -80)]  # nowhere to go
    t0 = base + 1.0
    strengths = []
    for step in range(9):
        seen = watch.draw(t0 + step * scope.LABEL_FADE_S / 6).get("ONE001")
        strengths.append(seen[1] if seen else 0.0)
        if seen:
            assert seen[0].topleft == box.topleft, "it fades where it was"
    assert strengths[0] < 1.0 and strengths[-1] == 0.0
    assert all(a >= b for a, b in zip(strengths, strengths[1:], strict=False))
    assert len({s for s in strengths if 0 < s < 1}) >= 3, "in steps, not all at once"
    assert app._active_until >= t0 + scope.LABEL_FADE_S * 0.5

    # When there is room again it fades back in the same way, where it belongs.
    watch.walls = []
    t1 = t0 + 10
    rising = []
    for step in range(9):
        seen = watch.draw(t1 + step * scope.LABEL_FADE_S / 6).get("ONE001")
        rising.append(seen[1] if seen else 0.0)
        if seen:
            assert seen[0].topleft == box.topleft
    assert rising[0] < 1.0 and rising[-1] == 1.0
    assert all(a <= b for a, b in zip(rising, rising[1:], strict=False))
    assert len({s for s in rising if 0 < s < 1}) >= 3
    assert app.frame_rate(t1 + 0.05) == app_module.FRAME_RATE, "smoothly, touched or not"

    # The selected aircraft's label is never taken away, wherever it has to go.
    watch.walls = [_right_of(app, -100, -80), _left_of(app, -100, -80)]
    app._select("one001")
    for moment in (20, 21, 25):
        assert watch.draw(t1 + moment)["ONE001"][1] == 1.0


def test_what_is_there_when_the_map_first_appears_is_simply_there(app, monkeypatch):
    plane = _plane(app, "one001", -100, -80)
    watch = _Watch(app, monkeypatch, [plane])
    assert watch.draw(time.monotonic())["ONE001"][1] == 1.0, "no fade on the first frame"


def test_two_aircraft_side_by_side_do_not_make_each_others_labels_jump(app, monkeypatch):
    # Abreast, close enough that the left one's label on the right would run into the
    # right one's icon label, and taking turns at being nearer the middle of the view:
    # an arrangement that would flip the labels from side to side.
    left = _plane(app, "left01", -30, -90)
    right = _plane(app, "right1", 30, -90)
    watch = _Watch(app, monkeypatch, [left, right])
    base = time.monotonic()
    first = watch.draw(base)
    assert set(first) == {"LEFT01", "RIGHT1"}, "there is room for both, one each side"
    assert not first["LEFT01"][0].colliderect(first["RIGHT1"][0])

    def sides(seen):
        return {
            name: box.left > watch.app.positions[name.lower()][0] for name, (box, _) in seen.items()
        }

    settled = sides(first)
    for frame in range(1, 90):
        # Nudge the view a pixel either way, so which of the two is nearer keeps changing.
        app.view_center = shifted(app.home, (-1) ** frame * 2, 0, app.zoom())
        seen = watch.draw(base + frame / 30)
        assert set(seen) == {"LEFT01", "RIGHT1"}, f"frame {frame}: both still labeled"
        assert sides(seen) == settled, f"frame {frame}: a label changed sides"
        assert all(strength == 1.0 for _, strength in seen.values())

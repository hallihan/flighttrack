import io
import json
import time
from pathlib import Path

import pygame
import pytest
import requests

from flighttrack.geo import destination
from flighttrack.geo.mercator import (
    TILE_PX,
    offset_px,
    tiles_covering,
    world_px,
    zoom_for_range,
)
from flighttrack.tiles import IMAGERY, PLACES, TileLayer
from flighttrack.tiles.radar import MAX_ZOOM, latest_layer
from flighttrack.tiles.store import TileStore
from flighttrack.tiles.weather import (
    NWS_COLORS,
    RAINVIEWER_COLORS,
    WeatherSources,
    cloud_layer,
    cloud_satellite,
    in_nexrad_coverage,
    nexrad_layer,
)
from flighttrack.ui import theme
from flighttrack.ui.basemap import MARGIN_PX, RETRY_MISSING_S, Basemap, LayerSpec

FIXTURES = Path(__file__).parent / "fixtures"
INDEX = json.loads((FIXTURES / "rainviewer_index.json").read_text())
HOME = (48.0, 11.0)
TEST_LAYER = TileLayer("test", "http://tiles.example/{z}/{x}/{y}.png", 18, "png", "Test")


def test_world_pixels():
    assert world_px(0, 0, 0) == pytest.approx((128, 128))
    assert world_px(0, -180, 3) == pytest.approx((0, 1024))
    x, y = world_px(85.05112878, 180, 1)
    assert (x, y) == pytest.approx((512, 0), abs=1e-6)
    assert world_px(-89.9, 0, 0)[1] == pytest.approx(256), "latitude is clamped to the map"


def test_zoom_for_range_makes_the_range_span_the_ring():
    zoom = zoom_for_range(HOME[0], 10, 192)
    dx, dy = offset_px(HOME, *destination(*HOME, 0, 10), zoom)
    # Within a pixel: Mercator stretches slightly with latitude across the view.
    assert (dx, dy) == pytest.approx((0, -192), abs=1)
    dx, dy = offset_px(HOME, *destination(*HOME, 90, 10), zoom)
    assert (dx, dy) == pytest.approx((192, 0), abs=1)
    # Halving the range is exactly one zoom level closer.
    assert zoom_for_range(HOME[0], 5, 192) == pytest.approx(zoom + 1)


def test_offsets_take_the_short_way_across_the_antimeridian():
    dx, _ = offset_px((0.0, 179.9), 0.0, -179.9, 8)
    assert 0 < dx < 100


def test_tiles_covering_a_view():
    scale, (cx, cy), columns, rows = tiles_covering(HOME, 10.3, 10, 592, 400)
    assert scale == pytest.approx(2**0.3)
    assert (cx, cy) == pytest.approx(world_px(*HOME, 10))
    assert 2 <= len(columns) <= 4 and 2 <= len(rows) <= 3
    # The tiles span at least the view, measured in tile-level pixels.
    assert (
        columns[0] * TILE_PX <= cx - 296 / scale and (columns[-1] + 1) * TILE_PX >= cx + 296 / scale
    )
    assert rows[0] * TILE_PX <= cy - 200 / scale and (rows[-1] + 1) * TILE_PX >= cy + 200 / scale


def test_tiles_covering_clamps_rows_at_the_poles_and_lets_columns_wrap():
    _, _, columns, rows = tiles_covering((84.0, 179.5), 2.0, 2, 592, 400)
    assert rows[0] == 0 and rows[-1] <= 3
    assert columns[-1] >= 4, "columns past the edge are wrapped by the caller"


def test_layer_urls_and_zoom_choice():
    assert IMAGERY.url(11, 1023, 681).endswith("/World_Imagery/MapServer/tile/11/681/1023")
    assert PLACES.url(5, 1, 2).endswith("/tile/5/2/1")
    assert IMAGERY.zoom_for(11.4) == 11 and IMAGERY.zoom_for(11.6) == 12
    assert IMAGERY.zoom_for(25) == IMAGERY.max_zoom and IMAGERY.zoom_for(-3) == 0


def test_latest_radar_layer_from_a_recorded_index():
    layer = latest_layer(INDEX)
    newest = INDEX["radar"]["past"][-1]
    assert layer.name == f"radar-rainviewer-{newest['time']}"
    assert layer.url(5, 15, 10) == f"{INDEX['host']}{newest['path']}/256/5/15/10/2/1_1.png"
    assert layer.max_zoom == MAX_ZOOM == 7 and layer.attribution == "RainViewer"
    assert layer.zoom_for(11.4) == 7, "close views reuse the deepest radar level"


@pytest.mark.parametrize(
    "index",
    [
        {},
        {"host": "https://x", "radar": {"past": []}},
        {"host": "http://x", "radar": {"past": [{"time": 1, "path": "/p"}]}},
    ],
)
def test_bad_radar_indexes_are_rejected(index):
    with pytest.raises((KeyError, IndexError, ValueError)):
        latest_layer(index)


class FakeIndexSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def get(self, url, headers, timeout):
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode()
        return response


def test_weather_source_selection_by_place():
    assert in_nexrad_coverage(39.0, -98.0) and not in_nexrad_coverage(51.5, -0.1)
    assert not in_nexrad_coverage(21.3, -157.9), "Hawaii is outside the composite"
    assert cloud_satellite(-98.0) == "GOES-East_ABI"
    assert cloud_satellite(-150.0) == "GOES-West_ABI"
    assert cloud_satellite(139.7) == "Himawari_AHI"
    assert cloud_satellite(2.3) is None, "no GeoColor product over Europe"


def test_weather_layers_roll_over_with_time():
    first, same, later = nexrad_layer(1000.0), nexrad_layer(1100.0), nexrad_layer(1300.0)
    assert first.name == same.name != later.name
    assert first.url(6, 17, 25).endswith("/nexrad-n0q-900913/6/17/25.png")

    clouds = cloud_layer(-98.0, 1_800_000_000.0)
    assert clouds.name == "clouds-1799997600"
    assert "/GOES-East_ABI_GeoColor/default/2027-01-15T07:20:00Z/" in clouds.url(5, 7, 12)
    assert clouds.url(5, 7, 12).endswith("/GoogleMapsCompatible_Level7/5/12/7.png")
    assert cloud_layer(2.3, 1_800_000_000.0) is None


def test_weather_sources_in_the_us_need_no_lookup_and_report_replaced_layers():
    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("no request expected inside NEXRAD coverage")

    now = [1_800_000_000.0]
    changes = []
    sources = WeatherSources(
        lambda: (39.0, -98.0),
        on_change=lambda prefix, keep: changes.append((prefix, keep)),
        session=NoNetwork(),
        clock=lambda: now[0],
    )
    sources.refresh()
    weather = sources.current
    assert weather.radar.attribution == "IEM" and weather.clouds.attribution == "NASA"
    assert weather.radar_colors is NWS_COLORS
    assert changes == [("radar-", weather.radar.name), ("clouds-", weather.clouds.name)]

    sources.refresh()
    assert len(changes) == 2, "nothing new, nothing reported"
    now[0] += 300
    sources.refresh()
    assert changes[-1] == ("radar-", sources.current.radar.name) and len(changes) == 3


def test_weather_sources_elsewhere_use_rainviewer_and_survive_its_failures():
    now = [1_800_000_000.0]
    session = FakeIndexSession([requests.ConnectionError("down"), INDEX])
    sources = WeatherSources(lambda: (51.5, -0.1), session=session, clock=lambda: now[0])
    sources.refresh()
    assert sources.current.radar is None and sources.current.clouds is None
    sources.refresh()
    assert sources.current.radar is None, "the index is not re-read on every check"
    now[0] += 301
    sources.refresh()
    assert sources.current.radar.name == latest_layer(INDEX).name
    assert sources.current.radar_colors is RAINVIEWER_COLORS


def _png(color, size=(TILE_PX, TILE_PX)) -> bytes:
    surface = pygame.Surface(size)
    surface.fill(color)
    buffer = io.BytesIO()
    pygame.image.save(surface, buffer, "tile.png")
    return buffer.getvalue()


def _wait_for(store, count=1, timeout=3.0):
    arrived, deadline = [], time.monotonic() + timeout
    while len(arrived) < count and time.monotonic() < deadline:
        arrived.extend(store.drain())
        time.sleep(0.01)
    return arrived


def test_store_fetches_once_then_serves_from_disk(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return _png((10, 20, 30))

    store = TileStore(tmp_path, workers=1, fetch=fetch)
    store.start()
    try:
        store.request(TEST_LAYER, 3, 4, 5)
        store.request(TEST_LAYER, 3, 4, 5)  # already on its way: not fetched twice
        ((key, data),) = _wait_for(store)
        assert key == ("test", 3, 4, 5) and data == _png((10, 20, 30))
        assert calls == ["http://tiles.example/3/4/5.png"]
        assert store.path(TEST_LAYER, 3, 4, 5).read_bytes() == data
    finally:
        store.stop()

    def offline(url):
        raise requests.ConnectionError("offline")

    cached = TileStore(tmp_path, workers=1, fetch=offline)
    cached.start()
    try:
        cached.request(TEST_LAYER, 3, 4, 5)
        assert len(_wait_for(cached)) == 1, "a cached tile needs no network"
    finally:
        cached.stop()


def test_store_ignores_non_images_and_failures_and_does_not_hammer(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/1.png"):
            return b"<html>Service unavailable</html>"
        if url.endswith("/2.png"):
            return None  # 404
        raise requests.ConnectionError("down")

    store = TileStore(tmp_path, workers=1, fetch=fetch)
    store.start()
    try:
        for y in (1, 2, 3):
            store.request(TEST_LAYER, 0, 0, y)
        assert _wait_for(store, timeout=0.5) == []
        for y in (1, 2, 3):
            store.request(TEST_LAYER, 0, 0, y)  # recently failed: not retried yet
        assert _wait_for(store, timeout=0.3) == []
        assert len(calls) == 3
        # A tile the server says does not exist is left alone far longer than one that
        # merely failed to arrive.
        waits = {key[3]: leave for key, (_, leave) in store._missing.items()}
        assert waits[2] > waits[3] >= 1
        assert not any(tmp_path.rglob("*.png")), "nothing bad was cached"
    finally:
        store.stop()


def test_prune_removes_the_least_recently_used_tiles(tmp_path):
    import os

    layer_dir = tmp_path / "test" / "1" / "0"
    layer_dir.mkdir(parents=True)
    for i in range(10):
        path = layer_dir / f"{i}.png"
        path.write_bytes(b"x" * 1000)
        os.utime(path, (1000 + i, 1000 + i))
    store = TileStore(tmp_path, max_bytes=5000, fetch=lambda url: None)
    store.prune()
    left = sorted(int(p.stem) for p in layer_dir.iterdir())
    assert left == [6, 7, 8, 9], "oldest go first, down to 90% of the cap"


def test_forget_layers_removes_old_radar_frames_only(tmp_path):
    for name in ("radar-100", "radar-200", "imagery"):
        (tmp_path / name / "1" / "0").mkdir(parents=True)
        (tmp_path / name / "1" / "0" / "0.png").write_bytes(b"x")
    store = TileStore(tmp_path, workers=1, fetch=lambda url: None)
    store.start()
    try:
        store.forget_layers("radar-", keep="radar-200")
        deadline = time.monotonic() + 2
        while (tmp_path / "radar-100").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["imagery", "radar-200"]
    finally:
        store.stop()


@pytest.fixture
def display():
    pygame.display.init()
    pygame.display.set_mode((800, 480))
    yield
    pygame.quit()


def _settled(basemap, store, layers, zoom=10.0, timeout=3.0):
    """Render until every requested tile has arrived and been composed."""
    deadline = time.monotonic() + timeout
    image, _ = basemap.render(HOME, zoom, layers, time.monotonic())
    while time.monotonic() < deadline:
        time.sleep(0.02)
        basemap.pump()
        image, _ = basemap.render(HOME, zoom, layers, time.monotonic() + 10)
        with store._lock:
            idle = not store._pending
        if idle and not basemap._raw and not basemap._dirty:
            break
    return image


def test_basemap_composes_tiles_darkens_the_base_and_overlays_layers(tmp_path, display):
    colors = {"base": (200, 100, 50), "over": (0, 0, 255)}

    def fetch(url):
        return _png(colors["base" if "base" in url else "over"])

    base = TileLayer("base", "http://t.example/base/{z}/{x}/{y}", 18, "png", "A")
    over = TileLayer("over", "http://t.example/over/{z}/{x}/{y}", 7, "png", "B")
    store = TileStore(tmp_path, fetch=fetch)
    store.start()
    try:
        basemap = Basemap(store, (592, 400))
        shade = (128, 128, 128)
        first, offset = basemap.render(
            HOME, 10.0, (LayerSpec(base, shade=shade),), time.monotonic()
        )
        assert offset == (-MARGIN_PX, -MARGIN_PX), "the picture overhangs the view all round"
        assert first.get_size() == (592 + 2 * MARGIN_PX, 400 + 2 * MARGIN_PX)
        assert first.get_at((296, 200))[:3] == theme.MAP_FILL, "plain fill until tiles arrive"

        image = _settled(basemap, store, (LayerSpec(base, shade=shade),))
        for point in ((0, 0), (296, 200), (591, 399)):
            assert image.get_at(point)[:3] == pytest.approx((100, 50, 25), abs=2)

        # A second layer at half opacity, from a much shallower zoom level, blends over it.
        both = (LayerSpec(base, shade=shade), LayerSpec(over, alpha=128))
        image = _settled(basemap, store, both)
        assert image.get_at((296, 200))[:3] == pytest.approx((50, 25, 140), abs=4)

        # Turning the overlay off again restores the base without refetching.
        image = _settled(basemap, store, (LayerSpec(base, shade=shade),))
        assert image.get_at((296, 200))[:3] == pytest.approx((100, 50, 25), abs=2)
    finally:
        store.stop()


def test_basemap_survives_undecodable_tiles(tmp_path, display):
    store = TileStore(tmp_path, fetch=lambda url: b"\x89PNG\r\n\x1a\n" + b"garbage")
    store.start()
    try:
        basemap = Basemap(store, (592, 400))
        image = _settled(basemap, store, (LayerSpec(TEST_LAYER),), timeout=1.0)
        assert image.get_at((296, 200))[:3] == theme.MAP_FILL
    finally:
        store.stop()


def test_basemap_slides_its_picture_while_panning_and_adds_bright_cloud_only(tmp_path, display):
    from flighttrack.geo.mercator import shifted
    from flighttrack.ui.overlays import Overlay

    def fetch(url):
        return _png((200, 200, 200) if "cloud" in url else (40, 40, 40))

    base = TileLayer("base", "http://t.example/base/{z}/{x}/{y}", 18, "png", "A")
    cloud = TileLayer("cloud", "http://t.example/cloud/{z}/{x}/{y}", 7, "png", "B")
    dark = TileLayer("dark", "http://t.example/dark/{z}/{x}/{y}", 7, "png", "C")
    store = TileStore(tmp_path, fetch=fetch)
    store.start()
    try:
        basemap = Basemap(store, (592, 400))
        layers = (LayerSpec(base), LayerSpec(cloud, shade=(128, 128, 128), add_above=100))
        image = _settled(basemap, store, layers)
        # (200 - 100) * 128/255 = 50 added to the base's 40.
        assert image.get_at((296, 200))[:3] == pytest.approx((90, 90, 90), abs=3)
        # A layer no brighter than the threshold adds nothing at all.
        image = _settled(basemap, store, (LayerSpec(base), LayerSpec(dark, add_above=100)))
        assert image.get_at((296, 200))[:3] == pytest.approx((40, 40, 40), abs=2)

        # Dragged 30 px right and 12 px down, the same picture comes back, shifted to match.
        now = time.monotonic() + 100
        basemap.render(HOME, 10.0, (LayerSpec(base),), now)
        moved = shifted(HOME, -30, -12, 10.0)
        builds = []
        counting = Overlay("count", lambda surface, center, zoom: builds.append(center))
        layers = (LayerSpec(base),)
        basemap.render(HOME, 10.0, layers, now + 1, (counting,))
        assert len(builds) == 1
        _, offset = basemap.render(moved, 10.0, layers, now + 1.01, (counting,))
        assert offset == (30 - MARGIN_PX, 12 - MARGIN_PX)

        # Within the margin the picture is not rebuilt, however long the view sits there or
        # however it wanders: this is what keeps following an aircraft cheap.
        for i in range(1, 40):
            wander = shifted(HOME, -30 - i, -12 + (i % 7), 10.0)
            basemap.render(wander, 10.0, layers, now + 1 + i, (counting,))
        assert len(builds) == 1

        # Past most of the margin it is rebuilt around the new center, and no more often
        # than the throttle allows however fast the view is moving.
        far = shifted(HOME, -(MARGIN_PX - 10), 0, 10.0)
        _, offset = basemap.render(far, 10.0, layers, now + 50, (counting,))
        assert len(builds) == 2 and offset == (-MARGIN_PX, -MARGIN_PX)
        for i in range(1, 6):
            farther = shifted(far, -100 * i, 0, 10.0)
            basemap.render(farther, 10.0, layers, now + 50 + 0.01 * i, (counting,))
        assert len(builds) == 2, "a fast drag does not rebuild every frame"
        basemap.render(farther, 10.0, layers, now + 51, (counting,))
        assert len(builds) == 3

        # An overlay whose key changes is repainted, but that too waits for the throttle.
        changed = Overlay("count-2", counting.paint)
        basemap.render(farther, 10.0, layers, now + 51.01, (changed,))
        assert len(builds) == 3
        basemap.render(farther, 10.0, layers, now + 52, (changed,))
        basemap.render(farther, 10.0, layers, now + 53, (changed,))
        assert len(builds) == 4

        # A new range is different: nothing of the old picture is right, so it is immediate.
        basemap.render(farther, 11.0, layers, now + 53.001, (changed,))
        assert len(builds) == 5
    finally:
        store.stop()


def test_basemap_without_a_tile_store_still_paints_overlays(display):
    from flighttrack.ui.overlays import Overlay

    def paint(surface, center, zoom):
        surface.fill((1, 2, 3))

    basemap = Basemap(None, (100, 80))
    basemap.pump()
    image, _ = basemap.render(HOME, 10.0, (), 0.0, (Overlay("x", paint),))
    assert image.get_at((50, 40))[:3] == (1, 2, 3)


def test_a_weather_layer_keeps_its_last_tiles_until_the_new_version_arrives(tmp_path, display):
    """Weather layers are replaced by a newer version every few minutes. Where the new one
    has nothing yet, or fails, the picture must not go blank."""
    serving = {"color": (0, 0, 200), "fail": False}

    def fetch(url):
        if serving["fail"]:
            raise requests.ConnectionError("down")
        return _png(serving["color"])

    first = TileLayer("clouds-1", "http://t.example/1/{z}/{x}/{y}", 7, "png", "A")
    second = TileLayer("clouds-2", "http://t.example/2/{z}/{x}/{y}", 7, "png", "A")
    third = TileLayer("clouds-3", "http://t.example/3/{z}/{x}/{y}", 7, "png", "A")
    store = TileStore(tmp_path, fetch=fetch)
    store.start()
    try:
        basemap = Basemap(store, (592, 400))
        image = _settled(basemap, store, (LayerSpec(first, family="clouds"),), zoom=7.0)
        assert image.get_at((296, 200))[:3] == (0, 0, 200)

        # The next version cannot be fetched: the old tiles stay on screen.
        serving["fail"] = True
        image, _ = basemap.render(
            HOME, 7.0, (LayerSpec(second, family="clouds"),), time.monotonic() + 50
        )
        assert image.get_at((296, 200))[:3] == (0, 0, 200), "kept at once, no blank frame"
        image = _settled(
            basemap, store, (LayerSpec(second, family="clouds"),), zoom=7.0, timeout=0.6
        )
        assert image.get_at((296, 200))[:3] == (0, 0, 200), "and kept while it keeps failing"

        # A later version does arrive, and replaces them.
        serving.update(fail=False, color=(200, 0, 0))
        image = _settled(basemap, store, (LayerSpec(third, family="clouds"),), zoom=7.0)
        assert image.get_at((296, 200))[:3] == (200, 0, 0)

        # A layer without a family has no such memory.
        plain = TileLayer("other-1", "http://t.example/o/{z}/{x}/{y}", 7, "png", "A")
        serving["fail"] = True
        image = _settled(basemap, store, (LayerSpec(plain),), zoom=7.0, timeout=0.5)
        assert image.get_at((296, 200))[:3] == theme.MAP_FILL
    finally:
        store.stop()


def test_missing_map_tiles_are_asked_for_again_without_anyone_touching_the_screen(
    tmp_path, display, monkeypatch
):
    from flighttrack.tiles import store as store_module

    monkeypatch.setattr(store_module, "RETRY_MISSING_AFTER_S", 0.0)
    online = {"up": False}
    calls = []

    def fetch(url):
        calls.append(url)
        if not online["up"]:
            raise requests.ConnectionError("no network yet")
        return _png((10, 120, 10))

    store = TileStore(tmp_path, fetch=fetch)
    store.start()
    try:
        basemap = Basemap(store, (592, 400))
        layers = (LayerSpec(TEST_LAYER),)
        now = 1000.0
        basemap.render(HOME, 10.0, layers, now)
        assert basemap.missing > 0, "it knows it has holes"
        assert basemap.missing_by_layer == {"test": basemap.missing}
        wanted = basemap.missing
        deadline = time.monotonic() + 3
        while len(calls) < wanted and time.monotonic() < deadline:
            time.sleep(0.01)
        first_round = len(calls)
        assert first_round == wanted

        # Nothing changes on screen, and nobody touches it. Before the retry time: no asking.
        basemap.pump()
        basemap.render(HOME, 10.0, layers, now + RETRY_MISSING_S - 1)
        time.sleep(0.1)
        assert len(calls) == first_round

        # The network comes back. After the retry time the map asks again, and fills in.
        online["up"] = True
        basemap.render(HOME, 10.0, layers, now + RETRY_MISSING_S + 1)
        image = _settled(basemap, store, layers)
        assert len(calls) > first_round
        assert basemap.missing == 0
        assert image.get_at((MARGIN_PX + 296, MARGIN_PX + 200))[:3] == (10, 120, 10)
    finally:
        store.stop()

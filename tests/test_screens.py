import subprocess
import time

import pygame
import pytest

from flighttrack import settings as settings_module
from flighttrack.app import App
from flighttrack.platform.wifi import Network, Wifi, parse_networks
from flighttrack.ui import keyboard as kb
from flighttrack.ui import screens, theme
from flighttrack.ui.keyboard import Keyboard

SIZE = (800, 480)


def _down(target, pos):
    target.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))


def _up(target, pos):
    target.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1))


def _tap(target, pos):
    _down(target, pos)
    _up(target, pos)


def _drag(target, start, end, steps=5):
    _down(target, start)
    for i in range(1, steps + 1):
        pos = (
            round(start[0] + (end[0] - start[0]) * i / steps),
            round(start[1] + (end[1] - start[1]) * i / steps),
        )
        target.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1, 0, 0))
        )
    _up(target, end)


def _key(board, name):
    return next(rect.center for rect, key in board.keys if key == name)


def _type(board, text):
    for char in text:
        if char == " ":
            _tap(board, _key(board, kb.SPACE))
            continue
        for page, switch in (("letters", kb.LETTERS), ("symbols", kb.SYMBOLS), ("more", kb.MORE)):
            lowered = char.lower() if page == "letters" else char
            if any(key == lowered for _, key in _plain_keys(board, page)):
                while board.page != page:
                    step = (
                        switch
                        if any(k == switch for _, k in board.keys)
                        else (kb.SYMBOLS if board.page == "letters" else kb.LETTERS)
                    )
                    _tap(board, _key(board, step))
                if char.isupper():
                    _tap(board, _key(board, kb.SHIFT))
                target = char if page != "letters" or char.isupper() else lowered
                _tap(board, _key(board, target))
                break
        else:
            raise AssertionError(f"no key for {char!r}")


def _plain_keys(board, page):
    rows = {"letters": kb.ROWS_LOWER, "symbols": kb.ROWS_SYMBOLS, "more": kb.ROWS_MORE}[page]
    return [(None, char) for row in rows for char in row]


def test_keyboard_types_letters_symbols_and_capitals():
    result = []
    board = Keyboard(SIZE, "Test", "", result.append, lambda: result.append(None))
    _type(board, "http://Rx-1.local/a_b.json")
    assert board.text == "http://Rx-1.local/a_b.json"
    _type(board, " [#]")
    assert board.text.endswith(" [#]")
    for _ in range(4):
        _tap(board, _key(board, kb.BACKSPACE))
    assert board.text == "http://Rx-1.local/a_b.json"
    _tap(board, _key(board, kb.DONE))
    assert result == ["http://Rx-1.local/a_b.json"]


def test_keyboard_shift_is_for_one_letter_and_a_second_tap_locks_it():
    board = Keyboard(SIZE, "Test", "ab", lambda t: None, lambda: None)
    _tap(board, _key(board, kb.SHIFT))
    _tap(board, _key(board, "C"))
    _tap(board, _key(board, "d"))
    assert board.text == "abCd"
    # Twice is caps lock: it stays through letters, spaces and a visit to the digits...
    _tap(board, _key(board, kb.SHIFT))
    _tap(board, _key(board, kb.SHIFT))
    assert board.shift == kb.SHIFT_LOCK
    for key in ("N", kb.SPACE, "A"):
        _tap(board, _key(board, key))
    _tap(board, _key(board, kb.SYMBOLS))
    _tap(board, _key(board, "1"))
    _tap(board, _key(board, kb.LETTERS))
    _tap(board, _key(board, "B"))
    assert board.text == "abCdN A1B"
    # ...until a third tap lets go.
    _tap(board, _key(board, kb.SHIFT))
    assert board.shift == kb.SHIFT_OFF
    _tap(board, _key(board, "c"))
    assert board.text == "abCdN A1Bc"


def test_cancel_is_away_from_the_keys_and_asks_before_discarding_what_was_typed():
    result = []
    board = Keyboard(SIZE, "Test", "ab", result.append, lambda: result.append("canceled"))
    board.clock = FakeClock()
    assert kb.CANCEL not in [key for _, key in board.keys]
    keys_top = min(rect.top for rect, _ in board.keys)
    assert board.cancel_rect.bottom < keys_top - 100, "nowhere near a typing finger"
    assert board.cancel_rect.width >= theme.MIN_TOUCH_PX <= board.cancel_rect.height

    # With something typed, one tap only asks; so does a second that comes at once.
    _tap(board, _key(board, "c"))
    _tap(board, board.cancel_rect.center)
    assert result == [] and board.confirming_discard
    _tap(board, board.cancel_rect.center)
    assert result == []
    # Typing on calls the question off.
    _tap(board, _key(board, "d"))
    assert not board.confirming_discard and board.text == "abcd"
    # Asked, and answered after a moment: discarded.
    _tap(board, board.cancel_rect.center)
    board.clock.pause()
    _tap(board, board.cancel_rect.center)
    assert result == ["canceled"]

    # With nothing changed there is nothing to lose, and one tap leaves.
    untouched = Keyboard(SIZE, "Test", "ab", result.append, lambda: result.append("left"))
    _tap(untouched, untouched.cancel_rect.center)
    assert result[-1] == "left"


def test_the_cursor_moves_by_arrow_and_by_touch_and_edits_happen_there():
    pygame.display.init()
    pygame.font.init()
    surface = pygame.display.set_mode(SIZE)
    fonts = theme.Fonts.load()
    try:
        board = Keyboard(SIZE, "Test", "hello", lambda t: None, lambda: None)
        assert board.cursor == 5
        for _ in range(3):
            _tap(board, _key(board, kb.LEFT))
        _tap(board, _key(board, "x"))
        assert board.text == "hexllo" and board.cursor == 3
        _tap(board, _key(board, kb.BACKSPACE))
        _tap(board, _key(board, kb.RIGHT))
        assert board.text == "hello" and board.cursor == 3
        for _ in range(9):
            _tap(board, _key(board, kb.RIGHT))
        assert board.cursor == 5, "stops at the end"

        # A touch on the text puts the cursor between the letters nearest the finger.
        board.draw(surface, fonts, 0.0)
        left = board.box.left + 14
        between = left + fonts.large.size("he")[0]
        _tap(board, (between + 2, board.box.centery))
        assert board.cursor == 2
        _tap(board, (board.box.left + 2, board.box.centery))
        assert board.cursor == 0
        _tap(board, (board.box.right - 5, board.box.centery))
        assert board.cursor == 5

        # Text too long for the box shows the part round the cursor, wherever that is.
        board.set_text("http://receiver.example/" + "tar1090/data/" * 4 + "aircraft.json")
        board.draw(surface, fonts, 0.0)
        assert board._first > 0, "the end is in view"
        board.cursor = 0
        board.draw(surface, fonts, 0.0)
        assert board._first == 0, "and now the start"
    finally:
        pygame.quit()


def test_a_held_delete_or_arrow_repeats_by_the_clock_not_by_the_frame():
    board = Keyboard(SIZE, "Test", "x" * 40, lambda t: None, lambda: None)
    _down(board, _key(board, kb.BACKSPACE))
    assert len(board.text) == 39, "one at once"
    board.tick(100.0)
    board.tick(100.0 + kb.REPEAT_AFTER_S - 0.05)
    assert len(board.text) == 39, "and no more for a moment"
    # One slow frame a second later takes off as many as thirty quick ones would have.
    board.tick(100.0 + kb.REPEAT_AFTER_S + 1.0)
    expected = 39 - (int(1.0 / kb.REPEAT_EVERY_S) + 1)
    assert len(board.text) == expected
    _up(board, _key(board, kb.BACKSPACE))
    board.tick(105.0)
    assert len(board.text) == expected, "released: it stops"

    # An ordinary key does not repeat however long it is held.
    _down(board, _key(board, "a"))
    board.tick(200.0)
    board.tick(203.0)
    assert board.text.count("a") == 1


def test_keyboard_keys_are_touch_sized_and_fill_the_width():
    board = Keyboard(SIZE, "Test", "", lambda t: None, lambda: None)
    for page in ("letters", "symbols", "more"):
        board.page = page
        board._layout()
        for rect, key in board.keys:
            assert rect.width >= theme.MIN_TOUCH_PX and rect.height >= theme.MIN_TOUCH_PX, key
            assert pygame.Rect(0, 0, *SIZE).contains(rect), key
        assert len({key for _, key in board.keys}) == len(board.keys), "no duplicate keys"


TERSE = """\
no:HomeNet:71:WPA2
yes:HomeNet:64:WPA2
no:Cafe\\: Free WiFi:40:
no::55:WPA2
no:Neighbor:82:WPA2 WPA3
no:Open\\\\Net:30:--
garbage
"""


def test_parse_networks_merges_access_points_and_handles_escapes():
    assert parse_networks(TERSE) == [
        Network("HomeNet", 64, True, True),
        Network("Neighbor", 82, True, False),
        Network("Cafe: Free WiFi", 40, False, False),
        Network("Open\\Net", 30, False, False),
    ]
    assert parse_networks("") == []


def test_wpa3_only_networks_are_told_apart_from_mixed_ones():
    listing = "no:Mixed:70:WPA2 WPA3\nno:Strict:60:WPA3\nno:Old:50:WPA1 WPA2\nno:Open:40:\n"
    flags = {n.ssid: n.wpa3_only for n in parse_networks(listing)}
    assert flags == {"Mixed": False, "Strict": True, "Old": False, "Open": False}


class FakeNmcli:
    """Stands in for nmcli: records what was asked and answers from a script."""

    def __init__(self, connect_ok=True, saved=("HomeNet",)):
        self.calls = []
        self.connect_ok = connect_ok
        self.saved = list(saved)  # network names with a saved profile

    @staticmethod
    def profile(ssid):
        # The first network's profile is named by something else, as the OS imager does.
        return "preconfigured" if ssid == "HomeNet" else ssid

    def __call__(self, args, timeout):
        self.calls.append(args)
        out, err, code = "", "", 0
        if "list" in args:
            out = TERSE
        elif args[:4] == ["nmcli", "-t", "-f", "NAME,TYPE"]:
            out = "Wired connection 1:802-3-ethernet\nlo:loopback\n"
            out += "".join(f"{self.profile(ssid)}:802-11-wireless\n" for ssid in self.saved)
        elif args[:4] == ["nmcli", "-t", "-f", "802-11-wireless.ssid"]:
            ssid = next(s for s in self.saved if self.profile(s) == args[-1])
            out = f"802-11-wireless.ssid:{ssid}\n"
        elif args[1:3] == ["connection", "add"]:
            self.saved.append(args[args.index("ssid") + 1])
        elif args[1:3] == ["connection", "delete"]:
            self.saved = [s for s in self.saved if self.profile(s) != args[-1]]
        elif "connect" in args and self.connect_ok and args[4] not in self.saved:
            self.saved.append(args[4])  # joining a network saves it
        elif "connect" in args and not self.connect_ok:
            code = 4
            err = "Error: Connection activation failed: (7) Secrets were required.\n"
        return subprocess.CompletedProcess(args, code, out, err)


def test_wifi_connect_passes_names_and_passwords_as_plain_arguments():
    nmcli = FakeNmcli()
    wifi = Wifi(runner=nmcli, available=True)
    ok, message = wifi.connect("Cafe: Free; rm -rf /", "p@ss word'\"$(x)")
    assert ok and "Connected" in message
    assert [
        "nmcli",
        "device",
        "wifi",
        "connect",
        "Cafe: Free; rm -rf /",
        "password",
        "p@ss word'\"$(x)",
    ] in nmcli.calls
    wifi.connect("OpenNet", None)
    assert ["nmcli", "device", "wifi", "connect", "OpenNet"] in nmcli.calls


def test_joined_networks_are_kept_and_all_set_to_reconnect_for_ever():
    nmcli = FakeNmcli()
    wifi = Wifi(runner=nmcli, available=True)
    assert {n.ssid for n in wifi.scan() if n.saved} == {"HomeNet"}, "matched by network name"

    wifi.connect("Neighbor", "secret")
    modified = [call[4] for call in nmcli.calls if call[1:3] == ["connection", "modify"]]
    assert sorted(modified) == ["Neighbor", "preconfigured"], "by profile name, every network"
    assert all(
        call[5:] == ["connection.autoconnect", "yes", "connection.autoconnect-retries", "0"]
        for call in nmcli.calls
        if call[1:3] == ["connection", "modify"]
    )
    assert not any("delete" in call for call in nmcli.calls), "nothing known is ever removed"
    assert {n.ssid for n in wifi.scan() if n.saved} == {"HomeNet", "Neighbor"}


def test_wifi_failed_connect_forgets_a_new_network_but_not_a_known_one():
    nmcli = FakeNmcli(connect_ok=False)
    wifi = Wifi(runner=nmcli, available=True)
    ok, message = wifi.connect("Neighbor", "wrong")
    assert (ok, message) == (False, "Wrong password.")
    assert not any("delete" in call for call in nmcli.calls), "no profile was made, none removed"

    # NetworkManager sometimes leaves a profile behind after a failed join; that one goes.
    nmcli.saved.append("Other")
    real = nmcli.__call__

    def leaves_profile(args, timeout):
        if "connect" in args and "Leftover" in args:
            nmcli.saved.append("Leftover")
        return real(args, timeout)

    wifi = Wifi(runner=leaves_profile, available=True)
    assert wifi.connect("Leftover", "wrong")[0] is False
    assert nmcli.calls[-1] == ["nmcli", "connection", "delete", "id", "Leftover"]

    nmcli.calls.clear()
    wifi.connect("HomeNet", "wrong")
    assert not any("delete" in call for call in nmcli.calls), "an existing profile is kept"


def test_wifi_reports_timeouts_and_unavailability():
    def slow(args, timeout):
        raise subprocess.TimeoutExpired(args, timeout)

    assert Wifi(runner=slow, available=True).connect("X", "y") == (
        False,
        "The network did not respond in time.",
    )
    with pytest.raises(RuntimeError):
        Wifi(runner=slow, available=True).scan()
    assert Wifi(available=False).connect("X", "y")[0] is False


def test_current_network_saved_list_forget_and_add():
    nmcli = FakeNmcli(saved=("HomeNet", "Cabin"))
    wifi = Wifi(runner=nmcli, available=True)
    assert wifi.current() == "HomeNet"
    assert "--rescan" in nmcli.calls[-1] and nmcli.calls[-1][-1] == "no", "no scan: it is polled"
    assert wifi.saved() == ["Cabin", "HomeNet"]

    assert wifi.forget("HomeNet") == (True, "Removed HomeNet.")
    assert nmcli.calls[-1] == ["nmcli", "connection", "delete", "id", "preconfigured"]
    assert wifi.saved() == ["Cabin"]
    assert wifi.forget("Nowhere")[0] is False

    ok, message = wifi.add("Hangar 5; reboot", "s3cret pass")
    assert (ok, message) == (True, "Saved Hangar 5; reboot.")
    added = nmcli.calls[-1]
    assert added[1:9] == [
        "connection",
        "add",
        "type",
        "wifi",
        "con-name",
        "Hangar 5; reboot",
        "ssid",
        "Hangar 5; reboot",
    ]
    assert added[-4:] == ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", "s3cret pass"]
    assert "connection.autoconnect-retries" in added, "new networks keep retrying too"

    wifi.add("OpenField", None)
    assert "wifi-sec.psk" not in nmcli.calls[-1]
    assert wifi.add("OpenField", None) == (False, "OpenField is already saved.")
    assert wifi.add("  ", "longenough")[0] is False
    assert wifi.add("Short", "1234567") == (False, "A Wi-Fi password is 8 to 63 characters.")
    assert wifi.saved() == ["Cabin", "Hangar 5; reboot", "OpenField"]

    offline = Wifi(available=False)
    assert offline.current() is None and offline.saved() == []


@pytest.fixture
def app(tmp_path):
    nmcli = FakeNmcli()
    app = App(
        fullscreen=False,
        data_dir=tmp_path,
        replay=True,
        wifi=Wifi(runner=nmcli, available=True),
    )
    app.nmcli = nmcli
    yield app
    pygame.quit()


class FakeClock:
    """Time for a page's two-tap confirmations, moved by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def pause(self, seconds: float = 1.0) -> None:
        self.now += seconds


def _clocked(page):
    page.clock = FakeClock()
    return page.clock


def _items(page):
    """Every row of a page, on whichever of its tabs."""
    if not page.tabs:
        return page.items()
    showing, found = page.tab, []
    for tab in range(len(page.tabs)):
        page.tab = tab
        found += page.items()
    page.tab = showing
    return found


def _row(page, label, which=0):
    """The row with this label: the first by default, -1 for the last. On a page with tabs,
    the tab that has it is brought up first."""
    for _ in range(len(page.tabs)):
        if any(item.label == label for item in page.items()):
            break
        page.show_tab((page.tab + 1) % len(page.tabs))
    page._lay_out()
    return [rect for rect, item in page._layout if item.label == label][which]


def _scroll_to(page, label, which=0):
    """Drag the page until the row is comfortably on screen."""
    for _ in range(12):
        rect = _row(page, label, which)
        if page.top <= rect.top and rect.bottom <= SIZE[1]:
            return rect
        delta = 150 if rect.top < page.top else -150
        _drag(page, (400, 300), (400, 300 + delta))
    raise AssertionError(f"could not scroll to {label}")


def test_gear_opens_a_full_screen_page_that_scrolls_and_closes(app):
    app.draw(time.monotonic())
    app.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=app.settings_button.rect.center, button=1)
    )
    app.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONUP, pos=app.settings_button.rect.center, button=1)
    )
    page = app.page
    assert page is not None
    app.draw(time.monotonic())
    assert app.screen.get_at((700, 300))[:3] == theme.BACKGROUND, "the map is covered"

    # Six tabs, each a short list: everything is one tap away and little needs scrolling.
    assert page.tabs == (
        "Display",
        "Map",
        "Airports",
        "Aircraft",
        "Feeds",
        "System",
        "Storage",
        "About",
    )
    expected = {
        "Display": ["Screen brightness", "Map features opacity", "Day and night shading"],
        "Map": ["Map", "Kind of map", "Rain radar", "Cloud cover", "Weather opacity"],
        "Airports": ["Favorite airports"],
        "Aircraft": ["Favorite aircraft"],
        "Feeds": ["Local ADS-B receiver", "OpenSky client ID", "FlightAware AeroAPI key"],
        "System": ["Wi-Fi network", "Web page password", "Power"],
        "Storage": ["Clear caches"],
        "About": ["Where the data comes from"],
    }
    assert page.tab == 0
    for rect, name in zip(page.tab_rects(), page.tabs, strict=True):
        assert rect.height >= theme.MIN_TOUCH_PX and rect.width >= theme.MIN_TOUCH_PX
        assert rect.top >= screens.HEADER_PX and not rect.colliderect(page.done_rect)
        _tap(app, rect.center)
        assert page.tabs[page.tab] == name and page.scroll == 0
        labels = [item.label for item in page.items()]
        assert all(label in labels for label in expected[name]), (name, labels)
        app.draw(time.monotonic())
        if name not in ("About", "Display", "Map", "Feeds"):  # these run over, with a scroll bar
            assert not page.overflows, f"{name} fits without scrolling"
    assert any(i.label.startswith("Favorite aircraft") for i in _items(page))

    # About is the long one. It scrolls, stops at both ends, and shows that there is more.
    assert page.overflows and page.scroll == 0
    _drag(app, (400, 400), (400, 200))
    assert page.scroll == pytest.approx(200 - screens.TAP_SLOP_PX, abs=45)
    _drag(app, (400, 200), (400, 2000))
    assert page.scroll == 0, "cannot scroll past the top"
    _drag(app, (400, 400), (400, -5000))
    page._lay_out()
    assert page._layout[-1][0].bottom <= SIZE[1], "scrolling stops with the end in view"
    app.draw(time.monotonic())
    # Changing tab starts the next one at its top.
    _tap(app, page.tab_rects()[0].center)
    assert page.scroll == 0

    _tap(app, page.done_rect.center)
    assert app.page is None
    app.draw(time.monotonic())


def test_settings_page_sliders_and_switches_apply_and_persist(app, tmp_path):
    app.open_settings()
    page = app.page
    rect = _row(page, "Screen brightness")
    left, right, y = page._track(rect)
    _drag(page, (left + 40, y), (left - 30, y))
    assert app.settings.brightness == pytest.approx(0.1), "far left is the floor, not zero"
    _drag(page, (left, y), (right + 50, y))
    assert app.settings.brightness == pytest.approx(1.0)
    assert page.scroll == 0, "dragging a slider does not scroll the page"

    rect = _row(page, "Map brightness")
    left, right, y = page._track(rect)
    _tap(page, ((left + right) // 2, y))
    assert app.settings.map_brightness == pytest.approx(0.6, abs=0.02), "a tap sets it too"
    saved = settings_module.load(tmp_path / "settings.json")
    assert saved.map_brightness == pytest.approx(app.settings.map_brightness)

    # A vertical drag that starts on a slider's label, not its track, scrolls instead.
    before = app.settings.weather_opacity
    rect = _row(page, "Weather opacity")
    _drag(page, (rect.left + 60, rect.top + 12), (rect.left + 60, rect.top - 100))
    assert app.settings.weather_opacity == before

    for label, name in (
        ("Day and night shading", "day_night"),
        ("Runways at close range", "show_runways"),
        ("Airspace boundaries", "show_airspace"),
    ):
        was = getattr(app.settings, name)
        _tap(page, _scroll_to(page, label).center)
        assert getattr(app.settings, name) is (not was)
        assert getattr(settings_module.load(tmp_path / "settings.json"), name) is (not was)


def test_settings_page_adds_pins_and_removes_favorite_aircraft(app, tmp_path):
    from flighttrack.feeds.base import Aircraft
    from flighttrack.ui.go import Everywhere

    app.open_settings()
    page = app.page
    page.show_tab("Aircraft")
    assert "None yet. Tap + to add one." in [i.label for i in _items(page)]
    clock = _clocked(page)

    def head():
        rect = _scroll_to(page, "Favorite aircraft")
        item = next(i for i in _items(page) if i.label == "Favorite aircraft")
        return page.icon_rects(rect, item)[0]

    def icons(label):
        rect = _scroll_to(page, label)
        item = next(i for i in _items(page) if i.label == label)
        return item, page.icon_rects(rect, item)

    # + opens the GO page's search, for aircraft only; what is typed can be added as it is.
    plus = head()
    assert plus.width >= theme.MIN_TOUCH_PX and plus.height >= theme.MIN_TOUCH_PX
    _tap(page, plus.center)
    adding = page.child
    assert adding.scope == "aircraft" and adding.keyboard.done_label == "ADD"
    _type(adding.keyboard, "n123ab")
    app.draw(time.monotonic())
    assert adding.chosen == Everywhere("N123AB")
    _tap(app, adding.go_key.center)
    assert page.child is None and app.settings.favorites == ["N123AB"]
    assert settings_module.load(tmp_path / "settings.json").favorites == ["N123AB"]

    # One on the map is added by its registration.
    plane = Aircraft(
        "400abc", 51.5, -0.4, time.monotonic(), callsign="BAW117", registration="G-XWBA"
    )
    app.store.set_aircraft("replay", [plane], time.monotonic())
    _tap(page, head().center)
    _type(page.child.keyboard, "baw117")
    app.draw(time.monotonic())
    assert page.child.chosen == plane
    _tap(app, page.child.go_key.center)
    assert app.settings.favorites == ["N123AB", "G-XWBA"]

    # The pin keeps its track; the bin asks, and a tap on the row then removes it.
    item, (pin, trash) = icons("N123AB")
    assert [i.name for i in item.icons] == ["pin", "trash"] and not item.icons[0].on
    _tap(page, pin.center)
    assert app.settings.pinned_favorites == ["N123AB"]
    _tap(page, trash.center)
    assert app.settings.favorites == ["N123AB", "G-XWBA"], "the bin only asks"
    rect = _scroll_to(page, "Remove N123AB? Tap here.")
    clock.pause()
    _tap(page, (rect.left + 80, rect.centery))
    assert app.settings.favorites == ["G-XWBA"] and app.settings.pinned_favorites == []
    app.draw(time.monotonic())

    page.show_tab("Feeds")
    _tap(page, _scroll_to(page, "Local ADS-B receiver").center)
    assert page.keyboard.text == "http://", "the scheme is filled in to save typing"
    _tap(page, page.keyboard.cancel_rect.center)  # nothing typed: it leaves at once
    assert page.keyboard is None
    assert app.settings.local_receiver_url is None


def test_settings_page_lists_favorite_airports_with_a_house_a_bin_and_a_plus(app, tmp_path):
    from flighttrack import airports

    app.choose_favorite_airport(airports.find("EGLL"))
    app.toggle_favorite_airport(airports.find("KLAX"))
    app._reconfigure.clear()
    app.open_settings()
    page = app.page
    page.show_tab("Airports")

    def row(start):
        label = next(i.label for i in _items(page) if i.label.startswith(start))
        rect = _scroll_to(page, label)
        return rect, next(i for i in _items(page) if i.label == label)

    def house(start):
        return page.icon_rects(*row(start))[0].center

    def trash(start):
        return page.icon_rects(*row(start))[1].center

    def body(rect):
        return (rect.left + 80, rect.centery)

    _, item = row("EGLL")
    assert [i.name for i in item.icons] == ["home", "trash"] and item.icons[0].on
    assert not row("KLAX")[1].icons[0].on
    # The current location has a house and no bin, and the houses line up.
    here_rect, here = row("Current Location")
    assert here.icons[1] is None
    assert page.icon_rects(here_rect, here)[0].x == page.icon_rects(*row("KLAX"))[0].x
    for slot in page.icon_rects(*row("KLAX")):
        assert slot.width >= theme.MIN_TOUCH_PX and slot.height >= theme.MIN_TOUCH_PX
    app.draw(time.monotonic())
    clock = _clocked(page)

    # The body of a row does nothing; its house makes it home.
    _tap(page, body(row("KLAX")[0]))
    assert app.settings.home_airport == "EGLL"
    _tap(page, house("KLAX"))
    assert app.settings.home_airport == "KLAX" and app._reconfigure.is_set()
    assert app.settings.favorite_airports == ["EGLL", "KLAX"]

    # The bin only asks; the bin again backs out.
    _tap(page, trash("EGLL"))
    assert app.settings.favorite_airports == ["EGLL", "KLAX"]
    rect, item = row("Remove EGLL?")
    assert item.warning
    app.draw(time.monotonic())
    clock.pause()
    _tap(page, page.icon_rects(rect, item)[1].center)
    assert app.settings.favorite_airports == ["EGLL", "KLAX"], "backed out"
    assert not any(i.label.startswith("Remove EGLL?") for i in _items(page))

    # Confirming is a tap on the body of the row, and not one that follows instantly.
    _tap(page, trash("EGLL"))
    rect, _ = row("Remove EGLL?")
    _tap(page, body(rect))
    assert app.settings.favorite_airports == ["EGLL", "KLAX"], "too soon: a double tap"
    clock.pause()
    _tap(page, body(rect))
    assert app.settings.favorite_airports == ["KLAX"] and app.settings.home_airport == "KLAX"

    # Removing the home airport says so, and hands home to another favorite, or to nothing.
    _tap(page, trash("KLAX"))
    rect, item = row("Remove KLAX?")
    assert "It is home" in item.label
    clock.pause()
    _tap(page, body(rect))
    assert app.settings.favorite_airports == [] and app.settings.home_airport is None
    saved = settings_module.load(tmp_path / "settings.json")
    assert saved.favorite_airports == [] and saved.home_airport is None

    # + opens the GO page's search, for airports only.
    head_rect = _scroll_to(page, "Favorite airports")
    head = next(i for i in _items(page) if i.label == "Favorite airports")
    _tap(page, page.icon_rects(head_rect, head)[0].center)
    go_page = page.child
    assert go_page.scope == "airports" and go_page.keyboard.done_label == "ADD"
    _type(go_page.keyboard, "pao")
    app.draw(time.monotonic())
    _tap(app, go_page.go_key.center)
    assert page.child is None and app.page is page, "back on the settings page"
    assert app.settings.favorite_airports == ["KPAO"], "added, and home not moved by it"
    assert app.settings.home_airport is None


def _wait(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_wifi_page_lists_networks_and_joins_one_with_a_typed_password(app):
    app.open_settings()
    page = app.page
    _tap(page, _scroll_to(page, "Wi-Fi network").center)
    wifi_page = page.child
    assert wifi_page is not None
    assert _wait(lambda: wifi_page.busy is None and wifi_page.networks)
    app.draw(time.monotonic())
    assert [n.ssid for n in wifi_page.networks][:2] == ["HomeNet", "Neighbor"]

    _tap(app, _scroll_to(wifi_page, "HomeNet", -1).center)
    assert wifi_page.keyboard is None, "already on this one: nothing to do"

    _tap(app, _scroll_to(wifi_page, "Neighbor", -1).center)
    assert wifi_page.keyboard is not None and "Neighbor" in wifi_page.keyboard.title
    _type(wifi_page.keyboard, "Secret#1")
    _tap(app, _key(wifi_page.keyboard, kb.DONE))
    assert _wait(lambda: wifi_page.busy is None and wifi_page.message)
    assert [
        "nmcli",
        "device",
        "wifi",
        "connect",
        "Neighbor",
        "password",
        "Secret#1",
    ] in app.nmcli.calls
    assert wifi_page.message == "Connected to Neighbor." and not wifi_page.failed

    # An open network needs no password; a failure is reported on the page.
    app.nmcli.connect_ok = False
    _tap(app, _scroll_to(wifi_page, "Cafe: Free WiFi", -1).center)
    assert wifi_page.keyboard is None
    assert _wait(lambda: wifi_page.busy is None and wifi_page.failed)
    assert wifi_page.message == "Wrong password."
    app.draw(time.monotonic())

    # A network joined before needs no password again.
    app.nmcli.connect_ok = True
    assert "Neighbor" in app.nmcli.saved
    wifi_page.scan()
    assert _wait(lambda: wifi_page.busy is None)
    app.nmcli.calls.clear()
    _tap(app, _scroll_to(wifi_page, "Neighbor", -1).center)
    assert wifi_page.keyboard is None
    assert _wait(lambda: wifi_page.busy is None and "Connected" in wifi_page.message)
    assert ["nmcli", "device", "wifi", "connect", "Neighbor"] in app.nmcli.calls

    _tap(app, wifi_page.done_rect.center)
    assert page.child is None and app.page is page


def test_without_wifi_control_the_page_says_so(tmp_path):
    app = App(fullscreen=False, data_dir=tmp_path, replay=True)
    try:
        app.open_settings()
        labels = [item.label for item in _items(app.page)]
        assert "Wi-Fi can't be changed from here on this system." in labels
        button = next(i for i in _items(app.page) if i.label == "Wi-Fi network")
        assert button.action is None
        app.draw(time.monotonic())
    finally:
        pygame.quit()


def test_footer_and_settings_show_the_network_in_use(app):
    now = time.monotonic()
    assert app._network_note(now)[0] in ("No Wi-Fi", "No network"), "before the first check"
    app.refresh_network()
    assert app.network_name == "HomeNet"
    assert app._network_note(now) == ("Wi-Fi: HomeNet", theme.TEXT_DIM)
    app._address, app._address_checked_at = "10.1.2.3", now
    assert app.network_summary(now) == "HomeNet (10.1.2.3)"
    app.draw(now)

    app.open_settings()
    button = next(i for i in _items(app.page) if i.label == "Wi-Fi network")
    assert button.value == "HomeNet (10.1.2.3)"

    app.network_name = None
    assert app.network_summary(now) == "10.1.2.3", "wired, or not yet known"
    app._address = None
    assert app.network_summary(now) == "Not connected"
    assert app._network_note(now) == ("No network", theme.STATUS_STALE)


def test_a_long_network_name_in_the_footer_is_cut_short_not_overlapped(app):
    from flighttrack.ui import bars

    surface = pygame.Surface((800, 32))
    rect = surface.get_rect()
    long_name = "Wi-Fi: " + "An Extremely Long Network Name " * 4
    bars.draw_bottom(
        surface, rect, app.fonts, "RANGE 250 NM", "Updated 12:00:00", theme.STATUS_OK, "", long_name
    )
    center = app.fonts.small.size("Updated 12:00:00")[0]
    gap = pygame.Rect(400 - center // 2 - 10, 0, 8, 32)
    assert (
        pygame.mask.from_threshold(
            surface.subsurface(gap), theme.TEXT_DIM, (60, 60, 60, 255)
        ).count()
        == 0
    )


def test_saved_networks_can_be_removed_with_a_second_tap_and_added_by_name(app):
    app.nmcli.saved.append("Cabin")
    app.open_settings()
    page = app.page
    _tap(page, _scroll_to(page, "Wi-Fi network").center)
    wifi_page = page.child
    assert _wait(lambda: wifi_page.busy is None and wifi_page.saved)
    assert wifi_page.saved == ["Cabin", "HomeNet"]
    labels = [item.label for item in wifi_page.items()]
    assert labels.index("Saved networks") < labels.index("Networks in range")
    app.draw(time.monotonic())

    def saved_row(label):
        """The row in the saved area (a network in range has a row of the same name below)."""
        return _scroll_to(wifi_page, label)

    clock = _clocked(wifi_page)

    def remove_button(label):
        rect = saved_row(label)
        return (rect.right - screens.MARGIN - screens.SIDE_BUTTON_PX // 2, rect.centery)

    asking = "Remove Cabin? Tap here."

    # A saved network's row is not a button: tapping its name does nothing at all, so it
    # cannot be taken for one of the rows further down, where a tap joins.
    saved = next(i for i in wifi_page.items() if i.label == "Cabin")
    assert saved.action is None and saved.side_label == "Remove" and saved.plain
    calls = len(app.nmcli.calls)
    _tap(app, saved_row("Cabin").center)
    assert wifi_page.confirming is None and len(app.nmcli.calls) == calls

    # Its Remove button asks; a tap elsewhere, or on Keep, calls it off; nothing is deleted.
    _tap(app, remove_button("Cabin"))
    assert wifi_page.confirming == "Cabin"
    assert any(item.label == asking for item in wifi_page.items())
    _tap(app, (400, screens.HEADER_PX + 5))
    assert wifi_page.confirming is None
    _tap(app, remove_button("Cabin"))
    clock.pause()
    _tap(app, remove_button(asking))  # where Remove was is now Keep
    assert wifi_page.confirming is None
    assert not any("delete" in call for call in app.nmcli.calls)

    # The question is answered in the body of the row, away from where the finger was. A
    # tap that follows at once removes nothing: it came too soon to be a decision.
    _tap(app, remove_button("Cabin"))
    _tap(app, saved_row(asking).center)
    time.sleep(0.05)
    assert not any("delete" in call for call in app.nmcli.calls)
    assert wifi_page.confirming == "Cabin", "still asking"

    # After a moment it does.
    clock.pause()
    _tap(app, saved_row(asking).center)
    assert _wait(lambda: wifi_page.busy is None and wifi_page.message == "Removed Cabin.")
    assert wifi_page.saved == ["HomeNet"]

    # The network in use says so, in its row and again in the question.
    assert next(i for i in wifi_page.items() if i.label == "HomeNet").value == "in use"
    _tap(app, remove_button("HomeNet"))
    assert any("in use now" in i.label for i in wifi_page.items() if i.warning)
    _tap(app, (400, screens.HEADER_PX + 5))

    # Add a network that is not in range: name, then password.
    _tap(app, saved_row("Add a network").center)
    assert wifi_page.keyboard.title == "Network name"
    _type(wifi_page.keyboard, "Hangar")
    _tap(app, _key(wifi_page.keyboard, kb.DONE))
    assert wifi_page.keyboard.title == "Password for Hangar"
    _type(wifi_page.keyboard, "longpassword")
    _tap(app, _key(wifi_page.keyboard, kb.DONE))
    assert _wait(lambda: wifi_page.busy is None and wifi_page.message == "Saved Hangar.")
    assert wifi_page.saved == ["Hangar", "HomeNet"]
    app.draw(time.monotonic())

    # A password that cannot be right is refused with a reason, and nothing is saved.
    _tap(app, saved_row("Add a network").center)
    _type(wifi_page.keyboard, "Shed")
    _tap(app, _key(wifi_page.keyboard, kb.DONE))
    _type(wifi_page.keyboard, "short")
    _tap(app, _key(wifi_page.keyboard, kb.DONE))
    assert _wait(lambda: wifi_page.busy is None and wifi_page.failed)
    assert "8 to 63" in wifi_page.message and "Shed" not in wifi_page.saved


def test_wpa3_only_networks_are_marked_and_a_failed_join_explains_why(app):
    real = app.nmcli.__call__

    def with_strict(args, timeout):
        result = real(args, timeout)
        if "list" in args:
            result.stdout += "no:Strict:60:WPA3\n"
        return result

    app.wifi._run = with_strict
    app.nmcli.connect_ok = False
    app.open_settings()
    app.page.open_wifi()
    wifi_page = app.page.child
    assert _wait(lambda: wifi_page.busy is None and wifi_page.networks)
    row = next(i for i in wifi_page.items() if i.label == "Strict")
    assert "WPA3 only" in row.value

    _tap(app, _scroll_to(wifi_page, "Strict", -1).center)
    assert "WPA3 only" in wifi_page.keyboard.hint
    _type(wifi_page.keyboard, "password1")
    _tap(app, _key(wifi_page.keyboard, kb.DONE))
    assert _wait(lambda: wifi_page.busy is None and wifi_page.failed)
    assert wifi_page.message == "Strict is WPA3 only, which this display's Wi-Fi cannot join."


class FakePower:
    """Stands in for the system's power control: says yes, and records being asked to act."""

    def __init__(self, allowed=True, works=True):
        self.allowed, self.works, self.calls = allowed, works, []

    def __call__(self, args, timeout):
        self.calls.append(args)
        if args[0] == "busctl":
            return subprocess.CompletedProcess(
                args, 0, 's "yes"\n' if self.allowed else 's "challenge"\n', ""
            )
        if self.works:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "Failed to power off: Access denied\n")


def _power_app(tmp_path, fake):
    from flighttrack.platform.power import Power

    return App(
        fullscreen=False, data_dir=tmp_path, replay=True, power=Power(runner=fake, available=True)
    )


def test_shut_down_takes_two_taps_and_then_asks_the_system_to_power_off(tmp_path):
    fake = FakePower()
    app = _power_app(tmp_path, fake)
    try:
        app.open_settings()
        page = app.page
        assert _wait(lambda: page.can_shut_down)
        clock = _clocked(page)
        _tap(page, _scroll_to(page, "Shut down").center)
        assert page.confirming_shutdown
        assert not any(call[0] == "systemctl" for call in fake.calls), "one tap does nothing yet"
        app.draw(time.monotonic())

        # A tap somewhere else calls it off.
        _tap(page, _scroll_to(page, "Wait for the screen to go dark before unplugging.").center)
        assert not page.confirming_shutdown
        assert any(i.label == "Shut down" for i in _items(page))

        # A double tap does not shut the device down.
        _tap(page, _scroll_to(page, "Shut down").center)
        _tap(page, _scroll_to(page, "Shut down now?").center)
        time.sleep(0.05)
        assert not any(call[0] == "systemctl" for call in fake.calls)
        assert page.confirming_shutdown

        # Nor does a tap that comes long after the question was asked.
        clock.pause(screens.CONFIRM_MAX_S + 1)
        assert not page.confirming_shutdown
        assert any(i.label == "Shut down" for i in _items(page))

        _tap(page, _scroll_to(page, "Shut down").center)
        clock.pause()
        _tap(page, _scroll_to(page, "Shut down now?").center)
        assert _wait(lambda: ["systemctl", "poweroff", "--no-ask-password"] in fake.calls)
        assert sum(call[0] == "systemctl" for call in fake.calls) == 1
        assert [i.label for i in _items(page) if i.label.startswith("Shutting")] == [
            "Shutting down..."
        ]
        app.draw(time.monotonic())
    finally:
        pygame.quit()


def test_shut_down_is_not_offered_where_the_system_would_ask_for_a_password(tmp_path):
    for fake in (FakePower(allowed=False), None):
        app = (
            _power_app(tmp_path, fake)
            if fake
            else App(fullscreen=False, data_dir=tmp_path, replay=True)
        )
        try:
            app.open_settings()
            time.sleep(0.1)
            labels = [item.label for item in _items(app.page)]
            assert "Shut down" not in labels
            assert "Shutting down from here is not available on this system." in labels
        finally:
            pygame.quit()


def test_a_refused_shut_down_says_why(tmp_path):
    fake = FakePower(works=False)
    app = _power_app(tmp_path, fake)
    try:
        app.open_settings()
        page = app.page
        assert _wait(lambda: page.can_shut_down)
        clock = _clocked(page)
        page.shut_down()
        clock.pause()
        page.shut_down()
        assert _wait(lambda: "Access denied" in page.shutdown_message)
    finally:
        pygame.quit()

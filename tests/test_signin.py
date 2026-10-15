"""The web page's password, the keys for services, and keeping both out of sight."""

import time

import pygame
import pytest

from flighttrack import portal as portal_module
from flighttrack import secrets_store
from flighttrack import settings as settings_module
from flighttrack.app import App, portal_secret
from flighttrack.portal import create_app
from flighttrack.ui.keyboard import Keyboard


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(portal_module, "PASSWORD_REQUIRED", True)
    settings_module.save(
        settings_module.Settings(home_lat=37.0, home_lon=-122.6), tmp_path / "settings.json"
    )
    app = App(fullscreen=False, data_dir=tmp_path, replay=False)
    yield app
    pygame.quit()


def _set_password(app, text):
    """Set the password and wait for it to be hashed and kept (it is done in the background)."""
    error = app.set_portal_password(text)
    deadline = time.monotonic() + 10
    while app.password_saving and time.monotonic() < deadline:
        time.sleep(0.01)
    return error


def _client(app):
    web = create_app(app.portal_state, app.apply_changes, preview=lambda: b"\xff\xd8 jpeg")
    return web.test_client()


def test_without_a_password_set_the_web_page_is_only_the_picture(app):
    client = _client(app)
    page = client.get("/").get_data(as_text=True)
    assert "first set a password on the display" in page
    assert '"switches"' not in page and "Favorite airports" not in page, "no settings sent"
    assert client.get("/preview.jpg").status_code == 200
    for path in ("/api/state", "/airports?q=lhr", "/aircraft?q=n1"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/set", json={"name": "show_map", "value": False}).status_code == 401
    assert app.settings.show_map is True
    assert client.post("/signin", json={"password": "anything"}).status_code == 403


def test_signing_in_opens_the_settings_and_a_new_password_signs_everyone_out(app):
    assert _set_password(app, "short") == "At least 6 characters."
    assert _set_password(app, "hunter22") is None
    stored = app.settings.portal_password
    assert stored.startswith("scrypt$131072$8$1$") and "hunter22" not in stored, "only a hash"
    assert settings_module.load(app.settings_path).portal_password == stored

    client = _client(app)
    assert 'type="password"' in client.get("/").get_data(as_text=True)
    assert client.post("/signin", json={"password": "wrong-one"}).status_code == 403
    assert client.post("/signin", json={"password": "hunter22"}).status_code == 200
    assert client.get("/api/state").status_code == 200
    assert "Favorite airports" in client.get("/").get_data(as_text=True)

    _set_password(app, "another-one")
    assert client.get("/api/state").status_code == 401, "a new password signs everyone out"
    client.post("/signin", json={"password": "another-one"})
    client.post("/signout")
    assert client.get("/api/state").status_code == 401


def test_guessing_is_slowed_down(app):
    _set_password(app, "hunter22")
    client = _client(app)
    for _ in range(portal_module.SIGN_IN_TRIES):
        assert client.post("/signin", json={"password": "guess"}).status_code == 403
    answer = client.post("/signin", json={"password": "hunter22"})
    assert answer.status_code == 429, "even the right one, until the minute is up"


def test_keys_are_kept_but_never_shown_in_full(app):
    _set_password(app, "hunter22")
    client = _client(app)
    client.post("/signin", json={"password": "hunter22"})
    key = "abcdef-1234567890-XYZ9"
    answer = client.post("/api/key", json={"name": "flightaware_key", "value": key}).get_json()
    assert app.settings.flightaware_key == key
    assert answer["keys"]["flightaware_key"] == "••••XYZ9"
    assert key not in client.get("/").get_data(as_text=True)
    assert key not in client.get("/api/state").get_data(as_text=True)
    bad = client.post("/api/key", json={"name": "flightaware_key", "value": "has spaces in it"})
    assert bad.status_code == 400 and app.settings.flightaware_key == key
    assert (
        client.post("/api/key", json={"name": "portal_password", "value": "x" * 8}).status_code
        == 400
    )
    client.post("/api/key", json={"name": "flightaware_key", "value": ""})
    assert app.settings.flightaware_key is None, "empty removes it"

    # On the display: the Feeds tab shows it masked, and typing one is hidden from the web.
    assert app.set_key("adsbx_key", "zzzzzz-key-0001") is None
    app.open_settings()
    page = app.page
    page.show_tab("Feeds")

    def row(field):
        return next(i for i in page.items() if i.label == secrets_store.KEY_FIELDS[field])

    kept = row("adsbx_key")
    assert kept.value == "••••0001"
    assert [i.name for i in kept.icons] == ["edit", "trash"] and kept.icons[1].action is not None
    empty = row("flightaware_key")
    assert empty.value == "Not set" and empty.icons[1].action is None, "nothing to remove"
    # The bin asks first: the row turns into the question, a tap on it removes the key.
    kept.icons[1].action()
    question = next(i for i in page.items() if i.label.startswith("Remove the ADS-B"))
    assert question.warning and app.settings.adsbx_key == "zzzzzz-key-0001"
    page._armed = (page._armed[0], page._armed[1] - 1)  # a deliberate second tap, not a bounce
    question.action()
    assert app.settings.adsbx_key is None
    # The pencil opens the keyboard in secret mode, hidden from the web page meanwhile.
    row("flightaware_key").icons[0].action()
    assert page.keyboard is not None and page.keyboard.secret
    assert app.typing_in_private(), "the web page's picture is blanked meanwhile"


def test_a_secret_is_typed_as_dots_unless_shown():
    board = Keyboard((800, 480), "Key", "", lambda t: None, lambda: None, secret=True)
    board.text = "abc123"
    assert board.shown_text == "•" * 6
    pygame.display.init()
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        board.handle_event(pygame.event.Event(kind, pos=board.show_rect.center, button=1))
    assert board.reveal and board.shown_text == "abc123"
    plain = Keyboard((800, 480), "Name", "abc", lambda t: None, lambda: None)
    assert plain.show_rect is None and plain.shown_text == "abc"


def test_the_cookie_key_is_kept_and_private(tmp_path):
    first = portal_secret(tmp_path)
    assert len(first) == 32 and portal_secret(tmp_path) == first
    assert (tmp_path / "portal_secret").stat().st_mode & 0o077 == 0, "owner only"


def test_secrets_are_kept_apart_where_only_this_user_can_read_them(tmp_path):
    import json
    import stat

    settings = settings_module.Settings(
        home_airport="KPAO",
        flightaware_key="fa-key-1234567890",
        portal_password="scrypt$1$1$1$00$00",
    )
    path = tmp_path / "data" / "settings.json"
    settings_module.save(settings, path)
    plain = json.loads(path.read_text())
    assert "flightaware_key" not in plain and "portal_password" not in plain
    assert plain["home_airport"] == "KPAO"
    secrets_file = path.parent / settings_module.SECRETS_FILE
    assert json.loads(secrets_file.read_text())["flightaware_key"] == "fa-key-1234567890"
    assert stat.S_IMODE(secrets_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700, "the folder too"
    again = settings_module.load(path)
    assert again.flightaware_key == "fa-key-1234567890" and again.home_airport == "KPAO"

    # Secrets come only from their own file: a key put into settings.json is not taken.
    path.write_text(json.dumps({**plain, "adsbx_key": "stray-key-123456"}))
    assert settings_module.load(path).adsbx_key is None

"""The settings portal: a small web page served on the local network.

It mirrors the settings page on the display, tab for tab, for anyone who would sooner use a
real keyboard. It is unauthenticated, so it must never expose anything beyond these settings:
no Wi-Fi, no shutting the machine down.
"""

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request, session
from werkzeug.serving import BaseWSGIServer, make_server

from flighttrack import __version__, airports, credits, favorites, secrets_store, themes
from flighttrack.feeds.base import Aircraft
from flighttrack.settings import (
    FRACTION_SETTINGS,
    MAP_STYLES,
    MAX_FAVORITE_AIRPORTS,
    THEMES,
    Settings,
)
from flighttrack.ui.format import receiver_lines

log = logging.getLogger(__name__)

DEFAULT_PORT = 80
FALLBACK_PORT = 8080
MAX_FORM_BYTES = 16 * 1024
MAX_QUERY_CHARS = 40
SUGGESTIONS = 8

# The switches the page may flip. Sliders are FRACTION_SETTINGS. Nothing else can be set by
# name: the rest goes through routes that check what they are given.
SWITCHES = ("show_map", "show_radar", "show_clouds", "show_airspace", "show_runways", "day_night")
# Settings that are one of a fixed list, with the list.
CHOICES = {"map_style": MAP_STYLES, "theme": THEMES}


@dataclass(frozen=True)
class PortalState:
    """What the page shows. Built by the app on request, so it is always current."""

    settings: Settings
    location_label: str | None  # where the display is centered right now
    location_source: str | None  # "airport", "coordinates", "ip" or None while unknown
    near: tuple[float, float] | None = None  # that place, to rank airport suggestions by
    here: str | None = None  # the current location's "City, ST", once it is known
    categories: dict[str, str] = field(default_factory=dict)  # airport -> VFR, MVFR, IFR, LIFR
    whereabouts: dict[str, str] = field(default_factory=dict)  # favorite aircraft -> words
    # Keyed services: how each last answered (words, good, bad); and every service's requests
    # today, a line each (keyed.usage_lines).
    services: dict[str, list[tuple[str, bool, bool]]] = field(default_factory=dict)
    usage: list[str] = field(default_factory=list)


# What can be seen without the password: the sign-in page, the picture of the screen, and the
# page's own files.
OPEN_ENDPOINTS = frozenset({"index", "screen", "favicon", "static", "sign_in", "sign_out"})
SIGNED_IN_FOR_S = 30 * 24 * 3600
# Whether the password is asked for. Always, in use; tests of the routes behind it turn it
# off (tests/conftest.py), and the tests of signing in turn it back on.
PASSWORD_REQUIRED = True
SIGN_IN_TRIES = 5  # wrong passwords from one visitor ...
SIGN_IN_WINDOW_S = 60.0  # ... in this long, and it must wait

FORM_FIELDS = ("home_airport", "favorite_airports", "local_receiver_url", "favorites")


def validate(form: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    """Turn typed fields into settings changes. Returns (changes, errors by field). Used by
    the settings page on the display as well as the routes here, so the two cannot disagree
    about what is valid."""
    changes: dict[str, object] = {}
    errors: dict[str, str] = {}

    code = form.get("home_airport", "").strip().upper()
    home = airports.find(code) if code else None
    if not code:
        changes["home_airport"] = None
    elif home is None:
        errors["home_airport"] = (
            f"No airport has the code {code[:10]}. Type its code, or pick one from the list."
        )
    else:
        changes["home_airport"] = home.ident

    # Favorite airports: any codes, stored as ICAO idents. Home is always one of them.
    idents, unknown = [], []
    for piece in re.split(r"[,\s;]+", form.get("favorite_airports", "")):
        if piece:
            found = airports.find(piece)
            (idents if found else unknown).append(found.ident if found else piece.upper()[:10])
    if unknown:
        errors["favorite_airports"] = (
            "No airport found with the code " + ", ".join(unknown[:5]) + "."
        )
    elif "home_airport" in changes:
        if home is not None and home.ident not in idents:
            idents.insert(0, home.ident)
        changes["favorite_airports"] = list(dict.fromkeys(idents))[:MAX_FAVORITE_AIRPORTS]

    url = form.get("local_receiver_url", "").strip()
    if not url:
        changes["local_receiver_url"] = None
    elif urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        errors["local_receiver_url"] = "Enter a full address starting with http:// or https://."
    else:
        changes["local_receiver_url"] = url

    entries, rejected = favorites.parse(form.get("favorites", ""))
    if rejected:
        errors["favorites"] = "Not a registration, callsign or hex code: " + ", ".join(rejected[:5])
    elif len(entries) > favorites.MAX_FAVORITES:
        errors["favorites"] = f"At most {favorites.MAX_FAVORITES} favorites."
    else:
        changes["favorites"] = entries

    return changes, errors


def _describe(state: PortalState) -> dict:
    """Everything the page shows, as plain data."""
    s = state.settings
    colors = themes.css_colors(s.theme)
    listed = []
    for ident in s.favorite_airports:
        airport = airports.find(ident)
        listed.append(
            {
                "ident": ident,
                "name": airport.name if airport else "",
                "place": airport.place if airport else "",
                "home": ident == s.home_airport,
                "category": state.categories.get(ident, ""),
            }
        )
    return {
        "switches": {name: bool(getattr(s, name)) for name in SWITCHES},
        "choices": {
            name: {"value": getattr(s, name), "options": options}
            for name, options in CHOICES.items()
        },
        "sliders": {
            name: {"value": getattr(s, name), "min": low} for name, low in FRACTION_SETTINGS.items()
        },
        "colors": colors,
        "airports": listed,
        "here": {
            "known": state.here is not None,
            "place": state.here or "",
            "home": state.location_source == "ip",
        },
        "aircraft": list(s.favorites),
        "pinned": list(s.pinned_favorites),
        "where": {entry: state.whereabouts.get(entry, "") for entry in s.favorites},
        "receiver": s.local_receiver_url or "",
        # Keys as they are shown once set: masked, but for OpenSky's client ID, which is not
        # a secret (secrets_store.shown).
        "keys": {
            name: secrets_store.shown(name, getattr(s, name)) for name in secrets_store.KEY_FIELDS
        },
        "services": {
            group: [{"text": t, "good": g, "bad": b} for t, g, b in lines]
            for group, lines in state.services.items()
        },
        "usage": list(state.usage),
        "centered": {"label": state.location_label, "source": state.location_source},
    }


def create_app(
    get_state: Callable[[], PortalState],
    apply_changes: Callable[[dict[str, object]], None],
    flights: Callable[[], list[Aircraft]] | None = None,
    reset: Callable[[], None] | None = None,
    storage: Callable[[], dict[str, int] | None] | None = None,
    clear_caches: Callable[[], None] | None = None,
    preview: Callable[[], bytes | None] | None = None,
    receiver_status: Callable[[], dict | None] | None = None,
    secret_key: bytes | None = None,
) -> Flask:
    """flights returns the aircraft being tracked now, for suggesting favorites; reset puts
    the settings back to their defaults (keeping home and the favorites). secret_key signs
    the sign-in cookie; keep it across restarts so that people stay signed in.

    Everything but the picture of the screen needs the password, which is set on the display
    (Settings.portal_password). Until it is entered, and while none is set, the page is only
    that picture and a box to sign in."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FORM_BYTES
    app.secret_key = secret_key or os.urandom(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=SIGNED_IN_FOR_S,
    )
    failures: dict[str, list[float]] = {}  # visitor -> times of recent wrong passwords

    def signed_in() -> bool:
        if not PASSWORD_REQUIRED:
            return True
        stored = get_state().settings.portal_password
        version = session.get("auth")
        return stored is not None and version == secrets_store.password_version(stored)

    @app.before_request
    def refuse_cross_site_changes():
        # A page elsewhere must not be able to change settings on a device that happens to
        # be on the visitor's network. Every route that changes anything is a POST.
        if request.method != "GET":
            origin = request.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc != request.host:
                return "Cross-site request refused.", 403
        return None

    @app.before_request
    def require_the_password():
        if request.endpoint in OPEN_ENDPOINTS or signed_in():
            return None
        return jsonify({"error": "Sign in first."}), 401

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def text(name: str, limit: int = 300) -> str:
        value = body().get(name, "")
        return value.strip()[:limit] if isinstance(value, str) else ""

    def extras() -> dict:
        """What the page shows that is not a setting: sizes, and how the receiver answered
        (already put into words, the same words the display uses)."""
        status = receiver_status() if receiver_status is not None else None
        return {
            "storage": storage() if storage is not None else None,
            "receiver_status": [
                {"text": line.strip(), "good": good, "bad": bad}
                for line, good, bad in receiver_lines(status, time.time())
            ],
        }

    def done(error: str | None = None):
        """The answer to every change: how things now stand, and what was wrong if anything."""
        answer = _describe(get_state())
        answer.update(extras())
        if error:
            answer["error"] = error
        return jsonify(answer), (400 if error else 200)

    @app.get("/")
    def index():
        if not signed_in():
            # Nothing about the settings is sent before the password: just the picture.
            return render_template(
                "signin.html",
                theme_css=themes.css(get_state().settings.theme),
                can_preview=preview is not None,
                password_set=get_state().settings.portal_password is not None,
                version=__version__,
            )
        state = get_state()
        return render_template(
            "settings.html",
            theme_css=themes.css(state.settings.theme),
            state={**_describe(state), **extras()},
            credits=credits,
            version=__version__,
            can_reset=reset is not None,
            can_clear=clear_caches is not None,
            can_preview=preview is not None,
            key_groups=[
                (group, [(f, secrets_store.KEY_FIELDS[f]) for f in fields], about)
                for group, fields, about in secrets_store.KEY_GROUPS
            ],
        )

    @app.post("/signin")
    def sign_in():
        who = request.remote_addr or ""
        now = time.monotonic()
        recent = [t for t in failures.get(who, []) if now - t < SIGN_IN_WINDOW_S]
        if len(recent) >= SIGN_IN_TRIES:
            return jsonify({"error": "Too many wrong tries. Wait a minute."}), 429
        stored = get_state().settings.portal_password
        if stored is None:
            return jsonify({"error": "No password is set. Set one on the display."}), 403
        if not secrets_store.password_matches(text("password", 200), stored):
            failures[who] = [*recent, now]
            return jsonify({"error": "That is not the password."}), 403
        failures.pop(who, None)
        session.clear()
        session.permanent = True
        session["auth"] = secrets_store.password_version(stored)
        return jsonify({"ok": True})

    @app.post("/signout")
    def sign_out():
        session.clear()
        return jsonify({"ok": True})

    @app.post("/api/key")
    def change_key():
        """Keep a service's key, or remove it (empty). The key never comes back in full."""
        name = text("name", 40)
        if name not in secrets_store.KEY_FIELDS:
            return done("Not a key this display keeps.")
        key, error = secrets_store.check_key(text("value", 300))
        if error:
            return done(error)
        apply_changes({name: key})
        return done()

    @app.get("/preview.jpg")
    def screen():
        """The display's screen as it is now. View only: a picture, nothing more. (The
        display blanks it while its settings keyboard is up.)"""
        picture = preview() if preview is not None else None
        if picture is None:
            return Response("No picture yet.", status=503, headers={"Retry-After": "1"})
        return Response(picture, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/favicon.ico")
    def favicon():
        # Browsers ask for this whatever the page says; give them the small picture.
        return app.send_static_file("icon-32.png")

    @app.get("/api/state")
    def state():
        return done()

    @app.get("/airports")
    def suggest_airports():
        """Airports matching what has been typed: the same search as the GO page on the
        display. Read-only, and says nothing about this display."""
        query = request.args.get("q", "")[:MAX_QUERY_CHARS]
        found = airports.search(query, get_state().near, SUGGESTIONS) if query.strip() else ()
        return jsonify(
            [{"ident": a.ident, "iata": a.iata, "name": a.name, "place": a.place} for a in found]
        )

    @app.get("/aircraft")
    def suggest_aircraft():
        """Aircraft the display is tracking now whose callsign, registration or hex starts
        with what has been typed, to pick a favorite from. By registration when known: a
        callsign belongs to a flight, not an airframe."""
        query = favorites.normalize(request.args.get("q", "")[:MAX_QUERY_CHARS])
        found, seen = [], set()
        for aircraft in flights() if flights is not None and query else ():
            names = [n for n in (aircraft.registration, aircraft.callsign, aircraft.hex) if n]
            if not any(favorites.normalize(n).startswith(query) for n in names):
                continue
            entry = (aircraft.registration or aircraft.callsign or aircraft.hex).upper()
            if entry not in seen:
                seen.add(entry)
                found.append(
                    {
                        "entry": entry,
                        "callsign": aircraft.callsign or "",
                        "registration": aircraft.registration or "",
                        "type": aircraft.type_code or "",
                        "seen": round(time.monotonic() - aircraft.seen_at),
                    }
                )
        found.sort(key=lambda item: item["entry"])
        return jsonify(found[:SUGGESTIONS])

    @app.post("/api/set")
    def set_one():
        """A switch or a slider, by name."""
        name, value = body().get("name"), body().get("value")
        if name in SWITCHES and isinstance(value, bool):
            apply_changes({name: value})
        elif name in CHOICES and isinstance(value, str) and value in CHOICES[name]:
            apply_changes({name: value})
        elif name in FRACTION_SETTINGS and isinstance(value, int | float):
            if isinstance(value, bool) or value != value:  # no booleans, no NaN
                return done("Not a number.")
            apply_changes({name: min(max(float(value), FRACTION_SETTINGS[name]), 1.0)})
        else:
            return done("No such setting.")
        return done()

    @app.post("/api/airports")
    def change_airports():
        """Add or remove a favorite airport, or say which is home. As on the display: home
        is the favorite chosen most recently, or the current location."""
        action, code = text("action"), text("code", 10)
        settings = get_state().settings
        listed = list(settings.favorite_airports)
        if action == "home" and not code:
            apply_changes({"home_airport": None})  # the current location
            return done()
        airport = airports.find(code)
        if airport is None:
            return done(f"No airport has the code {code.upper() or '(none)'}.")
        ident = airport.ident
        if action == "add":
            if ident not in listed:
                if len(listed) >= MAX_FAVORITE_AIRPORTS:
                    return done(f"At most {MAX_FAVORITE_AIRPORTS} favorite airports.")
                apply_changes({"favorite_airports": [*listed, ident]})
        elif action == "home":
            if ident not in listed:
                listed.append(ident)
            apply_changes({"favorite_airports": listed, "home_airport": ident})
        elif action == "remove":
            if ident in listed:
                listed.remove(ident)
                changes: dict[str, object] = {"favorite_airports": listed}
                if settings.home_airport == ident:
                    # Home passes to the favorite added most recently before it.
                    changes["home_airport"] = listed[-1] if listed else None
                apply_changes(changes)
        else:
            return done("Nothing to do.")
        return done()

    @app.post("/api/aircraft")
    def change_aircraft():
        action, typed = text("action"), text("entry", 40)
        listed = list(get_state().settings.favorites)
        entries, rejected = favorites.parse(typed)
        if rejected or len(entries) != 1:
            return done("That is not a registration, callsign or hex code.")
        entry = entries[0]
        key = favorites.normalize(entry)
        present = [e for e in listed if favorites.normalize(e) == key]
        if action == "add":
            if not present:
                if len(listed) >= favorites.MAX_FAVORITES:
                    return done(f"At most {favorites.MAX_FAVORITES} favorites.")
                apply_changes({"favorites": [*listed, entry]})
        elif action == "remove":
            if present:
                apply_changes({"favorites": [e for e in listed if e not in present]})
        else:
            return done("Nothing to do.")
        return done()

    @app.post("/api/pin")
    def change_pin():
        """Pin a favorite aircraft's track, or unpin it."""
        entry, on = text("entry", 40), body().get("on")
        settings = get_state().settings
        if entry not in settings.favorites or not isinstance(on, bool):
            return done("That is not one of the favorite aircraft.")
        pinned = [e for e in settings.pinned_favorites if e != entry]
        apply_changes({"pinned_favorites": [*pinned, entry] if on else pinned})
        return done()

    @app.post("/api/receiver")
    def change_receiver():
        changes, errors = validate({"local_receiver_url": text("url")})
        if "local_receiver_url" in errors:
            return done(errors["local_receiver_url"])
        apply_changes({"local_receiver_url": changes["local_receiver_url"]})
        return done()

    @app.post("/api/reset")
    def reset_all():
        if reset is None or body().get("confirm") is not True:
            return done("Not confirmed.")
        reset()
        return done()

    @app.post("/api/clear-caches")
    def clear():
        if clear_caches is None or body().get("confirm") is not True:
            return done("Not confirmed.")
        clear_caches()
        return done()

    return app


class PortalServer:
    """Runs the portal in a background thread."""

    def __init__(self, app: Flask, port: int = DEFAULT_PORT) -> None:
        self._app = app
        self._requested_port = port
        self._server: BaseWSGIServer | None = None
        self.port: int | None = None  # the port actually bound, once started

    def start(self) -> bool:
        """Bind and start serving. Tries the fallback port if the first is unavailable
        (binding port 80 needs the installer's sysctl change). Returns False if neither binds."""
        for port in dict.fromkeys((self._requested_port, FALLBACK_PORT)):
            try:
                self._server = make_server("0.0.0.0", port, self._app, threaded=True)
            except (OSError, SystemExit) as exc:
                log.warning("portal could not use port %d: %s", port, exc)
                continue
            self.port = self._server.server_port
            threading.Thread(target=self._server.serve_forever, name="portal", daemon=True).start()
            log.info("portal listening on port %d", self.port)
            return True
        return False

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

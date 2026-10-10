# FlightTrack

A desktop air-traffic display: a Raspberry Pi 5 and a 7" touchscreen in a printed case, showing live aircraft on a radar-style map. [PLAN.md](PLAN.md) is the design (A plan, B software, C case and hardware); [README.md](README.md) is the build guide. **When something is built or a decision changes, update PLAN.md (and the README if it affects a builder) in the same change.** PLAN.md describes the result, not how it was arrived at.

Details of our own machines (hostnames, deploy commands, location) are in `LOCAL.md`, which is git-ignored: read it before deploying or testing on hardware, and keep anything specific to our own network, location or accounts out of committed files.

## Working rules

- **Plan before building** anything substantial, and get the user's sign-off on the plan.
- **Never claim something works on the Pi unless it was run on the Pi.** Say "tested on macOS only" when that is the truth, and list what only hardware can confirm (touch, backlight, autostart, audio, frame rate) for the user to check.
- Don't run `install.sh` or any `sudo` or system-modifying command on the development Mac.
- Python 3.11+, type hints, ruff (line length 100), pytest. Shell: `bash`, `set -euo pipefail`, shellcheck-clean, idempotent.
- Keep dependencies few (pygame-ce, requests, Flask). Each new one needs a reason in the commit message. Record third-party terms (data, tiles, photos, sounds, icons, fonts) in `NOTICE`, and sources in `credits.py`, as they are adopted.
- All code, assets and text are written fresh; nothing is copied from other projects or vendor imagery. The name is written **FlightTrack** (environment variables apart).

## Hardware facts (measured on the Pi)

- Raspberry Pi OS (Debian 13 trixie), Python 3.13, labwc on Wayland. SDL picks the **x11** driver (XWayland); fullscreen covers the taskbar.
- The display is `DSI-1`, 800x480 at 60 Hz. Touch arrives as **mouse events, no finger events**: everything must work as a tap or a drag. Backlight `10-0045`, max 255, writable by the `video` group.
- Logs: `journalctl --user-unit flighttrack` (not `--user -u`, which finds nothing).
- No panel-specific code, no display configuration, no switching the session type, no reboot for display reasons. Read the screen size at run time.

## Code rules

- **Main thread only touches pygame**, and never waits on the network, the disk or image decoding. Workers hand over plain data or raw bytes. Start only the pygame modules in use, never `pygame.init()`.
- Keep logic testable without a display: geometry, tile math, feeds, settings are plain Python with no pygame import. UI code stays thin.
- **Touch first**: 48 px targets, no hover, no keyboard needed; text is typed with `ui/keyboard.py`. Sliders move only on a sideways drag that starts on their track, never on touch-down. Destructive actions use `ScrollScreen.confirm` (two taps, 0.6 to 5 s apart).
- **Every setting** is a field of `settings.Settings`, applies at once, persists, and is covered by the test in `tests/test_ui.py` that fails for a new field. Reset keeps only what is in `settings.KEPT_ON_RESET`. Anything a setting affects is re-derived in `App.configure_feeds()`, so changes apply without a restart.
- **Keep the web page and the display's settings page in sync**: a setting, row, wording, icon or behavior changed on one is changed on the other in the same change. Deliberate differences: Wi-Fi and Shut down only on the display, Preview only on the web.
- **Portal security**:
  - `POST /api/set` takes only `portal.SWITCHES` and `settings.FRACTION_SETTINGS`; anything else has its own validating route. No general "set any setting" route.
  - The portal never touches `App.settings` or the file: it calls `App.apply_changes`; web changes reach the widgets through `App.catch_up()`.
  - Validate in `portal.validate()`, which the display uses too.
  - Origin check on every state-changing route; Jinja autoescaping on; never mark user text safe.
  - Everything but `portal.OPEN_ENDPOINTS` needs the password.
  - It exposes nothing beyond app settings; Wi-Fi and shutting down are never on it.
- **Secrets** (`settings.SECRET_FIELDS`) live only in `secrets.json` (0600, data directory 0700), are read only from there, reach the web only masked (`secrets_store.masked`), and are **never logged**. The password is kept only as a scrypt hash. The preview is blanked while the keyboard is up (`App.typing_in_private`).
- **Every request to a public service** goes through `net` (the shared rate limiter and User-Agent). Never poll faster than a provider allows; back off on errors. No required API keys: keyed services are optional upgrades.
- **Tiles and caches**: memory first, the SD card as little as possible. A new cache must be added to `App._measure_storage` and `App.clear_caches`. Downloaded images are checked for type and size; uploaded ones (when built) must be re-encoded with Pillow.
- **Frame cost**: work that changes only when the feed answers goes in `scope.look`, not per frame. Overlays are keyed on `App.anchor()`, never the exact view center. Range rings keep their opacity baked into the layer's alpha: no per-frame `set_alpha` on a per-pixel-alpha surface (2.5 ms of a 3.1 ms frame on the Pi). Animation must keep `App._active_until` ahead of the clock, or the loop drops to 10 fps.
- Keep `DIAG` working: it is how hardware behavior is checked on the Pi.
- **Colors come from the theme** (`themes.py` for the palettes, `ui/theme.py` for the roles in force). Read them as `theme.X` when drawing, never as a default argument or a module constant (they change with the theme). Text on a lit fill uses `ON_ACCENT`; text over the map uses `MAP_TEXT` with `shadowed_text`. A new color role goes in every theme, and `tests/test_themes.py` holds every theme to its contrast and color-blindness rules.
- `install.sh` lives in functions with `main "$@"` on the last line (`update` replaces the script while it runs). It prints every system-level change and never touches the hostname, display configuration or session type.

## Commands

From the repo root; the dev venv is `.venv`.

```bash
.venv/bin/pip install -e '.[dev]'                   # set up / refresh
.venv/bin/python -m flighttrack                     # 800x480 window; live if a home is set
.venv/bin/python -m flighttrack --replay            # built-in demo traffic
.venv/bin/python -m flighttrack --replay-file F     # another recording
.venv/bin/python -m flighttrack --portal-port 8765  # web page on an unprivileged port
.venv/bin/python -m flighttrack --no-map            # fetches no tiles
.venv/bin/pytest                                    # headless, SDL dummy driver
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/shellcheck install.sh scripts/*.sh
.venv/bin/python scripts/make_sample_replay.py      # regenerate feeds/data/sample.json
.venv/bin/python scripts/make_airports.py           # regenerate the OurAirports tables
.venv/bin/python scripts/make_icon.py               # after changing the mark in ui/brand.py
python hardware/case/case.py                        # the case STLs (needs manifold3d, numpy)
```

- `FLIGHTTRACK_DATA_DIR` overrides the data directory (default `~/.local/share/flighttrack`): use it so dev runs don't touch real settings.
- To look at a frame without a window: `SDL_VIDEODRIVER=dummy` and `pygame.image.save(app.screen, path)`.
- Generated files (`feeds/data/sample.json`, `data/*.csv.gz`, the icons, the STLs) are never edited by hand.

## Tests

- Tests never call live services. `App(...)` takes `locate=`, `portal_port=`, `tile_store=`, `airspace=`, `wifi=` and the like, all off by default, so tests make no network calls, bind no ports and run no commands; `main()` turns them on. `tests/conftest.py` turns off the rate limiter and the web password.
- Fixtures in `tests/fixtures/` are real recorded responses with positions shifted and timestamps zeroed so they don't reveal where they were captured. Keep it that way.

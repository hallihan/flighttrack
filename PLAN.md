# FlightTrack plan

What FlightTrack is, how it is built, and why. In three parts: **A** the purpose and the plan, **B** the software, **C** the case and hardware. [README.md](README.md) is the build guide; this is the design. Millimeters, nautical miles and feet throughout.

---

## A. Purpose and plan

### A1. What it is

A standalone desktop air-traffic display: a Raspberry Pi 5 with a 7" touchscreen, in a printed case on a desk, showing live aircraft around a home location on a radar-style map. It works like the commercial desktop air-traffic displays sold to aviation enthusiasts, but nothing from them is copied: all code, artwork and text are original, and anything third-party is permissively licensed and recorded in [NOTICE](NOTICE).

It is a hobby display: **not for navigation**, which the README and the About tab say.

### A2. Requirements

1. A radar-style view centered on a location: range rings, aircraft drawn by type and rotated to their heading, labels.
2. Map backgrounds (satellite, VFR chart, street) with optional rain radar and cloud.
3. Ranges of 1, 2.5, 5, 10, 25, 30, 40, 50, 100 and 250 NM. 250 is the largest radius the public feeds answer in one request.
4. For each aircraft: callsign, altitude, ground speed, heading, vertical speed, squawk, distance and bearing, type, registration, operator, route and photos where known.
5. Works anywhere in the world: nothing US-only on the critical path.
6. Everything used day to day is on the touchscreen; setup is also on a web page.
7. **No account or key is needed.** With only a location, it shows traffic. Keys for services that offer more are optional.
8. Install by cloning the repository and running one script; update with one command.

### A3. Hardware and OS

- Raspberry Pi 5; a 7" DSI touch display, 800 x 480, that the OS recognizes with no setup (the case is made for the Freenove FNK0078, see C1); the official 27 W supply.
- The standard Raspberry Pi OS 64-bit desktop image, with desktop autologin (its default), Wi-Fi and SSH set when the card is written. The app runs inside that desktop session (labwc on Wayland, through XWayland), fullscreen.
- Rules that follow: no panel-specific code and no display configuration; read the screen size at run time; **single touch only** (every action is a tap or a drag, which arrive as mouse events); touch targets at least 48 px, no hover, no keyboard needed for anything (the app has its own on-screen keyboard); the backlight is found under `/sys/class/backlight/`, never named.

### A4. Plan

| Phase | Contents | State |
|---|---|---|
| A | This plan, the README, license and notices | Done |
| B | The software (part B) | Done, running on the Pi |
| C | The case and stands (part C) | Done, printed and in use |

Not yet built, and open:

- **Alerts**: a chime and a visual when an aircraft enters the range, and a distinct one on squawk 7500, 7600 or 7700 or a feed's emergency flag. Sound would go to the desktop's default audio output (in practice a Bluetooth speaker), always optional.
- Uploading custom aircraft pictures through the web page (they can be put in a folder now, B4.7), metric units, and screen rotation as settings.
- Memory over a long run: tiles are kept in memory up to a cap (B7), so use should level off at about 350-400 MB. Not yet confirmed over a day.
- The ADS-B Exchange key's address (RapidAPI) is as documented but unconfirmed with a key.

---

## B. Software

### B1. Architecture

One Python process, fullscreen in the desktop session.

```
main thread      pygame-ce: events, drawing, touch
feed pollers     one thread per source: area feed, local receiver, watch (B4)
fetch workers    map and weather tiles, airspace, aircraft details, photos, METAR
portal thread    the web settings page (Flask), on the local network
        all share a thread-safe store; settings persist to JSON
```

- Only the main thread touches pygame. Workers hand over plain data or raw bytes; they never draw, and nothing on the main thread waits on the network or the disk. Only the pygame modules in use are started (never `pygame.init()`).
- State lives outside the checkout, in `~/.local/share/flighttrack/` (settings, secrets, caches, pictures), so an update can replace the code freely.
- Hardware sits behind small interfaces (backlight, Wi-Fi, power) that report themselves unavailable where their tools are missing, so the app also runs in a window on macOS, where it is developed.
- Package layout: `app.py` (main loop), `ui/` (screens and drawing), `geo/` (distances, Web Mercator, the sun), `feeds/` (positions), `tiles/` (map and weather tiles), `portal/` (web page), `platform/` (hardware), and modules for settings, the store, airports, airspace, enrichment, keyed services, secrets and the network limiter.

### B2. The screen

At 800 x 480: a 48 px top bar, a 592 x 400 map, a 208 px side panel and a 32 px bottom bar.

- **Top bar**: the mark and name; chips for what the map shows (`MAP`, `RAD` rain radar, `CLD` cloud, `AIRSPC` airspace, `KEY` the radar's color key); four **filter chips**, the legend of aircraft colors (`<10k`, `10-30k`, `>30k`, `FAV`), each hiding its group of traffic everywhere when tapped (emergencies, ground traffic and the selected or pinned aircraft are never hidden); and the count of aircraft in range with the data's age. A chip never changes its label.
- **Map**: two range rings round the center, the outer one the selected range, pale yellow so they are never taken for airspace, and faded by a setting. The view is wider than the rings, so traffic beyond the range still shows at the sides. Range `-`/`+` (and `HOME` while panned) at the lower left, `GO` and the settings cog at the lower right, all see-through so the map shows beneath them.
- **Side panel**: the nearest airborne aircraft in range, or the selected aircraft or airport (B5), or the "which one?" list when a tap lands on several things.
- **Bottom bar**: the range, the Wi-Fi network in use, when the data last arrived (with a green, amber or red dot), the data credits, and a `DIAG` pill for diagnostics (surface, video driver, frame rate, pointer events, backlight, feeds), which is how behavior on the hardware is checked. While diagnostics are open the map is tinted with how low the feeds hear aircraft, in feet above the ground (B4.6), with its key along the bottom.
- **Themes** (`themes.py`): **Dark** (the default: graphite and amber), **Light** (chart paper and sectional blue), **Contrast** (black, white and yellow), **Midnight** (indigo and coral) and **Ember** (warm and dim, for a dark room). Chosen on the Display tab or the web page, which follows the same theme. A theme sets the chrome and the traffic colors; what describes the ground (airports, runways, airspace, text over the map, which has a dark edge so it reads on any background) is the same in all of them.
- **Traffic colors**: one for each altitude band (below 10,000 ft, to 30,000 ft, above), one for favorites, gray on the ground, red for an emergency, which wins over all. In every theme they are held to rules (`tests/test_themes.py`): any two stay at least 12 apart (CIEDE2000) under normal vision and simulated deuteranopia, protanopia and tritanopia; the altitude bands step down in lightness from low to high, which reads the same for everyone; each is at least 3:1 on dark imagery. Chrome text is at least 4.5:1 (WCAG AA), and a traffic or status color used as text on the chrome is adjusted to that.
- **Aircraft** are drawn as one of twelve silhouettes chosen from the ICAO type (widebody, airliner, regional jet, business jet, turboprop, light twin, high wing, low wing, composite, helicopter, heavy helicopter, unknown), falling back to the emitter category, then to an arrow; rotated in 5-degree steps. Ground traffic is a small unlabeled dot, left out of the list and the count; from 100 NM up it is hidden and icons shrink.
- **Labels**: the selected aircraft first, then favorites, then nearest first; each to the right of its aircraft, or the left when the right is taken, or left out. A label keeps its side while it can, changes side at most every 3 s, slides across when it does and fades in and out, so labels do not jump when two aircraft fly side by side.

### B3. Map and weather

- **Projection**: Web Mercator, the tiles' own, at a fractional zoom that makes the selected range fill the outer ring, so aircraft sit exactly on the ground beneath them. Distances shown are great-circle.
- **Layers**, bottom up: the map (dimmed by day and night), cloud, rain radar, place names, then airspace and runways painted in.
  - **Three maps**: satellite (Esri World Imagery with Esri's boundaries and place names), VFR sectional chart (the FAA's tiles over the imagery, US only, to about 50 NM), and street (Esri). Chosen on the Map tab or by a long press on `MAP`; a tap turns the map off and on.
  - **Rain radar**: the Iowa Environmental Mesonet's NEXRAD composite over the contiguous US (standard US colors), RainViewer elsewhere (up to zoom 7). **Cloud**: NASA GIBS GeoColor from the geostationary satellite covering home, blended so that only bright cloud shows. Weather layers refresh every few minutes; the last tile drawn stays until its newer one arrives, so the weather never blinks out.
  - **Airspace**: class B, C and D from the FAA's public service (US only), at 50 NM and closer, with altitude limits at 25 NM and closer, in dull colors that are never taken for aircraft. **Runways** to scale from the bundled OurAirports data, at 5 NM and closer.
  - **Day and night**: the sun's elevation at the center of the view shades the map through civil twilight, and with the setting on, halves the backlight at night.
- **Composing**: tiles are decoded on the main thread, three at most a frame, and composed with the overlays into one picture larger than the view by 112 px all round. A drag or a followed aircraft slides that picture; it is rebuilt only when the margin runs out, tiles arrive or the layers change, and the overlays are chosen round a point that trails the view, so they do not change as the view creeps. A frame is then a single blit.
- **Tiles** (`tiles/store.py`): three workers. Memory first, to spare the SD card: tiles are kept in memory up to an eighth of the memory free at start-up (16 to 128 MB). Map tiles are also written to disk once, kept for 30 days, and pruned oldest first; all caches together are held to the lesser of 32 GB and half the card's free room. Weather tiles stay in memory. Failed tiles are retried after 30 s, and the map says "Loading map..." and then "Map unavailable" while it has holes. When the view is still, the tiles round it and for the next range out are fetched ahead of time. While a tile is on its way, the same ground from a neighboring zoom level is drawn in its place, so changing range never shows a hole.
- **FAA data** goes out of date on the FAA's calendar (a new edition every 28 days for airspace and 56 for charts), not by age.

### B4. Aircraft data

1. **The area feed**: [adsb.fi](https://adsb.fi)'s open data API, with [adsb.lol](https://adsb.lol) if it fails. The query is sized to fill the view's corners and asked every 5 s while the query's radius is up to 100 NM (the 50 NM range), 10 s above that, 15 s once it reaches the 250 NM cap, sooner after a change of range, and backs off to 60 s on failure. Positions are dead-reckoned between answers.
2. **A local receiver** (optional): the `aircraft.json` of a readsb or tar1090 receiver on the network, every second, **merged** with the public feed per aircraft (the freshest position wins, gaps are filled from the other). Its status is shown under its address.
3. **The watch**: every 10 s, one request (to adsb.lol, adsb.fi as a fallback) for particular aircraft wherever they are: the selected one and the favorites (registrations turned into hex codes once; callsigns once seen flying). So an aircraft stays current when the map is dragged away from it.
4. **Staleness**: once the newest data is more than 20 s old, traffic is drawn gray and a banner gives its age; an aircraft unheard for 60 s leaves the map. The watch and looked-up flights do not count toward the data's age, so they cannot hide a failing feed. With no location the app shows a labeled demo.
5. **Tracks**: the store records a point every 0.05 NM for every aircraft, with its time (the last 600, and what comes before them thinned to 900, keeping the first). On the way out the display keeps the tracks of the aircraft heard in the last ten minutes, so after a restart each takes up where it was. When one is selected (and again after ten minutes) its earlier flight is gathered from every source there is: adsb.lol's full and recent traces, the local receiver's history; and, only while those do not reach back to where it took off, OpenSky's track, then FlightAware's (paid by the request: at most hourly, within its daily cap), whose flight record also says where and when it left the ground. The pieces are merged into one timeline and followed back from what the display is recording to where the flight began (`legs.py`):
   - a point on the ground is the takeoff;
   - so is the first point heard low beside an airport and climbing, low meaning down where the feeds stop hearing aircraft there (B4.6);
   - a hole of up to 10 minutes is always the same flight, one of over 2 hours never is;
   - in between, it is the same flight only if the aircraft covered ground across it at least half as fast as it was flying either side (one that landed and waited covers far less), and not impossibly fast (a bad position).

   Tracks are drawn in the altitude colors.
6. **How low the feeds hear** (`reach.py`): a map learned from every feed answer, in cells about 3 NM across, kept **for each provider separately** (adsb.fi, adsb.lol, ADS-B Exchange, the local receiver): their receivers differ, and a local receiver may be off. For each cell and provider, the lowest altitude at which each of the last six distinct aircraft was heard; the provider's floor there is the second lowest once three have been, or the ground once three have been heard on it. Kept on disk, so it improves over weeks; reports older than 60 days are let go (Clear caches leaves it: it cannot be fetched again).
   - It decides whether a track's first point near an airport is a takeoff into hearing or just an aircraft passing over. Judged by the providers that have answered in the last minute, the lowest of their floors: near an airport and no more than 800 ft above that floor (where nothing is learned yet, 1,500 ft above the field; where the ground is heard, 300 ft), it is.
   - Shown while diagnostics are open: the lowest of all the providers' floors, above the ground. Feed altitudes are above sea level (barometric, so a few hundred feet out with the weather); each cell with a floor gets the height of the ground at its middle from Open-Meteo's elevation API (Copernicus DEM, 90 m), a hundred cells a request, at most one request a minute, kept with the map. Diagnostics also count the cells known, in all and for each provider.
7. **Details of the selected aircraft**, asked once on a worker thread and kept on disk: route and airframe from [adsbdb.com](https://www.adsbdb.com) (where a flight with that callsign is expected to go, and the type and owner), up to two photos from adsbdb and [airport-data.com](https://www.airport-data.com) with their photographers credited, a tap to see one full screen. Pictures of your own in `images/` in the data directory, named for a registration, hex code or type, take precedence. With no route, the pane says where the track starts: "From KPAO" if it took off there, else "First seen 20 NM NE KSJC".
8. **Last sightings**, for a flight that is not live: the latest of this display's memory of the last 5,000 aircraft, the local receiver's own history (tar1090 keeps about 8 hours), and adsb.lol's traces for the last few days.
9. **With keys** (all optional, each within its own daily cap, B6):
   - **OpenSky Network** (a free account, OAuth2 client credentials): the selected aircraft's track when adsb.lol has none, and where an aircraft last landed.
   - **FlightAware AeroAPI** (Personal tier): the flight the selected aircraft is actually on, its origin, destination and operator, in place of the expected route. At most 30 requests a day (inside the free monthly credit), each kept 30 minutes.
   - **ADS-B Exchange** (Community API): the area feed's last resort after adsb.fi and adsb.lol, within its 10,000 requests a month.

### B5. Using it

- **Selecting**: a tap within reach of one aircraft or airport selects it; one clearly nearest is taken without asking; otherwise the panel lists what was there. The reach shrinks with the icons at wide ranges. A tap on empty map, or "Back to list", clears it.
- **The selected aircraft's pane**: its figures two to a row, type and registration, operator and route, photos when there is room, and three buttons along the top: the **heart** (favorite), the **pin** (keep its track on the map when not selected; only on a favorite, so that a pinned aircraft can always be found on the settings pages to unpin, and dimmed on anything else) and the **eye** (follow: the view keeps it centered, and distances are then given from home). An aircraft that stops being heard **stays selected**, drawn gray where it was last heard of, with "NOT UPDATING" and for how long; following holds there and resumes if it is heard again.
- **Favorites**: aircraft (registrations, callsigns or hex codes) are drawn in magenta and always labeled; their pins can be saved. Airports are gold on the map. "Current Location" (where the internet connection says the display is) is always the first favorite airport.
- **Home** is the favorite airport chosen most recently, or the current location; with exact coordinates in the settings file, those. Changing home re-centers the display.
- **Panning**: a drag moves the view, and everything follows the center of the view: rings, distances, the list, the feed's query, weather and shading. The position is kept across restarts until `HOME`.
- **Airports**: a tap shows the airport with its two longest runways and its current METAR (from the Aviation Weather Center), with its flight category. Markers show every airport at 10 NM and closer, larger ones further out, and favorites always.
- **GO**: a full-screen search with the keyboard, three suggestions that update as you type, and the favorites (airports and aircraft in two columns, scrolling together). A tap only chooses; the keyboard's GO key acts. An airport is shown and selected; a flight is centered, selected and followed; a flight not on the map is found worldwide, or where it was last seen (B4.8), and GO is grayed out while it looks and if nothing is found.
- **Keyboard**: a cursor moved by arrow keys or a touch, keys that repeat while held, shift and caps lock, and a separate Cancel that asks once something is typed. Passwords and keys are typed as dots unless Show is tapped.

### B6. Settings

- **On the display**: the cog opens a page of eight tabs, so nothing is more than a tap away:
  - **Display**: the theme, screen and map brightness, ring and map-feature opacity, day and night.
  - **Map**: map on or off, kind of map, radar, cloud, weather opacity, airspace, runways.
  - **Airports**: the favorites, each with a house (filled for home; tap to make home), its flight category, and a bin; "Current Location" first, never removable; a **+** to add.
  - **Aircraft**: the favorites, each with its pin, where it was last seen ("KPAO, 2h ago" or "20 NM NE KSJC, now"), and a bin; a **+** to add.
  - **Feeds**: the local receiver, and the optional keys (a pencil to enter one, a bin to remove it), with each service's status; and **requests today** for every service the display uses, against its allowance where known, updated live.
  - **System**: Wi-Fi, the web page's address and password, shut down, reset all settings.
  - **Storage**: free space and what each cache uses, and clearing them.
  - **About**: version, the not-for-navigation notice, and every data source.
- **On the web page** (`http://<hostname>.local`, port 80, or 8080 if 80 is not allowed): the same tabs, rows, icons and wording (except Wi-Fi and shut down, which stay on the display), plus a **Preview** tab, a picture of the screen about once a second. It needs the **password**, which is set on the display; without it, the page is only the preview and a sign-in box, and nothing else answers. The preview is blanked while a password or key is being typed on the display. Changes made on either side show on the other within seconds. Every change is a small request that is checked; cross-site requests are refused.
- **Confirmations**: anything destructive takes two taps, the second neither instant (a bounce) nor more than 5 s later.
- **Persistence**: every setting is kept, and applies at once; on the display a change is written up to five minutes later, to spare the SD card, and always on the way out. Reset puts everything back except what was typed in (home, favorites and their pins, receiver, keys, password).
- **Secrets**: keys and the password's hash are kept in `secrets.json`, readable by the display's user only, in a folder only it can enter, and shown only as dots and their last four characters (OpenSky's client ID, not a secret, in full). The password is kept only as a salted scrypt hash. Signing in sets a cookie for 30 days; a new password signs everyone out; five wrong tries in a minute and a visitor must wait.
- **Wi-Fi** through NetworkManager: scan and join, saved networks (rejoined without asking, retried for ever), add a hidden one by name. **Shut down** when the system allows it.

### B7. Being a good client

- Every request to a public service goes through one **rate limiter** shared by every thread: a minimum gap per host (1.1 s for adsb.fi, 1 s for other APIs, 6 s for AeroAPI, a short gap for map tiles). The local network is not limited. It also counts each service's requests for the Feeds tab.
- A User-Agent on every request, caches capped by size, backing off on failure, no polling faster than a provider allows, attributions on screen and in [NOTICE](NOTICE).

### B8. Install and run

- `install.sh` installs the system packages, makes a virtual environment in the checkout and sets up autostart. It can be run again at any time, prints every change it makes to the system, and never touches the hostname, the display configuration or the session type. `install.sh update` pulls the latest code and runs the install steps again.
- **Autostart**: a desktop autostart entry starts a systemd *user* service when the session begins, so the app has the session's display and audio.
- The installer lets ordinary programs listen on port 80, for the web page, and works round a Raspberry Pi OS Wi-Fi driver fault that stops it joining networks offering both WPA2 and WPA3 (the reboot after installing applies it). WPA3-only networks cannot be joined.

### B9. Performance

- 30 frames a second while the screen is touched and for 1.5 s after (or while a label moves), otherwise 10, sleeping until the next event. Nothing on the display moves when idle.
- What changes only when the feed answers (an aircraft's kind, color and label) is worked out once per answer, not per frame; positions once per frame for the list, taps and the map.
- The rings, the composed map and button labels are cached; a frame with 900 aircraft at 250 NM takes under 7 ms on the Pi.

### B10. Testing

pytest, headless, with recorded responses (positions moved and times zeroed, so they do not reveal where they were recorded) and fakes for every service: tests never call a live service, start the network services or the rate limiter, or need the password. A test fails if a setting is added without being covered. Lint with ruff and shellcheck.

---

## C. Case and hardware

### C1. The display

The **Freenove FNK0078** 7" DSI touchscreen (800 x 480, IPS, 5-point capacitive): it presents itself to the Pi as the official Raspberry Pi 7" display, so it needs no driver, appears as `DSI-1` and has its brightness at `/sys/class/backlight/10-0045`. Power comes from the Pi through two spring pins, so there is one cable. A Backlight button at its top edge steps the brightness; the app follows changes made with it. Its measurements, from calipers since no drawing is published, are in [hardware/case/dimensions.md](hardware/case/dimensions.md): treated as one block 165 x 102 x 9, with four M2.5 corner standoffs 154 x 92 apart and 4 proud of the back, and the Pi mounted on its back with nothing more than 28 behind the board.

### C2. The case

Three prints, the body, the cover and the pair of stands (C3), made by [hardware/case/case.py](hardware/case/case.py) (Python, with manifold3d), which checks its own work: every part one solid, nothing overlapping another part or what is inside, the display going in and the cover going on without touching anything but the walls its catches spring against, the lip clear of the picture, the vents large enough, and the stands working (C3).

```bash
pip install manifold3d numpy
python hardware/case/case.py     # writes body.stl, cover.stl and wedgestand.stl
```

Every number is a named constant at the top of `case.py`; change it there and run it again, never edit the STLs. It prints the sizes, the vent areas and the stand's stability, and writes nothing if a check fails.

| | Value | Why |
|---|---|---|
| Opening for the display | 166 x 103 | the display as a block 165 x 102, 0.5 clear each side |
| Wall | 2.4 | six passes of a 0.4 nozzle |
| Front lip | 1.2 thick | proud of the glass, so the glass never meets the table |
| Space behind the board | 33 | the Pi's tallest parts are 28, plus 5 |
| Overall | 170.8 x 107.8 x 45.2 | 187 wide over the stand knobs |
| Cover edges | 45-degree chamfers, 6 | print without support |
| Post cups | 7.4 across, flared, 1.5 deep | a millimeter clear of the 5.4 standoffs: stops, not locators |
| Power opening | 18 x 11, round the USB-C socket | passes the plug, so it goes in with the case closed |
| Backlight paddle | 24 long, full wall thickness | about 1.3 N to press |

- **Body**: the front lip and four walls in one piece, printed face down so the front is the smooth bed surface. The display goes in from the back, glass first, with 0.5 clearance all round; the walls are 2.4. The lip overlaps the display unevenly (2 at the top and left, 5.5 at the right, 11 at the bottom), which hides the panel's own uneven black border so the picture looks centered. The bottom 2 of the display is where its ribbon cables fold, so the lip and the floor are relieved there, and the display's weight rests on the edge of its board. The bottom front edge is bevelled.
- **Cover**: a dish with 45-degree edges that closes the back; the case is 45.2 deep overall, with 5 clear over the Pi (room, on paper, for the official Active Cooler). Four posts stand on the display's corner standoffs, so a push on the screen goes into the case, not the Pi; each ends in a cup with room for a foam dot, and is a tube for an optional **M2.5 x 4** screw into the standoff. Four sprung tabs snap into windows in the body's top and bottom walls; pressing them in through the windows releases the cover.
- **Openings**: the power cable goes straight up through the top wall above the Pi's USB-C socket, and plugs in with the case closed. The Backlight button is pressed through a paddle cut from the top wall above it (printed held by a sliver, cut after printing). USB, Ethernet and the SD card need the cover off.
- **Cooling**: slots in the top and bottom walls and the cover's edges, and a field of slots in the back over the processor, about 13 cm2 open below, 12 above and 9 in the back, for air to rise through the case. In the case on a desk, after several hours, the Pi runs at about 71 C, under the 80 C where it slows itself.
- **Clear of the cable**: nothing of the cover enters the space where the display cable loops beside the Pi.

### C3. The stands

A left and a right stand, mirror images, one print. Each hangs on a knob on the side of the body: it slips over the knob's ears pointing straight forward (the only way its keyway lines up) and swings down and back. Its arm runs down to the table 20 degrees behind vertical, braced by an arc at the elbow, and a foot runs forward along the table to a **cradle**: a wedge under the front edge with a 5 lip in front of the bezel, a floor under the bottom, and a hook round the bottom back edge, 6 up the cover's bevel (notched round a cover post that stands a fraction proud there). At the end of the swing the hook has to pass that edge: the stand gives about 2 and snaps on as it meets a stop peg on the body. Then the peg and the lip stop it going further back, the hook stops it swinging out, and the ears keep it on the knob.

Standing, the case leans back 30 degrees with its front edge 25 above the table; its weight is 43 behind the front of the wedges and 59 in front of the heels, and about 2.9 N square on to the top of the screen tips it backward. A rubber foot under each stand stops it sliding.

### C4. Printing

PLA or PETG (the case in use is PLA), 0.2 layers, three walls. The body front face down with an 8 mm brim and supports from the build plate for the knobs (it is 187 wide over them, so it needs a 190 mm bed); the cover back down; the stands flat as supplied. No other supports: the openings are short bridges.

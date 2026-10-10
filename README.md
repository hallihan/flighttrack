# FlightTrack

A desktop air-traffic display you can build yourself: a Raspberry Pi 5 and a 7-inch touchscreen in a 3D-printed case, showing live aircraft around your location on a radar-style map. No subscription, no phone app, and no ADS-B receiver needed (one can be added).

> For hobby and display use only. Not for navigation, flight safety or any operational use.

It shows aircraft as silhouettes of their type over a satellite map, VFR chart or street map, with rain radar, cloud, airspace and runways, at ranges from 1 to 250 NM. Tap an aircraft for its altitude, speed, route, type and photos, and its track back to takeoff; follow it, pin its track, or make it a favorite. Settings are on the touchscreen and on a password-protected web page. How it is designed is in [PLAN.md](PLAN.md).

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 5 | Any memory size; 2 GB is plenty. |
| [Freenove FNK0078 7" DSI touchscreen](https://www.amazon.com/dp/B0B44VZTRG) | 800 x 480, IPS, capacitive. It comes with the cable for the Pi 5 and the standoffs and screws to mount the Pi on its back. The case is made for this display. |
| Raspberry Pi 27 W USB-C power supply | The official one: the Pi 5 slows down on weaker supplies. |
| microSD card | 32 GB or more, A1 or A2. |
| A heatsink for the Pi 5 | A stick-on one is enough. The case leaves room for the official Active Cooler, but that has not been tried. |
| PLA or PETG filament | About 150 g, for the three printed parts. The prototype is PLA. |
| Optional | Four M2.5 x 4 mm screws (pan, button or socket head) to hold the display to the case; four small foam dots; four stick-on rubber feet for the stands. |

A computer with an SD card slot, for flashing the card, and a Wi-Fi network for the display.

## 1. Print the case

The parts are in [hardware/case/](hardware/case/), each already turned the way it should sit on the bed. PLA or PETG, 0.2 mm layers, three walls.

| Part | Size on the bed | How |
|---|---|---|
| `body.stl` | 187 x 108 x 39 mm | Front face down, 8 mm brim, supports touching the build plate only (for the knobs on the sides). It needs a bed at least 190 mm wide. |
| `cover.stl` | 171 x 108 x 32 mm | Back down, no supports. |
| `wedgestand.stl` | 158 x 156 x 30 mm | A left and a right stand, as supplied, no supports. |

The model is parametric: `hardware/case/case.py` makes all three and checks them. See [PLAN.md](PLAN.md#c-case-and-hardware) to change it.

## 2. Prepare the SD card

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

1. Choose **Raspberry Pi 5**, **Raspberry Pi OS (64-bit)** (the desktop version) and your SD card.
2. When asked about OS customisation, choose **Edit settings** and set:
   - a hostname, for example `flighttrack`;
   - a username and password;
   - your Wi-Fi network, its password and your country;
   - your time zone;
   - on the Services tab, **Enable SSH** with password authentication.
3. Write the card. It goes into the Pi during assembly (next): once the case is closed the card cannot be reached.

## 3. Assemble

1. On the body's top wall, near the right-hand end as you look at the front, a paddle is held by a thin sliver at its free end for printing: cut the sliver with a blade. The paddle then presses the display's Backlight button.
2. If the display's own two stands are fitted, unscrew them from its lower corner standoffs: the case's cover rests on those standoffs.
3. Stick the heatsink on the Pi's processor, put the SD card in the Pi, and screw the Pi to the four brass standoffs on the back of the display. Connect the display cable from the display to one of the Pi's two `CAM/DISP` sockets. The display's connector latch is fragile: open and close it at both ends at once.
4. Fold the display cable flat against the display's board, in the space beyond the Pi's microSD end, and no higher than the Pi's USB sockets: the case leaves room for it there and nowhere else.
5. Lay the body face down and lower the display into it, glass first.
6. If you have foam dots, put one in the cup at the end of each of the cover's four posts.
7. Press the cover on until its four tabs click into the windows in the body's top and bottom walls. If you have M2.5 screws, drop one down each post and screw it into the display's corner standoff, snug but not tight.
8. Fit the stands: hold each one pointing straight forward, slip it over the knob on the side of the case, and swing it down and back. At the end of the swing it snaps onto the bottom edge of the case. Stick a rubber foot under each.
9. Plug the power cable in through the opening in the top. The Pi starts; the first start takes a few minutes and ends at the desktop.

To take it apart: unplug it, pull each stand's foot forward to unsnap it, swing it up and lift it off; press the four tabs in through their windows and lift the cover; push on the glass to take the display out.

## 4. Install FlightTrack

From your computer, log in to the Pi over SSH, with the username and hostname you chose (`.local` names need mDNS: built into macOS and most Linux; on Windows, Bonjour, which comes with iTunes and some printer drivers, or use the Pi's IP address from your router):

```bash
ssh <username>@flighttrack.local
```

Then on the Pi:

```bash
git clone https://github.com/hallihan/flighttrack ~/flighttrack
cd ~/flighttrack
sudo bash install.sh
sudo reboot
```

The installer lists every change it makes. After the reboot the display starts by itself within about 20 seconds of the desktop appearing.

## 5. Set it up

- **Where it is centered.** At first the display uses the rough location of your internet connection. To center it on an airport, open the settings (the cog), go to **Airports**, tap **+**, find the airport and tap ADD, then tap its house to make it home.
- **The web page.** On the display, go to **Settings > System > Web page password** and choose a password. Then open the address shown on that tab (for example `http://flighttrack.local`) on a computer or phone on the same network, and sign in. The web page has the same settings as the display, and a live picture of its screen.
- **Optional:** your own ADS-B receiver (**Feeds**: the address of its `aircraft.json`), and keys for OpenSky, FlightAware AeroAPI or ADS-B Exchange (**Feeds**) for more history, routes and a backup feed. None is needed.

## Using it

- **Tap an aircraft** (or a row in the list) for its details. The three buttons at the top of its details: the heart makes it a favorite, the pin keeps its track on the map, the eye follows it. If it stops being heard, it stays selected, marked "not updating".
- **Tap an airport** for its runways and current weather; its heart makes it a favorite.
- **`-` and `+`** change the range. **Drag** the map to look elsewhere; `HOME` comes back.
- **`GO`** finds any airport or flight: type, choose a suggestion or a favorite, press GO. A flight that is not on the map is looked up worldwide, or where it was last seen.
- **The top bar**: `MAP`, `RAD`, `CLD`, `AIRSPC` show or hide the map, rain radar, cloud and airspace; `KEY` explains the radar colors; **press and hold `MAP`** to switch between satellite, VFR chart and street map. The four chips with colored dots are the altitude bands and your favorites: tap one to hide that traffic.
- **The cog** opens the settings. **`DIAG`**, at the end of the bottom bar, shows diagnostics.
- **Settings > System > Shut down** turns the Pi off. To start it again, unplug the power and plug it back in: the Pi's own power button is inside the case.

Aircraft are colored by altitude band (the colored chips in the top bar say which is which), with their own color for your favorites, gray on the ground and red in an emergency. **Settings > Display > Theme** picks the look: Dark, Light, Contrast, Midnight or Ember. Every theme's traffic colors stay distinct for the common kinds of color blindness, and its text meets WCAG AA contrast.

## Updating and troubleshooting

```bash
sudo bash ~/flighttrack/install.sh update   # update to the latest version
systemctl --user status flighttrack         # is it running?
journalctl --user-unit flighttrack -f       # its log
systemctl --user restart flighttrack        # restart it
```

The Pi's built-in Wi-Fi cannot join networks that are WPA3 *only*: switch the router to WPA2/WPA3 mixed mode (on an iPhone hotspot, "Maximize Compatibility").

## Development

Runs on macOS or Linux with Python 3.11 or later, in an 800 x 480 window:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m flighttrack --replay   # demo traffic; Esc quits
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

[CLAUDE.md](CLAUDE.md) has the working rules and the rest of the commands.

## Credits and license

Aircraft positions come from the community networks [adsb.fi](https://adsb.fi) and [adsb.lol](https://adsb.lol); maps from Esri and the FAA; weather from the Iowa Environmental Mesonet, RainViewer, NASA GIBS and the Aviation Weather Center; airports from [OurAirports](https://ourairports.com); ground heights from [Open-Meteo](https://open-meteo.com) (Copernicus DEM). The display's About tab lists every source; their terms are in [NOTICE](NOTICE).

FlightTrack is released under the [MIT License](LICENSE).

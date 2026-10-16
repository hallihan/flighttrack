# Display and Pi dimensions

The measurements the case in `case.py` is drawn from, taken with calipers from a Freenove FNK0078 with a Raspberry Pi 5 on its back (Freenove publishes no dimensioned drawing). Millimeters.

## What the display is

**Freenove FNK0078, 7-inch DSI touchscreen**, the board Freenove's guide "Tutorial_FNK0078 A/B/C" shows: blue, silk-screened "7inch DSI Display, 800X480 Pixel", **Rev0.1** by the lower right standoff. Bought on Amazon as "FREENOVE Touchscreen for Raspberry Pi 5 4 Screen Display Monitor 7 Inch IPS | 800x480 Pixel, 5-Point Capacitive Touch Screen, Integrated Back Mounting, Driver-Free" (ASIN B0B44VZTRG, <https://www.amazon.com/dp/B0B44VZTRG>).

- **Freenove's documents**: <https://github.com/Freenove/Freenove_Touchscreen_Monitor_for_Raspberry_Pi> (an FAQ and three short picture guides: A/B/C, D and H are different board versions; ours is A/B/C). Their files are CC BY-NC-SA: link to them, do not copy them in.
- **Panel**: 7 inch, 800 x 480, IPS according to the listing (Freenove sells TN and IPS versions under the same code; the board's silk-screen does not say which). 5-point capacitive touch.
- **Driver-free**: it presents itself to the Pi as the official Raspberry Pi 7" display, which is why the OS needs no setup, why it appears as `DSI-1`, and why its backlight is the official display's: on the Pi 5 under KMS, `/sys/class/backlight/10-0045` (I2C address 0x45), 0 to 255, as measured. (Freenove's FAQ gives the older name `rpi_backlight`, from before KMS.)
- **Interface**: one MIPI DSI connector on the board, a 15-pin flat-cable socket at the right-hand side seen from the back, with a printed warning: "Push and pull at the same time on both sides. No turn. Fragile snap." The latch is fragile: open and close it evenly from both ends. A Pi 5 (22-pin camera/display socket) needs the 22-to-15-pin cable; an older Pi uses a 15-to-15 one.
- **Power**: from the Pi, through the two spring pins (pogo pins, marked "-" and "+") under where the Pi sits, so there is no second power cable.
- **Backlight button** ("Backlight", at the top edge): a short press steps the brightness up by 10%, back to 10% after 100%; a long press turns the backlight off, and a press turns it on again at the brightness it had. The app follows changes made with it.
- **Standoffs**: four at the corners (anodized aluminium, threaded **M2.5**), which carry the kit's stands on the lower two, and four brass ones inside them that the Pi screws to.
- **Kit**: the display, the Pi's standoffs and screws, two stands (fixed to the lower corner standoffs with screws), and flat cables for the Pi models it supports (Pi 5 and older).

## Conventions

- **Seen from the front** (the glass), unless a line says otherwise.
- X is measured from the **left** edge of the display, Y from its **top** edge. The display is treated as one block 165 x 102 (see below), so its bottom edge is at Y = 102. Depth is measured backward **from the back of the display's board** unless it says "from the glass".
- "Measured" is from the unit. "Official" is from the Raspberry Pi 5 mechanical drawing. "Derived" is worked out from the two. "Estimate" is a reasoned guess, to be treated with a millimeter of doubt.

## Display

Measured:

| | |
|---|---|
| Outline, width x height | 165 x **102**, treated as one block |
| What that block is | the glass is 165 x 100; the board behind is 2 taller at the bottom, and **ribbon cables fold in the gap between glass and board along that bottom edge**. Nothing may press on them, so the case treats the whole depth as 102 tall. |
| Thickness, front of glass to back of board | 9 |
| Black border as measured: top / bottom / left / right | 3 / **12** / 3 / 6 (not precise). The bottom is from the bottom of the block: 10 of glass and the 2 below it. |
| Corner standoffs | 4, one per corner, 5.4 diameter, standing 4 off the back of the board |
| Standoff spacing, center to center | **154 across, 92 up and down** |
| Standoff thread | **M2.5** |
| Backlight button | on the back at the top edge, 7.5 wide, standing 3 off the back of the board; its near end is 17.5 from the **right** edge. Pressed **downward from the top edge**. It does not stick out past the edge of the panel. |

Derived:

| | X from left | Y from top |
|---|---|---|
| Standoff centers (estimate: the measured spacing, centered on the 165 x 102 board) | 5.5 and 159.5 | 5.0 and 97.0 |
| Side of a standoff to the nearest edges, on that estimate | 2.8 | 2.3 |
| Backlight button | 140 to 147.5, center 143.75 | flush with the top edge or just inside it; 0 to 3 behind the board |

**Where the picture is (estimate).** A 7-inch 800x480 panel has an active area of 154.08 x 85.92, which leaves 10.92 of border across and 16.08 up and down on a 165 x 102 block. Across, shared out in the proportion measured (1:2). Up and down, the top is kept at the share it had on the glass alone and the bottom takes the rest:

| Border | Measured | Estimate |
|---|---|---|
| Left | 3 | 3.6 |
| Right | 6 | 7.3 |
| Top | 3 | 3.2 |
| Bottom | 12 | 12.9 |

So the picture runs from about X = 3.6 to 157.7 and Y = 3.2 to 89.1. **A front lip may overlap the display by up to 2.5 at the top and left** (leaving a millimeter of doubt), and by more at the right (up to 6) and bottom (up to 11.5) if that helps hold it.

**The bottom edge.** The lowest 2 of the front is not glass: it is the gap where the ribbons fold, with the board behind. The front lip covers it. The case must not squeeze it: the lip there bears on the glass above Y = 100 only, and the floor the display stands on is relieved under the ribbons so that the weight is taken by the board's edge.

## Raspberry Pi 5 (official drawing)

| | |
|---|---|
| Board | 85 x 56, about 1.4 thick |
| Mounting holes | 2.7 diameter, 58 x 49 apart, 3.5 from the edges |
| USB and Ethernet sockets | overhang the board's short edge by 3 |
| USB-C power socket | on a long edge, center 11.2 from the short edge at the microSD end, 3.2 tall |
| Micro HDMI sockets | same long edge, centers 25.8 and 39.2 from that end |
| Tallest parts | the USB stacks and Ethernet, about 16 above the board |

Drawing: <https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf>

## The Pi on the display

Measured: the **faces of the Pi's USB sockets are 34 from the right edge**; the Pi is **32 from the top edge**; nothing reaches more than **28 behind the board**. We want **5 clear around the Pi in every direction**.

How it sits: USB and Ethernet sockets point at the **right** edge; the long edge with USB-C and HDMI points **up**; the microSD slot, power button and status LED are at the **left** end; the 40-pin header is along the bottom.

Derived (the sockets overhang the Pi's board by 3, so its board edge is 37 from the right):

| | X from left | Y from top | Depth behind the board |
|---|---|---|---|
| Pi board | 43 to 128 | 32 to 88 | underside at about 10.6 |
| USB/Ethernet socket faces | 131 | | tops at 28 |
| USB-C power socket, center | **54.2** | 32, plug goes straight up | about 13.6 |
| HDMI sockets, centers | 68.8 and 82.2 | 32 | |
| Space the case must leave (Pi + 5) | 38 to 136 | 27 to 93 | 33 |
| Room below the Pi's board | | 14, to the board's bottom edge at 102 | |

The 28 is taken as measured from the back of the board, not from the glass: the USB stacks alone are 17.4 from the Pi's underside to their tops, so from the glass (9 further forward) there would be no room for the Pi's own standoffs. That puts the Pi's underside about 10.6 behind the board, and the whole unit **37 deep from the front of the glass, 42 with clearance**.

Nothing here conflicts with the official drawing: the Pi fits inside the outline with room on every side. (The 32 from the top is unaffected by the display being 102 tall: the extra is all at the bottom.)

## Openings

- **Power cable, top.** The cable leaves **straight up**, through an opening in the top wall directly above the socket (center X = 54.2, about 13.6 behind the board), open to the back so the cover goes on with the cable plugged in. The plug has 32 between the Pi and the top edge of the display, so its body ends inside the case and only cable passes through the wall.
- **Backlight button, top right, X = 140 to 147.5.** It is pressed downward from the top edge and sits under the case's top wall, so a paddle is cut from the wall above it, hinged at one end with a dimple over the button and 0.5 of free travel. It is printed held by a sliver at its free end, cut with a blade after printing.
- **Power button.** After "Shut down" the Pi stays off until the power is replugged; its button (at the Pi's left end, inside the cable loop) is not reachable with the cover on.

## The display cable

The flat cable leaves the Pi near the HDMI sockets, crosses the top of the Pi and loops round beyond the Pi's left end to the connector on the display board.

- It is flexible, and can be turned back **before it comes within 15 of the left edge**. So the case's left wall can follow the display's edge; the cable needs the space from X = 15 to the Pi at 43.
- It will be routed **no higher than the Pi's USB sockets**, so it adds nothing to the 28.
- Keep that space (X = 15 to 43, over the height of the Pi and 10 above it) free of posts, ribs and latches, and as deep as over the Pi. Round any inside corner near it. The board is marked "Fragile" beside its connector: nothing may press on the cable there.
- The microSD card is inside the loop and clear of it. Changing the card means opening the case.

## Also worth knowing

1. **USB and Ethernet are 34 inside the right edge**, facing it. In a closed case they are out of reach. Fine if nothing is ever plugged in (the display uses Wi-Fi); otherwise the right wall needs a slot.
2. **The corner standoffs are threaded** (the two lower ones held the kit's stands, with screws). The cover snaps on; M2.5 x 4 screws through its posts into them are an optional firmer fixing.
3. **Cooling.** With a small stick-on heatsink the Pi runs at 64 to 70 C in the open, so the case has vents over the processor and a path for air in at the bottom and out at the top.
4. **Unused connectors on the display board**: a small flat-cable socket beside the Backlight button (the touch panel's own connection, made at the factory), and, near the bottom left, a white 2-pin socket marked "GND / +5V" (5 V from the board, for a fan: on Freenove's smaller boards the same socket is marked "Only for fan"). They need no access. Check none stands higher than 4 where a wall or rim will sit. **A small 5 V fan could be run from that socket**, should the case need one.

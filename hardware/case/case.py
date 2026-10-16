"""Parametric case for a 7-inch DSI display (Freenove FNK0078) with a Raspberry Pi 5 on its back.

The measurements are in dimensions.md; the design is described in PLAN.md (part C).
Generates, next to this script, each turned the way it should sit on the print bed:

    body.stl        the front lip and walls; the display goes in from the back
    cover.stl       the back: posts onto the display's standoffs, snap tabs, vents
    wedgestand.stl  a left and a right stand, nested

Run:  pip install manifold3d numpy  &&  python case.py

It checks its own work (the parts are solid, clear of each other and of what goes inside,
go together, and stand) and refuses to write the STLs if a check fails.

COORDINATES. dimensions.md describes the display seen from the front: X from its left edge,
Y down from its top edge. The model is right-handed with z running from the front of the
case to the back, so its x runs the other way: x from the RIGHT edge seen from the front
(the left edge seen from the back), y up from the bottom, z back from the front face of
the lip. Use px(), py() and pz() to place anything given in the display's terms.
"""

import struct
from math import atan2, cos, radians, sin, sqrt, tan
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold, OpType, set_circular_segments

set_circular_segments(64)
OUT = Path(__file__).resolve().parent

# ---- the display (dimensions.md) ---------------------------------------------------------------
DISP_W = 165.0
DISP_H = 102.0  # treated as one block: glass 100, board 102, ribbons folded in between
DISP_T = 9.0  # front of the glass to the back of the board
STUD_SPACING = (154.0, 92.0)  # center to center, measured
STUD_FIRST = (5.5, 5.0)  # left standoff's center from the left edge, top one's from the top
STUD_D = 5.4
STUD_H = 4.0  # how far a standoff stands off the back of the board
# The standoffs take M2.5 screws.
# Black border from each edge to the picture, estimated (see dimensions.md).
BORDER = {"left": 3.6, "right": 7.3, "top": 3.2, "bottom": 12.9}

# ---- the Pi on its back --------------------------------------------------------------------------
PI_X = (43.0, 128.0)  # its board, from the left edge of the display
PI_Y = (32.0, 88.0)  # from the top edge
PI_SOCKET_FACE_X = 131.0  # USB and Ethernet overhang the board
PI_DEPTH = 28.0  # tallest thing behind the back of the display's board
PI_UNDERSIDE = 10.6  # the Pi's own board, behind the display's
USBC_X = 54.2  # center of the power socket
USBC_DEPTH = 13.6  # its center behind the display's board
PLUG = (13.0, 7.0)  # width and thickness of the USB-C plug's body
CABLE_X = (15.0, 43.0)  # the flat display cable loops in here...
CABLE_Y = (22.0, 93.0)
BUTTON_X = (140.0, 147.5)  # Backlight button, on the back at the top edge, pressed downward
BUTTON_DEPTH = 3.0

# ---- the case ------------------------------------------------------------------------------------
CLEARANCE = 0.5  # round the display, each side
WALL = 2.4
LIP_T = 1.2  # the front lip stands this far proud of the glass
# How far the lip reaches in over each edge of the display. Uneven on purpose: the display's
# own border is uneven, and this leaves about the same strip of black showing on every side.
LIP = {"left": 2.0, "right": 5.5, "top": 2.0, "bottom": 11.0}
LIP_EASE = 0.8  # the opening's edge is chamfered back this far, so a finger reaches the picture
CORNER_R = 5.0
SPACE_BEHIND = PI_DEPTH + 5.0  # behind the display's board: the Pi, and 5 clear over it
BACK_T = 2.0
CHAMFER = 6.0  # the back's edges fall away at 45 degrees
COVER_WALL = 2.0  # thickness of those sloping walls, measured square to them

# Ribbons fold between glass and board along the bottom edge: nothing may bear on them.
RIBBON_H = 2.0  # the lowest part of the display's face that is ribbon, not glass
LIP_RELIEF = 0.6  # the back of the bottom lip is cut back by this over the ribbons
FLOOR_RELIEF = 0.6  # and the floor under them, in front of the board
BOARD_LAND = (
    2.5  # the last of the display's depth, where the board's edge is: it carries the weight
)

# The bottom front edge is bevelled: this deep, and at this angle to the bottom.
BEVEL_DEPTH = 3.0
BEVEL_DEG = 20.0

# Power opening, through the top wall. Big enough to pass the plug.
POWER_W = 18.0
POWER_D = 11.0

# Backlight paddle: a strip of the top wall, freed on three sides, that bends in to the button.
PADDLE_X = (126.0, 150.0)  # hinged at the first, free at the second
PADDLE_DEPTH = (-0.3, 3.7)  # behind the display's board: over the button
PADDLE_SLIT = 0.8

# Posts on the cover that stand on the display's standoffs.
POST_R = 5.2
CUP_D = 7.4  # a millimeter clear of the standoff all round: a stop, not a locator
CUP_DEPTH = 1.5
PLAY = 0.2  # front to back; a foam dot in each cup takes it up
# Optional M2.5 x 4 screws into the standoffs. The head drops down a bore in the post to a
# thin floor on the standoff's tip, so a short screw still reaches the thread.
SCREW_HOLE_D = 3.4  # some room, as STUD_FIRST is still an estimate
SCREW_HEAD_D = 6.2  # a pan, button or socket head, and the screwdriver after it
SCREW_FLOOR = 1.2  # under the head: leaves 2.8 of a 4 mm screw in the standoff

# The rim on the cover that sits just inside the body's walls and locates it.
RIM_T = 1.6
RIM_GAP = 0.25
RIM_REACH = 2.5

# Snap tabs: two on the top edge, two on the bottom. Given as display X, center of each.
TABS_TOP_X = (76.0, 110.0)
TABS_BOTTOM_X = (45.0, 120.0)
TAB_W = 12.0
TAB_T = 1.6
TAB_LEN = 18.0  # long, so that it bends little: it is printed standing up, across the layers
TAB_CATCH = 1.2  # how far the catch reaches into its window
TAB_GAP = 0.3
WINDOW = (11.0, 6.0)  # the window in the wall, as distances forward of the seam
WINDOW_W = TAB_W + 1.0

# Vents.
SLOT_W = 2.5
SLOT_PITCH = 5.0
WALL_SLOT_Z = (17.0, 36.5)  # slots in the body's top and bottom walls, from the front of the case
FIELD_X = (55.0, 100.0)  # the field in the back, over the processor
FIELD_Y = (40.0, 80.0)
VENT_TARGET_MM2 = 1200.0  # open area wanted at the bottom, and again at the top

# The stands, left and right mirror images. A solid knob with two ears on each side wall of the
# body; a stand slips over the ears pointing straight forward (its keyway lines up with them
# only then) and swings down and back. Its arm runs down to the table, a foot runs forward
# along it, and a cradle holds the case up: a wedge under the front edge with a lip in front
# of the bezel, a floor under the bottom, and a hook round the bottom back edge, over the
# cover's bevel. At the end of the swing the hook has to pass that corner: the stand flexes
# and the hook snaps over the bevel just as the stand meets its stop peg. Then the peg and
# the lip stop it going further back, the hook stops it swinging out, and the ears keep it on.
KNOB_Z = 15.0  # from the front of the case
KNOB_DROP = 20.0  # below the top of the case
SHANK_R = 6.0
EAR_REACH = 8.5
EAR_W = 6.0
EAR_T = 2.5
FIT = 0.2
STOP_R = 2.5  # the peg the stand comes to rest against ...
# ... centered here, (y, z): where the stand's arm meets it at the end of the swing, solved
# once from the stand's geometry so that peg and hook engage together.
STOP_PEG = (90.35860575115771, 31.611849283271948)
HUB_R = 11.0
STAND_T = 5.0  # thickness of the stand's plate
ARM_W = 12.0
WEDGE_TILT = 30.0  # degrees the case leans back on it
WEDGE_ARM = 20.0  # the arm runs from the knob down to the table this far behind vertical
WEDGE_IN = 25.0  # how far the wedge reaches in under the case, from the outside of its wall
WEDGE_TOE = 5.0  # how far the wedge stands out in front of the case
WEDGE_CLEAR = 0.3  # between the case and the cradle
WEDGE_LIP = 5.0  # the wedge rises this far in front of the bezel, above its bottom edge
WEDGE_HOOK = 6.0  # how far the hook reaches up the cover's bevel at the bottom back edge
WEDGE_HOOK_T = 2.5  # thickness of the hook, square to the bevel
SNAP_MAX = 3.0  # the most the cradle may have to give, at the end of the swing, to snap on
LIFT_CLEAR = 5.0  # the bottom back edge of the case above the table: the cradle's floor there
FOOT_H = 6.0  # height of the foot along the table
ELBOW_R = 20.0  # radius of the arc that fills the inside of the arm's elbow


# Rough weights, for the check that it stands up. Grams.
MASS_DISPLAY = 240.0
MASS_PI = 60.0
PLASTIC_G_PER_MM3 = 1.27e-3 * 0.55  # PLA or PETG, with walls and sparse infill

# ---- derived -------------------------------------------------------------------------------------
IN_W = DISP_W + 2 * CLEARANCE
IN_H = DISP_H + 2 * CLEARANCE
OUT_W = IN_W + 2 * WALL
OUT_H = IN_H + 2 * WALL
Z_GLASS = LIP_T
Z_BOARD = LIP_T + DISP_T  # back of the display's board
Z_INSIDE = Z_BOARD + SPACE_BEHIND  # inside face of the back
Z_BACK = Z_INSIDE + BACK_T  # outside face of the back: the overall depth
SEAM = Z_BACK - CHAMFER  # where the body ends and the cover begins
KNOB_Y = OUT_H - KNOB_DROP
TOP_IN = OUT_H - WALL  # inside face of the top wall


def px(display_x: float) -> float:
    """Model x of a point given from the left edge of the display, seen from the front."""
    return WALL + CLEARANCE + (DISP_W - display_x)


def py(display_y: float) -> float:
    """Model y of a point given down from the top edge of the display."""
    return WALL + CLEARANCE + (DISP_H - display_y)


def pz(behind_board: float) -> float:
    """Model z of a depth given behind the back of the display's board."""
    return Z_BOARD + behind_board


def x_span(display_x0: float, display_x1: float) -> tuple[float, float]:
    """Model x range for a range given in display X (the direction flips)."""
    return px(display_x1), px(display_x0)


def y_span(display_y0: float, display_y1: float) -> tuple[float, float]:
    return py(display_y1), py(display_y0)


# ---- shapes --------------------------------------------------------------------------------------


def box(x0, x1, y0, y1, z0, z1) -> Manifold:
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def rounded_rect(w: float, h: float, r: float) -> CrossSection:
    r = min(r, w / 2 - 0.01, h / 2 - 0.01)
    if r <= 0.01:
        return CrossSection.square([w, h])
    return CrossSection.square([w - 2 * r, h - 2 * r]).translate([r, r]).offset(r, JoinType.Round)


def plate(inset: float, z0: float, z1: float, radius: float | None = None) -> Manifold:
    """The case's footprint shrunk by inset, as a slab from z0 to z1."""
    r = max(CORNER_R - inset, 0.0) if radius is None else radius
    shape = rounded_rect(OUT_W - 2 * inset, OUT_H - 2 * inset, r)
    return Manifold.extrude(shape, z1 - z0).translate([inset, inset, z0])


def cyl_z(x: float, y: float, z0: float, z1: float, r0: float, r1: float | None = None) -> Manifold:
    """A cylinder (or cone, radius r0 at z0 and r1 at z1) along z."""
    return Manifold.cylinder(z1 - z0, r0, r0 if r1 is None else r1).translate([x, y, z0])


# Local (u, v, w) -> world (x = w, y = u, z = v): a shape drawn in the y-z plane, extruded
# along x; and a cylinder's axis turned from z to x.
_ALONG_X = np.array([[0.0, 0.0, 1.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])


def _ccw(points):
    area = sum(
        a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1], strict=True)
    )
    return points if area > 0 else points[::-1]


def prism_x(points_yz, x0: float, x1: float) -> Manifold:
    """A polygon in the y-z plane, extruded from x0 to x1."""
    shape = Manifold.extrude(CrossSection([_ccw(points_yz)]), x1 - x0)
    return shape.transform(_ALONG_X).translate([x0, 0, 0])


def cyl_x(x0: float, x1: float, y: float, z: float, r: float) -> Manifold:
    lo, hi = min(x0, x1), max(x0, x1)
    return Manifold.cylinder(hi - lo, r, r).transform(_ALONG_X).translate([lo, y, z])


def slot_y(x0, x1, z0, z1, y0, y1, r: float | None = None) -> Manifold:
    """A hole with rounded ends, running through in the y direction."""
    r = min(x1 - x0, z1 - z0) / 2 if r is None else r
    shape = rounded_rect(x1 - x0, z1 - z0, r).translate([x0, -z1])
    # Drawn in x and -z and stood up about the x axis, which takes -z to z and the
    # extrusion to y, without turning the solid inside out.
    return Manifold.extrude(shape, y1 - y0).rotate([-90, 0, 0]).translate([0, y0, 0])


def slot_z(x0, x1, y0, y1, z0, z1) -> Manifold:
    """A hole with rounded ends, running through in the z direction."""
    shape = rounded_rect(x1 - x0, y1 - y0, min(x1 - x0, y1 - y0) / 2).translate([x0, y0])
    return Manifold.extrude(shape, z1 - z0).translate([0, 0, z0])


def mirrored(part: Manifold) -> Manifold:
    """The same thing on the other side of the case."""
    return part.mirror([1, 0, 0]).translate([OUT_W, 0, 0])


def both_sides(part: Manifold) -> Manifold:
    return part + mirrored(part)


def stud_centers() -> list[tuple[float, float]]:
    """The display's four standoffs, in model x and y."""
    return [
        (px(STUD_FIRST[0] + dx), py(STUD_FIRST[1] + dy))
        for dx in (0.0, STUD_SPACING[0])
        for dy in (0.0, STUD_SPACING[1])
    ]


def tab_places() -> list[tuple[float, bool]]:
    """Model x of each snap tab's center, and whether it is on the top edge."""
    return [(px(x), True) for x in TABS_TOP_X] + [(px(x), False) for x in TABS_BOTTOM_X]


def _slot_columns(x0: float, x1: float, keep_out: list[tuple[float, float]]) -> list[float]:
    """Left edges of a row of vent slots between x0 and x1, missing the keep-out ranges."""
    count = int((x1 - x0 + SLOT_PITCH - SLOT_W) // SLOT_PITCH)
    start = x0 + ((x1 - x0) - ((count - 1) * SLOT_PITCH + SLOT_W)) / 2
    found = []
    for i in range(count):
        left = start + i * SLOT_PITCH
        if all(left + SLOT_W < lo or left > hi for lo, hi in keep_out):
            found.append(left)
    return found


# ---- the body ------------------------------------------------------------------------------------


def lip_opening() -> tuple[float, float, float, float]:
    """The hole in the front lip: model x0, x1, y0, y1."""
    x0, x1 = x_span(LIP["left"], DISP_W - LIP["right"])
    y0, y1 = y_span(LIP["top"], DISP_H - LIP["bottom"])
    return x0, x1, y0, y1


def power_hole() -> tuple[float, float, float, float]:
    """The power opening in the top wall: model x0, x1, z0, z1."""
    x0, x1 = x_span(USBC_X - POWER_W / 2, USBC_X + POWER_W / 2)
    center = pz(USBC_DEPTH)
    return x0, x1, center - POWER_D / 2, center + POWER_D / 2


def paddle_box() -> tuple[float, float, float, float]:
    """The Backlight paddle: model x0 (its free end), x1 (its hinge), z0, z1."""
    x0, x1 = x_span(*PADDLE_X)
    return x0, x1, pz(PADDLE_DEPTH[0]), pz(PADDLE_DEPTH[1])


def knob() -> Manifold:
    """The knob for a stand, on the side wall at x = 0. Negative x is outward."""
    tip = -(FIT + STAND_T + 0.3)
    shank = cyl_x(tip - EAR_T, 0.5, KNOB_Y, KNOB_Z, SHANK_R)
    ears = box(
        tip - EAR_T,
        tip,
        KNOB_Y - EAR_W / 2,
        KNOB_Y + EAR_W / 2,
        KNOB_Z - EAR_REACH,
        KNOB_Z + EAR_REACH,
    )
    return shank + ears


def stop_peg() -> Manifold:
    """The peg a stand comes to rest against, on the side wall at x = 0."""
    y, z = STOP_PEG
    return cyl_x(-(FIT + STAND_T), 0.5, y, z, STOP_R)


def wall_vents(top: bool) -> list[Manifold]:
    """Slots through the body's top or bottom wall, clear of the other openings in it."""
    keep = [
        (x - WINDOW_W / 2 - 2, x + WINDOW_W / 2 + 2) for x, is_top in tab_places() if is_top == top
    ]
    if top:
        hx0, hx1, _, _ = power_hole()
        keep.append((hx0 - 3, hx1 + 3))
    y0, y1 = (TOP_IN - 1, OUT_H + 1) if top else (-1, WALL + 1)
    return [
        slot_y(left, left + SLOT_W, WALL_SLOT_Z[0], WALL_SLOT_Z[1], y0, y1)
        for left in _slot_columns(14.0, OUT_W - 14.0, keep)
    ]


def make_body() -> Manifold:
    """The front lip and the four walls, with the knobs and stop pegs for the stands."""
    back = SEAM
    body = plate(0, 0, back)
    body -= box(WALL, OUT_W - WALL, WALL, OUT_H - WALL, LIP_T, back + 1)

    # The opening in the lip, its edge eased back toward the front.
    x0, x1, y0, y1 = lip_opening()
    e = LIP_EASE
    land = LIP_T - 0.4
    mouth = Manifold.batch_hull(
        [box(x0, x1, y0, y1, land, land + 0.01), box(x0 - e, x1 + e, y0 - e, y1 + e, -0.01, 0)]
    )
    body -= box(x0, x1, y0, y1, land - 0.01, LIP_T + 1) + mouth

    # Nothing bears on the ribbons: the lip is cut back behind them, and the floor under
    # them, leaving a land under the board's edge.
    ribbons_top = WALL + CLEARANCE + RIBBON_H + 0.4
    body -= box(WALL, OUT_W - WALL, WALL, ribbons_top, LIP_T - LIP_RELIEF, LIP_T + 0.01)
    body -= box(
        WALL + 6,
        OUT_W - WALL - 6,
        WALL - FLOOR_RELIEF,
        WALL + 0.01,
        LIP_T - 0.01,
        Z_BOARD - BOARD_LAND,
    )

    # A flat along the bottom front edge to stand on when it leans back.
    rise = BEVEL_DEPTH * tan(radians(BEVEL_DEG))
    wedge = [(-1, -1), (-1, BEVEL_DEPTH), (0, BEVEL_DEPTH), (rise, 0), (rise, -1)]
    body -= prism_x(wedge, -1, OUT_W + 1)

    # Power, straight up out of the top.
    hx0, hx1, hz0, hz1 = power_hole()
    body -= slot_y(hx0, hx1, hz0, hz1, TOP_IN - 1, OUT_H + 1, r=2.5)

    # The Backlight paddle: three slits free it, a sliver at the free end holds it for
    # printing (cut it with a blade), and a dimple marks where to press.
    fx, hinge, z0, z1 = paddle_box()
    s = PADDLE_SLIT
    y0, y1 = TOP_IN - 1, OUT_H + 1
    body -= box(fx, hinge, y0, y1, z0 - s, z0)  # the slit in front of it
    body -= box(fx - s, hinge, y0, y1, z1, z1 + s)  # the slit behind it
    body -= box(fx - s, fx, y0, y1, z0 + 0.6, z1 + s)  # round its free end...
    body -= box(fx - s, fx, y0, OUT_H - 0.8, z0, z0 + 0.6)  # ...all but a sliver at the outside
    button = px(sum(BUTTON_X) / 2)
    body -= Manifold.sphere(4.0).translate([button, OUT_H + 3.5, pz(BUTTON_DEPTH / 2)])

    # Vents, and the windows the cover's tabs snap into.
    for is_top in (True, False):
        for slot in wall_vents(is_top):
            body -= slot
    for x, is_top in tab_places():
        ya, yb = (TOP_IN - 1, OUT_H + 1) if is_top else (-1, WALL + 1)
        half = WINDOW_W / 2
        body -= box(x - half, x + half, ya, yb, SEAM - WINDOW[0], SEAM - WINDOW[1])
    return body + both_sides(knob() + stop_peg())


# ---- the cover -----------------------------------------------------------------------------------

_SLOPE = COVER_WALL * sqrt(2.0)  # the sloping wall's thickness, measured across the case


def cover_outside() -> Manifold:
    return Manifold.batch_hull([plate(0, SEAM, SEAM + 0.01), plate(CHAMFER, Z_BACK - 0.01, Z_BACK)])


def cover_inside() -> Manifold:
    """The hollow of the dish."""
    return Manifold.batch_hull(
        [
            plate(_SLOPE, SEAM - 0.02, SEAM - 0.01, radius=0),
            plate(_SLOPE + Z_INSIDE - SEAM, Z_INSIDE - 0.01, Z_INSIDE, radius=0),
        ]
    )


def tab(x: float, top: bool) -> Manifold:
    """A snap tab: a finger reaching forward along the inside of the wall, with a catch that
    springs into the window in the wall. Built for the top edge and turned over for the
    bottom."""
    face = TOP_IN - TAB_GAP  # the finger's outer face
    finger = box(x - TAB_W / 2, x + TAB_W / 2, face - TAB_T, face, SEAM - TAB_LEN, SEAM + 1.5)
    hold = SEAM - WINDOW[1] - 0.15  # the catch's square face, just forward of the window's edge
    reach = TAB_GAP + TAB_CATCH
    catch = prism_x(
        [
            (face - 0.3, hold - 5.0),  # starts inside the finger, so the two are one solid
            (face, hold - 5.0),
            (face + reach, hold - 0.9),
            (face + reach, hold),
            (face - 0.3, hold),
        ],
        x - TAB_W / 2,
        x + TAB_W / 2,
    )
    part = finger + catch
    return part if top else part.mirror([0, 1, 0]).translate([0, OUT_H, 0])


def cover_vents() -> dict[str, list[Manifold]]:
    """Slots in the top and bottom chamfers, and the field over the processor."""
    cuts: dict[str, list[Manifold]] = {"top": [], "bottom": [], "back": []}
    posts_x = [(x - POST_R - 1, x + POST_R + 1) for x, _ in stud_centers()]
    for left in _slot_columns(16.0, OUT_W - 16.0, posts_x):
        cuts["bottom"].append(
            slot_z(left, left + SLOT_W, 0.8, CHAMFER + 0.8, SEAM + 1.8, Z_BACK + 1)
        )
        cuts["top"].append(
            slot_z(left, left + SLOT_W, OUT_H - CHAMFER - 0.8, OUT_H - 0.8, SEAM + 1.8, Z_BACK + 1)
        )
    fx0, fx1 = x_span(*FIELD_X)
    fy0, fy1 = y_span(*FIELD_Y)
    for left in _slot_columns(fx0, fx1, []):
        cuts["back"].append(slot_z(left, left + SLOT_W, fy0, fy1, Z_INSIDE - 1, Z_BACK + 1))
    return cuts


def make_cover() -> Manifold:
    cover = cover_outside() - cover_inside()

    # The rim that sits just inside the body's walls.
    inset = WALL + RIM_GAP
    rim = plate(inset, SEAM - RIM_REACH, SEAM + 1.5, radius=0)
    rim -= plate(inset + RIM_T, SEAM - RIM_REACH - 1, SEAM + 2.5, radius=0)
    for x, _ in tab_places():  # the tabs spring; the rim must not stiffen them
        half = TAB_W / 2 + 1.2
        rim -= box(x - half, x + half, -1, OUT_H + 1, SEAM - RIM_REACH - 1, SEAM + 0.2)
    cover += rim
    for x, is_top in tab_places():
        cover += tab(x, is_top)

    # Posts onto the display's standoffs.
    tip = pz(STUD_H)  # where a standoff ends
    mouth = tip + PLAY - CUP_DEPTH
    for x, y in stud_centers():
        cover += cyl_z(x, y, mouth, Z_INSIDE + 0.5, POST_R)
    for x, y in stud_centers():
        cover -= cyl_z(x, y, mouth - 0.01, tip + PLAY, CUP_D / 2 + 0.7, CUP_D / 2)
        cover -= cyl_z(x, y, mouth - 1, Z_BACK + 1, SCREW_HOLE_D / 2)
        cover -= cyl_z(x, y, tip + PLAY + SCREW_FLOOR, Z_BACK + 1, SCREW_HEAD_D / 2)

    for group in cover_vents().values():
        for cut in group:
            cover -= cut
    return cover


# ---- the stand ----------------------------------------------------------------------------------


def placed_stand(stand: Manifold, angle_deg: float) -> Manifold:
    """A stand on the knob at x = 0, its arm swung back by angle_deg from pointing straight
    down. At -90 it points straight forward, which is how it goes on and comes off."""
    return stand.rotate([-angle_deg, 0, 0]).translate([-FIT, KNOB_Y, KNOB_Z])


#
# Worked out in three 2D frames, all in the plane of the side wall:
#   case   (y, z) as everywhere else in this file;
#   world  (up, back): standing on the table, up from it, back from the front of the case;
#   stand  the stand's own frame: its hub at the origin, its arm along -y.


def wedge_lift() -> float:
    """Height of the case's front bottom edge above the table, standing on the wedge stand:
    enough that the back of its bottom clears the table."""
    return LIFT_CLEAR + SEAM * sin(radians(WEDGE_TILT))


def case_to_world(y: float, z: float) -> tuple[float, float]:
    t = radians(WEDGE_TILT)
    return wedge_lift() + y * cos(t) - z * sin(t), y * sin(t) + z * cos(t)


def world_to_case(up: float, back: float) -> tuple[float, float]:
    t, u = radians(WEDGE_TILT), up - wedge_lift()
    return u * cos(t) + back * sin(t), -u * sin(t) + back * cos(t)


def wedge_angle() -> float:
    """The stand's angle on the case (as placed_stand takes it) when its arm is WEDGE_ARM
    behind vertical."""
    return WEDGE_TILT + WEDGE_ARM


def case_to_stand(y: float, z: float, angle: float) -> tuple[float, float]:
    """A point of the case, in the frame of a stand at angle on the knob."""
    a = radians(angle)
    y, z = y - KNOB_Y, z - KNOB_Z
    return y * cos(a) - z * sin(a), y * sin(a) + z * cos(a)


def _section(points) -> CrossSection:
    return CrossSection([_ccw([tuple(map(float, p)) for p in points])])


def _world_section(points) -> CrossSection:
    """A polygon given in world terms, in the stand's frame at its standing angle."""
    a = wedge_angle()
    return _section([case_to_stand(*world_to_case(u, b), a) for u, b in points])


def case_outline() -> list[tuple[float, float]]:
    """The case's side outline, (y, z): the bevel at the front bottom, the cover's chamfers."""
    rise = BEVEL_DEPTH * tan(radians(BEVEL_DEG))
    return [
        (0.0, BEVEL_DEPTH),
        (rise, 0.0),
        (OUT_H, 0.0),
        (OUT_H, SEAM),
        (OUT_H - CHAMFER, Z_BACK),
        (CHAMFER, Z_BACK),
        (0.0, SEAM),
    ]


def _case_in_stand(angle: float) -> CrossSection:
    """The case's side outline, in the frame of a stand at angle on the knob."""
    return _section([case_to_stand(y, z, angle) for y, z in case_outline()])


def _hook_blank() -> CrossSection:
    """Everything round the bottom back edge that the hook may fill, in the case frame: up
    the bevel by WEDGE_HOOK, WEDGE_HOOK_T thick, and down to the cradle under the bottom."""
    k = sqrt(0.5)
    up = (WEDGE_HOOK * k, SEAM + WEDGE_HOOK * k)  # how far up the bevel it reaches
    out = WEDGE_CLEAR + WEDGE_HOOK_T
    return _section(
        [
            (up[0] - WEDGE_CLEAR * k, up[1] + WEDGE_CLEAR * k),
            (up[0] - out * k, up[1] + out * k),
            (-30.0, SEAM + out * sqrt(2) + 30.0),
            (-30.0, SEAM - 10.0),
            (0.0, SEAM - 10.0),
        ]
    )


def wedge_profile() -> CrossSection:
    """The cradle's side profile, in the stand's frame. In front of the bottom back edge it is
    what the case does not pass through on the way in, so that only the hook has to snap: a
    wedge under the front edge with a lip in front of the bezel, then a floor under the
    bottom. Round the bottom back edge it is the hook, less the case itself."""
    final = wedge_angle()
    top = wedge_lift() + WEDGE_LIP
    back = case_to_world(0.0, SEAM)[1]
    blank = _world_section([(0, -WEDGE_TOE), (0, back), (top, back), (top, -WEDGE_TOE)])
    swept = CrossSection.batch_boolean(
        [_case_in_stand(a) for a in np.arange(-90.0, final + 0.01, 0.5)], OpType.Add
    ).offset(WEDGE_CLEAR, JoinType.Round)
    hook = _section([case_to_stand(y, z, final) for y, z in _hook_blank().to_polygons()[0]])
    case = _case_in_stand(final).offset(WEDGE_CLEAR, JoinType.Round)
    table = _world_section([(0, -500), (0, 500), (500, 500), (500, -500)])
    cradle = ((blank - swept) + (hook - case)) ^ table
    return cradle.offset(-0.6, JoinType.Round).offset(0.6, JoinType.Round) - case


def snap_depth() -> float:
    """How far into the case the cradle reaches at worst on its way in: how far the stand
    has to give at the end of the swing to snap on."""
    cradle = wedge_profile()
    worst = 0.0
    for angle in np.arange(wedge_angle() - 30.0, wedge_angle(), 0.5):
        case = _case_in_stand(float(angle))
        if (cradle ^ case).area() < 0.01:
            continue
        lo, hi = 0.0, 15.0
        for _ in range(16):
            mid = (lo + hi) / 2
            if (cradle ^ case.offset(-mid, JoinType.Miter)).area() > 0.01:
                lo = mid
            else:
                hi = mid
        worst = max(worst, lo)
    return worst


def stand_plate_profile() -> CrossSection:
    """The flat part: hub, a lug against the stop peg, the arm, and the foot."""
    a = radians(WEDGE_ARM)
    knob_up, knob_back = case_to_world(KNOB_Y, KNOB_Z)
    reach = knob_up / cos(a) + ARM_W  # well past the table; trimmed to it below
    down = (-cos(a), sin(a))
    side = (sin(a) * ARM_W / 2, cos(a) * ARM_W / 2)
    end = (knob_up + down[0] * reach, knob_back + down[1] * reach)
    arm = _world_section(
        [
            (knob_up + side[0], knob_back + side[1]),
            (end[0] + side[0], end[1] + side[1]),
            (end[0] - side[0], end[1] - side[1]),
            (knob_up - side[0], knob_back - side[1]),
        ]
    )
    heel = knob_back + knob_up * tan(a) + ARM_W / 2 / cos(a)
    foot = _world_section([(0, -WEDGE_TOE), (0, heel), (FOOT_H, heel), (FOOT_H, -WEDGE_TOE)])
    table = _world_section([(0, -500), (0, 500), (500, 500), (500, -500)])
    # The elbow, where the arm comes down to the foot, filled in with an arc on its inside for
    # stiffness (rounding out, then in again, fills a corner with a curve of that radius).
    plate = ((arm + foot) ^ table).offset(ELBOW_R, JoinType.Round)
    plate = plate.offset(-ELBOW_R, JoinType.Round)
    plate = (plate ^ table) + CrossSection.circle(HUB_R)

    # The lug: from the arm round to the peg, which it comes to rest against.
    peg = stop_peg_center()
    py_, pz_ = case_to_stand(*peg, wedge_angle())
    r = sqrt(py_**2 + pz_**2) + STOP_R
    a0, a1 = atan2(0.0, -1.0), atan2(pz_, py_)
    if a1 - a0 > np.pi:
        a1 -= 2 * np.pi
    if a0 - a1 > np.pi:
        a1 += 2 * np.pi
    arc = [(r * cos(x), r * sin(x)) for x in np.linspace(a0, a1, 24)]
    lug = _section([(0.0, 0.0), *arc])
    plate = plate + lug - CrossSection.circle(STOP_R + 0.15).translate([py_, pz_])
    # Drop the slivers the peg leaves at the end of the lug.
    return plate.offset(-0.8, JoinType.Round).offset(0.8, JoinType.Round)


def stop_peg_center() -> tuple[float, float]:
    """The stop peg's center, (y, z) on the case."""
    return STOP_PEG


def _along_x(shape: CrossSection, x0: float, x1: float) -> Manifold:
    """A profile in the stand's (y, z), extruded from x0 to x1."""
    return Manifold.extrude(shape, x1 - x0).transform(_ALONG_X).translate([x0, 0, 0])


def make_wedge_stand() -> Manifold:
    """The left stand, in its own frame: the face against the case at x = 0,
    the wedge reaching in under the case toward +x."""
    part = _along_x(stand_plate_profile(), -STAND_T, 0)
    part += _along_x(wedge_profile(), -STAND_T, FIT + WEDGE_IN)
    # The cover's posts stand a fraction proud of its bevel near the bottom corners: the hook
    # is notched round any it reaches, seen from the stand in place.
    angle = wedge_angle()
    for x, y in stud_centers():
        if x < WEDGE_IN + POST_R and y < OUT_H / 2:
            post = cyl_z(x, y, SEAM - 5, Z_BACK + 5, POST_R + 0.5)
            part -= post.translate([FIT, -KNOB_Y, -KNOB_Z]).rotate([angle, 0, 0])
    return _on_knob(part)


def _on_knob(part: Manifold) -> Manifold:
    """The hole for the knob, and the keyway: it meets the knob's ears only
    with the stand pointing straight forward, which is how it goes on."""
    part -= cyl_x(-STAND_T - 1, 1, 0, 0, SHANK_R + FIT)
    reach, half = EAR_REACH + 0.4, EAR_W / 2 + 0.3
    return part - box(-STAND_T - 1, 1, -reach, reach, -half, half)


def to_world(part: Manifold) -> Manifold:
    """Something placed on the case, as it stands on the wedge stands: y up from the table,
    z back from the front of the case."""
    up0, back0 = case_to_world(0.0, 0.0)
    return part.rotate([WEDGE_TILT, 0, 0]).translate([0, up0, back0])


def check_wedge_stand(stand: Manifold, body: Manifold, cover: Manifold) -> list[str]:
    wrong = []
    if stand.status().name != "NoError" or len(stand.decompose()) != 1:
        wrong.append("the wedge stand is not one valid solid")
        return wrong
    final = wedge_angle()
    case = body + cover
    # It goes on pointing straight forward and swings back. Only the cradle may meet the case,
    # and only at the end of the swing, where it snaps on.
    plate_only = _on_knob(_along_x(stand_plate_profile(), -STAND_T, 0))
    for angle in list(np.arange(-90.0, final, 2.0)) + [final]:
        if _overlap(placed_stand(plate_only, float(angle)), case) > 0.05:
            wrong.append(f"the stand's arm or foot meets the case at {angle:.0f} degrees")
            break
        if angle < final - 30 and _overlap(placed_stand(stand, float(angle)), case) > 0.05:
            wrong.append(f"the cradle meets the case at {angle:.0f} degrees, before the snap")
            break
    if _overlap(placed_stand(stand, final), case) > 0.05:
        wrong.append("in place, the cradle presses on the case")
    depth = snap_depth()
    if depth > SNAP_MAX:
        wrong.append(f"snapping on, the stand has to give {depth:.1f} mm")
    # On, it is held both ways: the peg stops it going further back, the hook stops it
    # swinging out; and turned at all, the knob's ears keep it on.
    if _overlap(placed_stand(stand, final + 1.5), body) < 0.5:
        wrong.append("the wedge stand does not come to rest against its stop peg")
    if _overlap(placed_stand(stand, final - 1.5), case) < 0.5:
        wrong.append("the hook does not hold the stand in place")
    if _overlap(placed_stand(stand, final).translate([-(EAR_T + 1), 0, 0]), knob()) < 1:
        wrong.append("in place, the stand could slide off its knob")
    # Standing: nothing but the stands touches the table.
    left = placed_stand(stand, final)
    case_low = to_world(case).bounding_box()[1]
    stands_low = to_world(left).bounding_box()[1]
    if abs(stands_low) > 0.05:
        wrong.append(f"the stand's foot is {stands_low:.2f} off the table")
    if case_low < LIFT_CLEAR - 0.1:
        wrong.append(f"the case comes within {case_low:.2f} of the table")
    return wrong


def stability(stand: Manifold, body: Manifold, cover: Manifold) -> dict[str, float]:
    """Where the weight is between the front and back of what stands on the table, and how
    hard a push square on to the top of the screen tips it backward."""
    left = placed_stand(stand, wedge_angle())
    masses = [
        (MASS_DISPLAY, (OUT_H / 2, (Z_GLASS + Z_BOARD) / 2)),
        (MASS_PI, (py(sum(PI_Y) / 2), pz(PI_DEPTH / 2 + 5))),
        (body.volume() * PLASTIC_G_PER_MM3, (OUT_H / 2, SEAM / 2)),
        (cover.volume() * PLASTIC_G_PER_MM3, (OUT_H / 2, Z_BACK - 4)),
    ]
    grams = sum(m for m, _ in masses) + 2 * stand.volume() * PLASTIC_G_PER_MM3
    case_g = sum(m for m, _ in masses)
    back = sum(m * case_to_world(*p)[1] for m, p in masses) / case_g
    verts = np.asarray(to_world(left).to_mesh().vert_properties)[:, :3]
    on_table = verts[verts[:, 1] < 0.3]
    front, rear = float(on_table[:, 2].min()), float(on_table[:, 2].max())
    # A push at the top of the screen, square on to it; moments about the back of the feet.
    t = radians(WEDGE_TILT)
    top_up, top_back = case_to_world(OUT_H, 0.0)
    push = (-sin(t), cos(t))  # (up, back)
    arm = (top_back - rear) * push[0] - (top_up - 0) * push[1]
    return {
        "grams": grams,
        "weight_back": back,
        "front": front,
        "rear": rear,
        "tip_newtons": grams / 1000 * 9.81 * (rear - back) / abs(arm),
    }


# ---- stand-ins for what goes inside, for the checks ----------------------------------------------


def stand_ins() -> dict[str, Manifold]:
    dx0, dx1 = px(DISP_W), px(0)
    dy0, dy1 = py(DISP_H), py(0)
    studs = Manifold()
    for x, y in stud_centers():
        studs += cyl_z(x, y, Z_BOARD, pz(STUD_H), STUD_D / 2)
    pi_x = x_span(PI_X[0], PI_SOCKET_FACE_X)
    pi_y = y_span(*PI_Y)
    plug_x = x_span(USBC_X - PLUG[0] / 2, USBC_X + PLUG[0] / 2)
    plug_z = pz(USBC_DEPTH)
    cable_x, cable_y = x_span(*CABLE_X), y_span(*CABLE_Y)
    bx = x_span(*BUTTON_X)
    return {
        "display": box(dx0, dx1, dy0, dy1, Z_GLASS, Z_BOARD),
        "standoffs": studs,
        "Pi": box(pi_x[0], pi_x[1], pi_y[0], pi_y[1], pz(PI_UNDERSIDE), pz(PI_DEPTH)),
        "power plug": box(
            plug_x[0],
            plug_x[1],
            py(PI_Y[0]),
            OUT_H + 20,
            plug_z - PLUG[1] / 2,
            plug_z + PLUG[1] / 2,
        ),
        "display cable": box(cable_x[0], cable_x[1], cable_y[0], cable_y[1], Z_BOARD, pz(PI_DEPTH)),
        "Backlight button": box(bx[0], bx[1], dy1 - 4, dy1, Z_BOARD, pz(BUTTON_DEPTH)),
    }


def _overlap(a: Manifold, b: Manifold) -> float:
    return (a ^ b).volume()


def check(body: Manifold, cover: Manifold) -> list[str]:
    """Everything that can be checked without printing. Returns what is wrong."""
    wrong = []
    ins = stand_ins()
    parts = {"body": body, "cover": cover}
    for name, part in parts.items():
        if part.status().name != "NoError" or part.volume() <= 0:
            wrong.append(f"{name} is not a valid solid")
        elif len(part.decompose()) != 1:
            wrong.append(f"{name} is in {len(part.decompose())} pieces")

    if _overlap(body, cover) > 0.5:
        wrong.append(f"body and cover overlap by {_overlap(body, cover):.1f} mm3")
    for name, thing in ins.items():
        for part_name in ("body", "cover"):
            v = _overlap(parts[part_name], thing)
            if v > 0.05:
                wrong.append(f"the {part_name} overlaps the {name} by {v:.2f} mm3")

    # The display goes in from behind: everything it sweeps through must be empty.
    b = ins["display"].bounding_box()
    path = box(b[0], b[3], b[1], b[4], Z_GLASS, Z_BACK + 30)
    if _overlap(body, path) > 0.05:
        wrong.append(
            f"the display cannot go in: the body is in its way ({_overlap(body, path):.2f} mm3)"
        )

    # The cover goes straight on. Its catches must give way against the walls (that is how
    # they work); nothing else may touch the body, or what is inside, on the way.
    catches = Manifold()
    for x, is_top in tab_places():
        ya, yb = (TOP_IN - TAB_GAP, OUT_H) if is_top else (0, WALL + TAB_GAP)
        catches += box(x - TAB_W / 2 - 0.5, x + TAB_W / 2 + 0.5, ya, yb, -5, Z_BACK + 40)
    inside = Manifold()
    for thing in ins.values():
        inside += thing
    for step in range(1, 31):
        moved = cover.translate([0, 0, float(step)])
        if _overlap(moved - catches, body) > 0.05 or _overlap(moved, inside) > 0.05:
            wrong.append(f"the cover fouls something {step} mm before it is home")
            break

    # The lip must not reach the picture, wherever the display sits in its clearance.
    for side in LIP:
        margin = BORDER[side] - LIP[side] - CLEARANCE
        if margin < 0.5:
            wrong.append(f"the {side} lip comes within {margin:.2f} of the picture")

    return wrong


def vent_areas() -> dict[str, float]:
    """Open area in square millimeters: at the bottom, at the top, and in the back."""

    def removed(solid: Manifold, cuts: list[Manifold], thickness: float) -> float:
        total = Manifold()
        for cut in cuts:
            total += cut
        return (solid ^ total).volume() / thickness

    dish = cover_outside() - cover_inside()
    blank = plate(0, 0, SEAM) - box(WALL, OUT_W - WALL, WALL, OUT_H - WALL, LIP_T, SEAM + 1)
    in_cover = cover_vents()
    hx0, hx1, hz0, hz1 = power_hole()
    power = (hx1 - hx0) * (hz1 - hz0) - PLUG[0] * PLUG[1]
    # A slot in a sloping wall is seen square on to the slope: the wall's own thickness.
    return {
        "bottom": removed(blank, wall_vents(False), WALL)
        + removed(dish, in_cover["bottom"], COVER_WALL),
        "top": removed(blank, wall_vents(True), WALL)
        + removed(dish, in_cover["top"], COVER_WALL)
        + power,
        "back": removed(dish, in_cover["back"], BACK_T),
    }


# ---- output --------------------------------------------------------------------------------------


def write_stl(part: Manifold, name: str) -> None:
    mesh = part.to_mesh()
    vertices = np.asarray(mesh.vert_properties, dtype=np.float32)[:, :3]
    triangles = vertices[np.asarray(mesh.tri_verts)]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    with open(OUT / name, "wb") as handle:
        handle.write(b"FlightTrack case".ljust(80, b" "))
        handle.write(struct.pack("<I", len(triangles)))
        for normal, triangle in zip(normals, triangles, strict=True):
            handle.write(struct.pack("<12fH", *normal, *triangle.reshape(-1), 0))


def on_bed(part: Manifold) -> Manifold:
    b = part.bounding_box()
    return part.translate([-b[0], -b[1], -b[2]])


def back_down(part: Manifold) -> Manifold:
    """Turned over to rest on the print bed on what was its back."""
    return on_bed(part.rotate([180, 0, 0]))


def print_orientation(body: Manifold, cover: Manifold, stand: Manifold) -> dict[str, Manifold]:
    """Each part turned the way it should sit on the print bed: the body front face down, the
    cover back down, the stands flat and nested."""
    return {
        "body.stl": on_bed(body),
        "cover.stl": back_down(cover),
        "wedgestand.stl": wedge_pair(stand),
    }


def wedge_pair(stand: Manifold) -> Manifold:
    """The left and right wedge stands, flat on the bed, nested."""
    left = on_bed(stand.transform(np.array([[0.0, 1, 0, 0], [0, 0, 1, 0], [1.0, 0, 0, 0]])))
    # Mirrored, and turned round to nest against the first: the right-hand stand.
    right = on_bed(left.mirror([1, 0, 0]).rotate([0, 0, 180])).translate([-46.0, -54.0, 0])
    return on_bed(left + right)


def main() -> None:
    body, cover, stand = make_body(), make_cover(), make_wedge_stand()
    wrong = check(body, cover) + check_wedge_stand(stand, body, cover)
    areas = vent_areas()
    for where in ("bottom", "top"):
        have = areas[where] + (areas["back"] if where == "top" else 0)
        if have < VENT_TARGET_MM2:
            wrong.append(
                f"vents at the {where}: {have / 100:.1f} cm2, target {VENT_TARGET_MM2 / 100:.0f}"
            )
    if len(wedge_pair(stand).decompose()) != 2:
        wrong.append("the two stands touch on the print bed: move them apart in wedge_pair")
    standing = stability(stand, body, cover)
    if standing["weight_back"] - standing["front"] < 20:
        wrong.append("on the stands the weight is too near the front")

    wide = OUT_W + 2 * (FIT + STAND_T + 0.3 + EAR_T)
    print(f"outside   {OUT_W:.1f} x {OUT_H:.1f} x {Z_BACK:.1f} mm, {wide:.1f} wide over the knobs")
    print("lip       over the edge: " + ", ".join(f"{k} {v:.1f}" for k, v in LIP.items()))
    print(
        f"vents     bottom {areas['bottom'] / 100:.1f} cm2, top {areas['top'] / 100:.1f} cm2,"
        f" back {areas['back'] / 100:.1f} cm2"
    )
    print(
        f"standing  leans back {WEDGE_TILT:.0f} deg, front edge {wedge_lift():.1f} mm up;"
        f" weight {standing['weight_back'] - standing['front']:.0f} mm behind the toes and"
        f" {standing['rear'] - standing['weight_back']:.0f} mm in front of the heels;"
        f" about {standing['tip_newtons']:.1f} N on the top of the screen tips it back;"
        f" snaps on with {snap_depth():.1f} mm of give"
    )
    if wrong:
        print("\nNOT WRITTEN. Problems:")
        for line in wrong:
            print("  -", line)
        raise SystemExit(1)
    for name, part in print_orientation(body, cover, stand).items():
        write_stl(part, name)
    print("written: body.stl, cover.stl, wedgestand.stl")


if __name__ == "__main__":
    main()

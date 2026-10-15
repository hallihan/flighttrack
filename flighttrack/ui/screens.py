"""Full-screen pages that take over from the map: settings, and choosing a Wi-Fi network.

A page is a scrolling list of items, or several of them under a row of tabs. Drag to scroll,
tap to act. The list is rebuilt from the current state before every frame, so a page never
shows stale values.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import hypot

import pygame

from flighttrack import __version__, credits
from flighttrack.keyed import usage_lines
from flighttrack.platform.wifi import Network, Wifi
from flighttrack.portal import validate
from flighttrack.secrets_store import KEY_FIELDS, KEY_GROUPS, MIN_PASSWORD, shown
from flighttrack.settings import FRACTION_SETTINGS, MAP_STYLES, THEMES
from flighttrack.ui import theme
from flighttrack.ui.format import receiver_lines
from flighttrack.ui.go import GoScreen
from flighttrack.ui.keyboard import Keyboard
from flighttrack.ui.widgets import draw_edit, draw_home, draw_pin, draw_plus, draw_save, draw_trash

HEADER_PX = 56
TAB_PX = 50  # height of the row of tabs, on a page that has them
MARGIN = 24
TAP_SLOP_PX = 14
SLIDER_GRAB_PX = 24  # how far above or below its track a touch still takes hold of a slider
SIDE_BUTTON_PX = 110  # width of the second button a row can carry
# Anything destructive takes two taps. The second must not follow at once (that is a double
# tap or a bouncing touch, not a decision), and the first does not wait for it for ever.
CONFIRM_MIN_S = 0.6
CONFIRM_MAX_S = 5.0

HEADING, SLIDER, SWITCH, TEXT, BUTTON, INFO, CHOICE, ROW, HEAD = range(9)
_HEIGHTS = {
    HEADING: 44,
    SLIDER: 70,
    SWITCH: 56,
    TEXT: 72,
    BUTTON: 60,
    INFO: 26,
    CHOICE: 60,
    ROW: 56,  # one entry of a list, with its icon buttons at the right
    HEAD: 56,  # a list's heading, with its own icon buttons (the + that adds to it)
}
CHOICE_LABEL_PX = 170  # room for the label before a row of options
# Icon buttons at the right of a row, in slots of one width, so the same button lines up
# from row to row; a row without one of them leaves its slot empty.
ICON_SLOT_PX = 60
ICON_FACE = (50, 42)


@dataclass(frozen=True)
class Icon:
    """An icon button: which icon, whether it is lit, and what a tap does (None: inert)."""

    name: str  # "pin", "trash", "home", "plus", "edit" or "save"
    on: bool = False
    action: Callable[[], None] | None = None
    lit: theme.Color | None = None  # the color it is drawn in when on (else the accent)


_ICONS = {
    "pin": draw_pin,
    "trash": draw_trash,
    "home": draw_home,
    "plus": draw_plus,
    "edit": draw_edit,
    "save": draw_save,
}


@dataclass
class Item:
    kind: int
    label: str = ""
    value: str = ""  # text fields: what is set; buttons: a note at the right
    detail: str = ""  # a smaller line underneath
    error: bool = False  # show the detail line as a problem
    fraction: float = 0.0  # sliders: position, 0-1 along the track
    on: bool = False  # switches
    action: Callable[[], None] | None = None  # switches, text fields and buttons: on tap
    on_slide: Callable[[float], None] | None = None  # sliders: called with 0-1 as it moves
    on_release: Callable[[], None] | None = None  # sliders: when the finger lifts
    # Buttons: a second, smaller button at the right-hand end, with its own action.
    side_label: str = ""
    side_action: Callable[[], None] | None = None
    disabled: bool = False  # shown dimmed, and taps on it do nothing
    bright: bool = False  # info lines: in the main text color, not dimmed
    plain: bool = False  # buttons: the body is a label, not something to tap; only the side is
    warning: bool = False  # buttons: drawn as the thing to confirm
    # Choices: one button for each option, side by side: its name, whether it is the one in
    # force, and what choosing it does.
    options: tuple[tuple[str, bool, Callable[[], None]], ...] = ()
    # Rows and list headings: icon buttons at the right, the last at the right-hand end;
    # None leaves its slot empty.
    icons: tuple[Icon | None, ...] = ()
    badge: tuple[str, theme.Color] | None = None  # rows: a colored tag at the right, e.g. VFR


def megabytes(count: int) -> str:
    """A size for people: MB below a thousand of them, GB above."""
    mb = count / 1e6
    if mb >= 1000:
        return f"{mb / 1000:.1f} GB"
    return f"{mb:.0f} MB" if mb >= 10 or mb == 0 else f"{mb:.1f} MB"


class ScrollScreen:
    title = ""
    tabs: tuple[str, ...] = ()  # names of the tabs, for a page split into several lists
    clock = staticmethod(time.monotonic)

    def __init__(self, size: tuple[int, int], close: Callable[[], None]) -> None:
        self.size = size
        self.close = close
        self.scroll = 0
        self.tab = 0  # which of the tabs is showing
        self.overflows = False  # whether the list is longer than the screen
        self._content = 0  # height of the whole list
        self.keyboard: Keyboard | None = None
        self._press: tuple[int, int] | None = None
        self._scrolling = False
        self._slider: int | None = None  # index of the slider being dragged
        self._slider_candidate: int | None = None  # a slider the press landed on, not yet moved
        self._layout: list[tuple[pygame.Rect, Item]] = []
        self.done_rect = pygame.Rect(size[0] - 132, 4, 124, HEADER_PX - 8)
        self._armed: tuple[str, float] | None = None  # a first tap waiting for its second
        self._guard_touched = False

    def status(self) -> tuple[str, theme.Color] | None:
        """Something to show in the header, where it stays in sight however far the page is
        scrolled: what the page is busy with, or how the last thing went."""
        return None

    def armed(self, key: str) -> bool:
        """Whether a first tap on this guarded action is still waiting for its second."""
        if self._armed is not None and self.clock() - self._armed[1] > CONFIRM_MAX_S:
            self._armed = None
        return self._armed is not None and self._armed[0] == key

    def confirm(self, key: str) -> bool:
        """Call on every tap of a guarded action. False on the first tap, which arms it, and
        on a second that comes too soon; True on a deliberate second tap."""
        self._guard_touched = True
        if not self.armed(key):
            self._armed = (key, self.clock())
            return False
        if self.clock() - self._armed[1] < CONFIRM_MIN_S:
            return False
        self._armed = None
        return True

    def disarm(self) -> None:
        self._armed = None

    def items(self) -> list[Item]:
        """The rows to show: those of the current tab, on a page with tabs."""
        raise NotImplementedError

    @property
    def top(self) -> int:
        """Where the list starts: under the header, and under the tabs if there are any."""
        return HEADER_PX + (TAB_PX if self.tabs else 0)

    def tab_rects(self) -> list[pygame.Rect]:
        if not self.tabs:
            return []
        width = self.size[0] // len(self.tabs)
        return [pygame.Rect(i * width, HEADER_PX, width, TAB_PX) for i in range(len(self.tabs))]

    def show_tab(self, tab: int | str) -> None:
        index = self.tabs.index(tab) if isinstance(tab, str) else tab
        if index != self.tab:
            self.tab, self.scroll = index, 0
            self._armed = None

    # ---- layout ---------------------------------------------------------------------------

    def _lay_out(self) -> None:
        width, height = self.size
        rows, y = [], 0
        for item in self.items():
            rows.append((y, item))
            y += _HEIGHTS[item.kind]
        room = height - self.top
        self._content = y + 16
        self.overflows = self._content > room
        self.scroll = max(0, min(self.scroll, self._content - room))
        self._layout = [
            (pygame.Rect(0, self.top + top - self.scroll, width, _HEIGHTS[item.kind]), item)
            for top, item in rows
        ]

    @staticmethod
    def _track(rect: pygame.Rect) -> tuple[int, int, int]:
        """A slider's track within its row: left, right and y."""
        return rect.left + MARGIN + 10, rect.right - MARGIN - 10, rect.bottom - 22

    @staticmethod
    def option_rects(rect: pygame.Rect, item: Item) -> list[pygame.Rect]:
        """Where each option of a choice row is: side by side, after the label."""
        left = rect.left + MARGIN + CHOICE_LABEL_PX
        width = (rect.right - MARGIN - left) / max(len(item.options), 1)
        return [
            pygame.Rect(round(left + i * width), rect.top + 6, round(width), rect.height - 12)
            for i in range(len(item.options))
        ]

    @staticmethod
    def icon_rects(rect: pygame.Rect, item: Item) -> list[pygame.Rect]:
        """The touch target of each of a row's icon slots, ending at the right margin."""
        count = len(item.icons)
        right = rect.right - MARGIN
        return [
            pygame.Rect(right - (count - i) * ICON_SLOT_PX, rect.top, ICON_SLOT_PX, rect.height)
            for i in range(count)
        ]

    def _item_at(self, pos: tuple[int, int]) -> tuple[int, pygame.Rect, Item] | None:
        if pos[1] < self.top:
            return None
        for index, (rect, item) in enumerate(self._layout):
            if rect.collidepoint(pos):
                return index, rect, item
        return None

    # ---- input ----------------------------------------------------------------------------

    def open_keyboard(
        self,
        title: str,
        text: str,
        on_done: Callable[[str], None],
        hint: str = "",
        secret: bool = False,
    ) -> None:
        def done(value: str) -> None:
            self.keyboard = None
            on_done(value)

        def cancel() -> None:
            self.keyboard = None

        self.keyboard = Keyboard(self.size, title, text, done, cancel, hint, secret)

    def _slide(self, rect: pygame.Rect, item: Item, x: int) -> None:
        left, right, _ = self._track(rect)
        if item.on_slide is not None:
            item.on_slide(min(max((x - left) / (right - left), 0.0), 1.0))

    def handle_event(self, event: pygame.event.Event) -> None:
        if self.keyboard is not None:
            self.keyboard.handle_event(event)
            return
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._lay_out()
            self._press, self._scrolling = event.pos, False
            self._slider = self._slider_candidate = None
            hit = self._item_at(event.pos)
            if hit is not None and hit[2].kind == SLIDER:
                if abs(event.pos[1] - self._track(hit[1])[2]) <= SLIDER_GRAB_PX:
                    self._slider_candidate = hit[0]
        elif event.type == pygame.MOUSEMOTION and event.buttons[0] and self._press is not None:
            dx, dy = event.pos[0] - self._press[0], event.pos[1] - self._press[1]
            if self._slider is not None:
                self._lay_out()
                rect, item = self._layout[self._slider]
                self._slide(rect, item, event.pos[0])
            elif self._scrolling:
                self.scroll -= dy
                self._press = event.pos
                self._lay_out()  # keeps the scroll within the page
            elif (
                self._slider_candidate is not None and abs(dx) > TAP_SLOP_PX and abs(dx) >= abs(dy)
            ):
                # A sideways drag on a slider moves it; an up-and-down one scrolls the page.
                self._slider, self._slider_candidate = self._slider_candidate, None
                self._lay_out()
                rect, item = self._layout[self._slider]
                self._slide(rect, item, event.pos[0])
            elif abs(dy) > TAP_SLOP_PX:
                self._scrolling = True
                self._slider_candidate = None
                self._press = event.pos
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._press is not None:
            press, self._press = self._press, None
            moved = hypot(event.pos[0] - press[0], event.pos[1] - press[1])
            if self._slider is None and self._slider_candidate is not None and moved <= TAP_SLOP_PX:
                self._slider = self._slider_candidate  # a tap on the track jumps there
                self._lay_out()
                rect, item = self._layout[self._slider]
                self._slide(rect, item, event.pos[0])
            if self._slider is not None:
                self._lay_out()
                item = self._layout[self._slider][1]
                if item.on_release is not None:
                    item.on_release()
            elif not self._scrolling and moved <= TAP_SLOP_PX:
                self.tap(event.pos)
            self._scrolling = False
            self._slider = self._slider_candidate = None

    def tap(self, pos: tuple[int, int]) -> None:
        if self.done_rect.collidepoint(pos):
            self.close()
            return
        for index, rect in enumerate(self.tab_rects()):
            if rect.collidepoint(pos):
                self.show_tab(index)
                return
        self._guard_touched = False
        self._lay_out()
        hit = self._item_at(pos)
        if hit is not None and hit[2].kind != SLIDER and not hit[2].disabled:
            _, rect, item = hit
            slots = self.icon_rects(rect, item)
            if item.kind == CHOICE:
                for face, (_, _, choose) in zip(
                    self.option_rects(rect, item), item.options, strict=True
                ):
                    if face.collidepoint(pos):
                        choose()
            elif slots and pos[0] >= slots[0].left:
                for slot, icon in zip(slots, item.icons, strict=True):
                    if slot.collidepoint(pos) and icon is not None and icon.action is not None:
                        icon.action()
            elif item.side_action is not None and pos[0] >= rect.right - MARGIN - SIDE_BUTTON_PX:
                item.side_action()
            elif item.action is not None:
                item.action()
        if not self._guard_touched:
            self._armed = None  # a tap anywhere else calls off whatever was waiting

    # ---- drawing --------------------------------------------------------------------------

    def draw(self, surface: pygame.Surface, fonts: theme.Fonts, now: float) -> None:
        if self.keyboard is not None:
            self.keyboard.draw(surface, fonts, now)
            return
        self._lay_out()
        width, height = self.size
        surface.fill(theme.BACKGROUND)
        top = self.top
        surface.set_clip(pygame.Rect(0, top, width, height - top))
        for index, (rect, item) in enumerate(self._layout):
            if rect.bottom >= top and rect.top <= height:
                self._draw_item(surface, fonts, rect, item, index == self._slider)
        surface.set_clip(None)
        if self.overflows:
            # A scroll bar, so it is plain there is more than fits.
            room = height - top
            bar = max(32, round(room * room / self._content))
            offset = round((room - bar) * self.scroll / (self._content - room))
            pygame.draw.rect(
                surface, theme.EDGE, (width - 7, top + offset + 3, 4, bar - 6), border_radius=2
            )
        for index, rect in enumerate(self.tab_rects()):
            current = index == self.tab
            pygame.draw.rect(surface, theme.PANEL_FILL if current else theme.BAR_FILL, rect)
            name = theme.text(
                fonts.body, self.tabs[index], theme.TEXT if current else theme.TEXT_DIM
            )
            surface.blit(name, name.get_rect(center=rect.center))
            if current:
                pygame.draw.rect(surface, theme.ACCENT, (rect.left, rect.bottom - 4, rect.width, 4))
            elif index:
                pygame.draw.line(
                    surface, theme.EDGE, (rect.left, rect.top + 12), (rect.left, rect.bottom - 12)
                )
        if self.tabs:
            pygame.draw.line(surface, theme.EDGE, (0, top), (width, top))

        pygame.draw.rect(surface, theme.BAR_FILL, (0, 0, width, HEADER_PX))
        pygame.draw.line(surface, theme.EDGE, (0, HEADER_PX), (width, HEADER_PX))
        title = theme.text(fonts.title, self.title, theme.ACCENT)
        placed = title.get_rect(midleft=(MARGIN, HEADER_PX // 2))
        surface.blit(title, placed)
        status = self.status()
        if status is not None:
            words, color = status
            room = self.done_rect.left - placed.right - 36
            while len(words) > 4 and fonts.body.size(words)[0] > room:
                words = words[:-4].rstrip() + "..."
            note = theme.text(fonts.body, words, color)
            surface.blit(note, note.get_rect(midleft=(placed.right + 20, HEADER_PX // 2)))
        pygame.draw.rect(surface, theme.TOGGLE_ON, self.done_rect, border_radius=8)
        done = theme.text(fonts.title, "Done", theme.ON_ACCENT)
        surface.blit(done, done.get_rect(center=self.done_rect.center))

    def _draw_item(
        self, surface: pygame.Surface, fonts: theme.Fonts, rect: pygame.Rect, item: Item, held: bool
    ) -> None:
        left, right = rect.left + MARGIN, rect.right - MARGIN
        if item.kind == HEADING:
            text = theme.text(fonts.body, item.label.upper(), theme.ACCENT)
            surface.blit(text, (left, rect.bottom - 26))
            pygame.draw.line(surface, theme.EDGE, (left, rect.bottom - 4), (right, rect.bottom - 4))
        elif item.kind == INFO:
            color = theme.TEXT if item.bright else theme.TEXT_DIM
            color = theme.EMERGENCY if item.error else color
            surface.blit(theme.text(fonts.body, item.label, color), (left, rect.top + 4))
        elif item.kind == SLIDER:
            surface.blit(theme.text(fonts.title, item.label, theme.TEXT), (left, rect.top + 8))
            percent = theme.text(fonts.title, item.value, theme.TEXT_DIM)
            surface.blit(percent, percent.get_rect(topright=(right, rect.top + 8)))
            start, end, y = self._track(rect)
            knob = start + round((end - start) * item.fraction)
            pygame.draw.line(surface, theme.EDGE, (start, y), (end, y), width=6)
            pygame.draw.line(surface, theme.TOGGLE_ON, (start, y), (knob, y), width=6)
            pygame.draw.circle(surface, theme.TEXT if held else theme.ACCENT, (knob, y), 13)
        elif item.kind == SWITCH:
            label = theme.text(fonts.title, item.label, theme.TEXT)
            surface.blit(label, label.get_rect(midleft=(left, rect.centery)))
            track = pygame.Rect(0, 0, 60, 32)
            track.midright = (right, rect.centery)
            fill = theme.TOGGLE_ON if item.on else theme.BUTTON_DISABLED
            pygame.draw.rect(surface, fill, track, border_radius=16)
            pygame.draw.rect(surface, theme.EDGE, track, width=1, border_radius=16)
            knob_x = track.right - 16 if item.on else track.left + 16
            knob = theme.ON_ACCENT if item.on else theme.TEXT
            pygame.draw.circle(surface, knob, (knob_x, track.centery), 12)
        elif item.kind == TEXT:
            surface.blit(theme.text(fonts.body, item.label, theme.TEXT_DIM), (left, rect.top + 6))
            box = pygame.Rect(left, rect.top + 26, right - left, 30)
            pygame.draw.rect(surface, theme.PANEL_FILL, box, border_radius=6)
            pygame.draw.rect(surface, theme.EDGE, box, width=1, border_radius=6)
            shown = item.value or "Tap to set"
            while fonts.title.size(shown)[0] > box.width - 20 and len(shown) > 4:
                shown = shown[:-4] + "..."
            color = theme.TEXT if item.value else theme.TEXT_DIM
            value = theme.text(fonts.title, shown, color)
            surface.blit(value, value.get_rect(midleft=(box.left + 10, box.centery)))
            if item.detail:
                color = theme.EMERGENCY if item.error else theme.TEXT_DIM
                surface.blit(theme.text(fonts.small, item.detail, color), (left, rect.bottom - 15))
        elif item.kind == CHOICE:
            label = theme.text(fonts.title, item.label, theme.TEXT)
            surface.blit(label, label.get_rect(midleft=(left, rect.centery)))
            faces = self.option_rects(rect, item)
            for index, (face, (name, chosen, _)) in enumerate(
                zip(faces, item.options, strict=True)
            ):
                # One bar cut into segments: rounded only at its two ends.
                first, last = index == 0, index == len(faces) - 1
                ends = {
                    "border_top_left_radius": 8 if first else 0,
                    "border_bottom_left_radius": 8 if first else 0,
                    "border_top_right_radius": 8 if last else 0,
                    "border_bottom_right_radius": 8 if last else 0,
                }
                fill = theme.TOGGLE_ON if chosen else theme.BUTTON_FILL
                pygame.draw.rect(surface, fill, face, **ends)
                pygame.draw.rect(surface, theme.EDGE, face, width=1, **ends)
                words = theme.text(fonts.title, name, theme.ON_ACCENT if chosen else theme.TEXT_DIM)
                surface.blit(words, words.get_rect(center=face.center))
        elif item.kind in (ROW, HEAD):
            self._draw_list_item(surface, fonts, rect, item)
        elif item.kind == BUTTON:
            face = pygame.Rect(left, rect.top + 6, right - left, rect.height - 12)
            fill = theme.BUTTON_DISABLED if item.disabled else theme.BUTTON_FILL
            if item.plain:
                fill = theme.BACKGROUND
            pygame.draw.rect(
                surface, theme.CONFIRM_FILL if item.warning else fill, face, border_radius=8
            )
            pygame.draw.rect(surface, theme.EDGE, face, width=1, border_radius=8)
            note_right = face.right - 14
            if item.side_action is not None:
                side = pygame.Rect(
                    face.right - SIDE_BUTTON_PX, face.top, SIDE_BUTTON_PX, face.height
                )
                pygame.draw.rect(
                    surface,
                    theme.BUTTON_FILL
                    if item.plain and not item.disabled
                    else theme.BUTTON_DISABLED,
                    side,
                    border_top_right_radius=8,
                    border_bottom_right_radius=8,
                )
                pygame.draw.line(surface, theme.EDGE, side.topleft, side.bottomleft)
                text = theme.text(fonts.body, item.side_label, theme.TEXT)
                surface.blit(text, text.get_rect(center=side.center))
                note_right = side.left - 12
            note_width = 0
            if item.value:
                note = theme.text(fonts.body, item.value, theme.TEXT_DIM)
                surface.blit(note, note.get_rect(midright=(note_right, face.centery)))
                note_width = note.get_width() + 12
            shown = item.label
            room = note_right - note_width - face.left - 14
            while len(shown) > 4 and fonts.title.size(shown)[0] > room:
                shown = shown[:-4].rstrip() + "..."
            label = theme.text(fonts.title, shown, theme.TEXT_DIM if item.disabled else theme.TEXT)
            surface.blit(label, label.get_rect(midleft=(face.left + 14, face.centery)))

    def _draw_list_item(
        self, surface: pygame.Surface, fonts: theme.Fonts, rect: pygame.Rect, item: Item
    ) -> None:
        """A list entry, or a list's heading: words at the left, icon buttons at the right."""
        left, right = rect.left + MARGIN, rect.right - MARGIN
        slots = self.icon_rects(rect, item)
        end = slots[0].left - 8 if slots else right
        if item.kind == HEAD:
            text = theme.text(fonts.body, item.label.upper(), theme.ACCENT)
            surface.blit(text, text.get_rect(midleft=(left, rect.centery)))
            pygame.draw.line(surface, theme.EDGE, (left, rect.bottom - 2), (right, rect.bottom - 2))
        else:
            face = pygame.Rect(left, rect.top + 4, end - left, rect.height - 8)
            fill = theme.CONFIRM_FILL if item.warning else theme.PANEL_FILL
            pygame.draw.rect(surface, fill, face, border_radius=8)
            pygame.draw.rect(surface, theme.EDGE, face, width=1, border_radius=8)
            # At the right of the row: a badge, and a note (value) before it, dimmed.
            note_right = face.right - 12
            if item.badge is not None:
                words, fill = item.badge
                tag = theme.text(fonts.body, words, theme.TEXT)
                box = tag.get_rect(midright=(note_right - 6, face.centery)).inflate(14, 6)
                pygame.draw.rect(surface, fill, box, border_radius=5)
                surface.blit(tag, tag.get_rect(center=box.center))
                note_right = box.left - 10
            if item.value:
                note = theme.text(fonts.body, item.value, theme.TEXT_DIM)
                surface.blit(note, note.get_rect(midright=(note_right, face.centery)))
                note_right -= note.get_width() + 12
            shown = item.label
            while len(shown) > 4 and fonts.title.size(shown)[0] > note_right - face.left - 12:
                shown = shown[:-4].rstrip() + "..."
            color = theme.TEXT_DIM if item.disabled else theme.TEXT
            label = theme.text(fonts.title, shown, color)
            surface.blit(label, label.get_rect(midleft=(face.left + 12, face.centery)))
        for slot, icon in zip(slots, item.icons, strict=True):
            if icon is None:
                continue
            button = pygame.Rect((0, 0), ICON_FACE)
            button.center = slot.center
            usable = icon.action is not None and not item.disabled
            pygame.draw.rect(surface, theme.BUTTON_FILL, button, border_radius=8)
            lit = icon.lit or theme.ACCENT
            edge = lit if icon.on else theme.EDGE
            pygame.draw.rect(surface, edge, button, width=1, border_radius=8)
            color = lit if icon.on else (theme.TEXT if usable else theme.EDGE)
            _ICONS[icon.name](surface, button.center, color, icon.on, 0.85)


class SettingsScreen(ScrollScreen):
    """Everything adjustable, on the device: the display, and all that the web page offers."""

    title = "SETTINGS"
    # Each tab is short enough to see nearly all of at once; nothing is more than a tap away.
    DISPLAY, MAP, AIRPORTS, AIRCRAFT, FEEDS, SYSTEM, STORAGE, ABOUT = TABS = (
        "Display",
        "Map",
        "Airports",
        "Aircraft",
        "Feeds",
        "System",
        "Storage",
        "About",
    )
    tabs = TABS

    def __init__(self, size: tuple[int, int], close: Callable[[], None], app, wifi: Wifi) -> None:
        super().__init__(size, close)
        self.app = app
        self.wifi = wifi
        self.errors: dict[str, str] = {}
        self.child: WifiScreen | GoScreen | None = None  # a page opened from this one
        self.shutdown_message = ""
        self.reset_message = ""
        self.cache_message = ""
        self.can_shut_down = False
        threading.Thread(target=self._ask_power, name="power", daemon=True).start()

    def _ask_power(self) -> None:
        self.can_shut_down = self.app.power.can_power_off()

    def shut_down(self) -> None:
        """Two taps: the first asks, the second does it. A tap anywhere else calls it off."""
        if not self.confirm("shutdown"):
            return
        self.shutdown_message = "Shutting down..."
        self.app.flush_settings()  # nothing changed in the last few minutes is lost

        def work() -> None:
            started, message = self.app.power.power_off()
            self.shutdown_message = message

        threading.Thread(target=work, name="power", daemon=True).start()

    @property
    def confirming_shutdown(self) -> bool:
        return self.armed("shutdown")

    def remove_airport(self, airport) -> None:
        """Second step of removing a favorite airport (the first was its Remove button)."""
        if self.confirm(f"airport:{airport.ident}"):
            self.app.toggle_favorite_airport(airport)

    def _airport_item(self, airport, home: str | None) -> Item:
        """A favorite airport: a house to make it home (filled for the home one) and a bin
        to remove it, which asks first in the body of the row."""
        key = f"airport:{airport.ident}"
        is_home = airport.ident == home
        house = Icon("home", is_home, lambda: self.app.choose_favorite_airport(airport))
        if self.armed(key):
            also = " It is home." if is_home else ""
            return Item(
                ROW,
                f"Remove {airport.ident}?{also} Tap here.",
                action=lambda: self.remove_airport(airport),
                warning=True,
                icons=(house, Icon("trash", True, self.disarm, theme.EMERGENCY)),
            )
        return Item(
            ROW,
            f"{airport.ident}  {airport.name}",
            icons=(house, Icon("trash", False, lambda: self.confirm(key))),
            badge=self._flight_category(airport.ident),
        )

    def _flight_category(self, ident: str) -> tuple[str, theme.Color] | None:
        """The airport's flight category from its latest METAR, as a badge, if it reports."""
        report = self.app.metar.lookup(ident) if self.app.metar is not None else None
        category = getattr(report, "category", None)
        return (category, theme.FLIGHT_CATEGORY[category]) if category else None

    def remove_aircraft(self, entry: str) -> None:
        """Second step of removing a favorite aircraft (the first was its bin)."""
        if self.confirm(f"plane:{entry}"):
            self.app.remove_favorite_aircraft(entry)

    def _aircraft_item(self, entry: str, pinned: bool, where: str = "") -> Item:
        """A favorite aircraft: the pin that keeps its track on the map, and a bin."""
        key = f"plane:{entry}"
        pin = Icon("pin", pinned, lambda: self.app.toggle_pinned_favorite(entry))
        if self.armed(key):
            return Item(
                ROW,
                f"Remove {entry}? Tap here.",
                action=lambda: self.remove_aircraft(entry),
                warning=True,
                icons=(pin, Icon("trash", True, self.disarm, theme.EMERGENCY)),
            )
        return Item(ROW, entry, where, icons=(pin, Icon("trash", False, lambda: self.confirm(key))))

    def _slider_item(self, label: str, name: str) -> Item:
        low = FRACTION_SETTINGS[name]
        value = getattr(self.app.settings, name)
        return Item(
            SLIDER,
            label,
            value=f"{round(value * 100)}%",
            fraction=(value - low) / (1.0 - low),
            on_slide=lambda f: self.app.set_fraction(name, low + f * (1.0 - low)),
            on_release=self.app.save_settings,
        )

    def _secret(
        self, field: str, title: str, hint: str, apply: Callable[[str], str | None]
    ) -> None:
        """The keyboard for a key or a password: empty to begin with (what is kept is never
        shown again), dots unless Show is tapped. An error goes under the row."""

        def done(text: str) -> None:
            error = apply(text)
            if error:
                self.errors[field] = error
            else:
                self.errors.pop(field, None)

        self.open_keyboard(title, "", done, hint, secret=True)

    def _key_items(self, field: str) -> list[Item]:
        """A key: what is kept of it (dots and its last four), a pencil to type a new one, and
        a bin, grayed out with nothing kept, that asks first. A problem goes under it."""
        value = getattr(self.app.settings, field)
        label = KEY_FIELDS[field]
        key = f"key:{field}"

        def edit() -> None:
            self._secret(field, label, "Paste or type it.", lambda t: self.app.set_key(field, t))

        def remove() -> None:
            if self.confirm(key):
                self.app.set_key(field, "")

        pencil = Icon("edit", False, edit)
        if value and self.armed(key):
            row = Item(
                ROW,
                f"Remove the {label}? Tap here.",
                action=remove,
                warning=True,
                icons=(pencil, Icon("trash", True, self.disarm, theme.EMERGENCY)),
            )
        else:
            bin_ = Icon("trash", False, (lambda: self.confirm(key)) if value else None)
            row = Item(ROW, label, shown(field, value) or "Not set", icons=(pencil, bin_))
        error = self.errors.get(field)
        return [row, *([Item(INFO, error, error=True)] if error else [])]

    def _password_item(self) -> Item:
        stored = self.app.settings.portal_password
        note = (
            "Saving..."
            if self.app.password_saving
            else "Set: the web page asks for it before showing the settings."
            if stored
            else "Not set: the web page shows only a picture of the screen."
        )
        return Item(
            TEXT,
            "Web page password",
            "\u2022" * 8 if stored else "",
            self.errors.get("portal_password", note),
            "portal_password" in self.errors,
            action=lambda: self._secret(
                "portal_password",
                "Web page password",
                f"At least {MIN_PASSWORD} characters. Leave empty to remove it.",
                self.app.set_portal_password,
            ),
        )

    def _switch_item(self, label: str, name: str) -> Item:
        return Item(
            SWITCH, label, on=getattr(self.app.settings, name), action=lambda: self.app.flip(name)
        )

    def _edit(self, field: str, title: str, current: str, hint: str) -> None:
        """Open the keyboard for one of the fields the web page also has, and apply the
        result through the same validation."""

        def done(text: str) -> None:
            s = self.app.settings
            form = {
                "home_airport": s.home_airport or "",
                "favorite_airports": " ".join(s.favorite_airports),
                "local_receiver_url": s.local_receiver_url or "",
                "favorites": "\n".join(s.favorites),
            }
            form[field] = text
            changes, errors = validate(form)
            if field in errors:
                self.errors[field] = errors[field]
            else:
                self.errors.pop(field, None)
                self.app.apply_changes({field: changes[field]})

        self.open_keyboard(title, current, done, hint)

    def items(self) -> list[Item]:
        app, s = self.app, self.app.settings
        location = app.location
        favorite_airports = app.favorite_airports()
        if favorite_airports:
            where = "The filled house is home. Tap another house to move it."
        elif location is not None and location.source == "ip":
            where = "No airports yet, so home is the current location."
        else:
            where = "No airports yet."
        url = app.portal_url(time.monotonic())

        name = self.tabs[self.tab]
        if name == self.DISPLAY:
            return [
                Item(
                    CHOICE,
                    "Theme",
                    options=tuple(
                        (label, key == s.theme, lambda key=key: app.set_theme(key))
                        for key, label in THEMES.items()
                    ),
                ),
                self._slider_item("Screen brightness", "brightness"),
                self._slider_item("Map brightness", "map_brightness"),
                self._slider_item("Range ring opacity", "ring_opacity"),
                self._slider_item("Map features opacity", "feature_opacity"),
                self._switch_item("Day and night shading", "day_night"),
            ]
        if name == self.MAP:
            return [
                self._switch_item("Map", "show_map"),
                Item(
                    CHOICE,
                    "Kind of map",
                    options=tuple(
                        (name, style == s.map_style, lambda style=style: app.set_map_style(style))
                        for style, name in MAP_STYLES.items()
                    ),
                ),
                self._switch_item("Rain radar", "show_radar"),
                self._switch_item("Cloud cover", "show_clouds"),
                self._slider_item("Weather opacity", "weather_opacity"),
                self._switch_item("Airspace boundaries", "show_airspace"),
                self._switch_item("Runways at close range", "show_runways"),
            ]
        if name == self.AIRPORTS:
            known = app.current_location() is not None
            return [
                Item(HEAD, "Favorite airports", icons=(Icon("plus", False, self.open_go),)),
                # Always first and never removable: where the internet address says we are.
                # Its bin's slot is left empty, so the houses line up.
                Item(
                    ROW,
                    app.current_location_label(),
                    disabled=not known,
                    icons=(
                        Icon(
                            "home",
                            app.current_location_is_home(),
                            (lambda: app.go_here(True)) if known else None,
                        ),
                        None,
                    ),
                ),
                *(self._airport_item(airport, s.home_airport) for airport in favorite_airports),
                Item(INFO, where),
            ]
        if name == self.AIRCRAFT:
            pinned = set(s.pinned_favorites)
            seen = app.favorite_whereabouts()
            planes = [
                self._aircraft_item(entry, entry in pinned, seen.get(entry, ""))
                for entry in s.favorites
            ]
            return [
                Item(
                    HEAD, "Favorite aircraft", icons=(Icon("plus", False, self.open_add_aircraft),)
                ),
                *planes,
                Item(
                    INFO,
                    "Pinned: its track stays on the map when it is not selected."
                    if planes
                    else "None yet. Tap + to add one.",
                ),
            ]
        if name == self.FEEDS:
            return [
                Item(HEADING, "Your receiver"),
                Item(
                    TEXT,
                    "Local ADS-B receiver",
                    s.local_receiver_url or "",
                    self.errors.get(
                        "local_receiver_url",
                        "Address of your receiver's aircraft.json, if you have one",
                    ),
                    "local_receiver_url" in self.errors,
                    action=lambda: self._edit(
                        "local_receiver_url",
                        "Local ADS-B receiver",
                        s.local_receiver_url or "http://",
                        "For example http://receiver.local/tar1090/data/aircraft.json",
                    ),
                ),
                *(
                    Item(INFO, line, error=bad, bright=good)
                    for line, good, bad in receiver_lines(app.receiver_status(), time.time())
                ),
                *(
                    item
                    for group, fields, about in KEY_GROUPS
                    for item in (
                        Item(HEADING, group),
                        *(row for field in fields for row in self._key_items(field)),
                        *(
                            Item(INFO, line, error=bad, bright=good)
                            for line, good, bad in app.service_lines(group)
                        ),
                        Item(INFO, about),
                    )
                ),
                Item(HEADING, "Requests today"),
                *(Item(INFO, line) for line in usage_lines()),
            ]
        if name == self.SYSTEM:
            return [
                Item(HEADING, "Network"),
                Item(
                    BUTTON,
                    "Wi-Fi network",
                    app.network_summary(time.monotonic()),
                    action=self.open_wifi if self.wifi.available else None,
                ),
                *(
                    []
                    if self.wifi.available
                    else [Item(INFO, "Wi-Fi can't be changed from here on this system.")]
                ),
                Item(
                    INFO, f"Settings web page: {url}" if url else "Settings web page: unavailable"
                ),
                self._password_item(),
                Item(HEADING, "Power"),
                *self._power_items(),
                *self._reset_items(),
            ]
        if name == self.STORAGE:
            return self._storage_items()
        rows = [
            Item(INFO, f"FlightTrack {__version__}", bright=True),
            Item(INFO, credits.NOT_FOR_NAVIGATION),
            Item(HEADING, "Where the data comes from"),
        ]
        for source, what, more in credits.SOURCES:
            rows.append(Item(INFO, f"{source}: {what}", bright=True))
            rows.append(Item(INFO, f"    {more}"))
        return rows

    def clear_caches(self) -> None:
        if self.confirm("caches"):
            self.app.clear_caches()
            self.cache_message = "Caches cleared. Maps will load again as they are needed."

    def _storage_items(self) -> list[Item]:
        sizes = self.app.storage(time.monotonic())
        if sizes is None:
            rows = [Item(INFO, "Measuring...")]
        else:
            rows = [
                Item(
                    INFO,
                    f"SD card: {megabytes(sizes['free'])} free of {megabytes(sizes['total'])}",
                    bright=True,
                ),
                Item(
                    INFO,
                    f"Caches: {megabytes(sizes['used'])} used, "
                    f"up to {megabytes(sizes['budget'])} allowed",
                    bright=True,
                ),
                *(Item(INFO, f"    {name}: {megabytes(size)}") for name, size in sizes["items"]),
            ]
        if self.armed("caches"):
            rows.append(
                Item(
                    BUTTON,
                    "Clear caches?",
                    "Tap again to confirm",
                    action=self.clear_caches,
                    warning=True,
                )
            )
        else:
            rows.append(Item(BUTTON, "Clear caches", action=self.clear_caches))
        rows.append(
            Item(
                INFO,
                self.cache_message or "Maps and photos are used for 30 days, then fetched afresh.",
            )
        )
        return rows

    def reset(self) -> None:
        """Two taps, like anything that cannot be undone."""
        if self.confirm("reset"):
            self.app.reset_settings()
            self.reset_message = "Settings are back to their defaults."

    def _reset_items(self) -> list[Item]:
        if self.armed("reset"):
            return [
                Item(
                    BUTTON,
                    "Reset all settings?",
                    "Tap again to confirm",
                    action=self.reset,
                    warning=True,
                ),
                Item(INFO, "Home, favorite airports and aircraft, and your receiver are kept."),
            ]
        return [
            Item(BUTTON, "Reset all settings", action=self.reset),
            Item(
                INFO,
                self.reset_message
                or "Brightness, opacities, layers, filters and range go back to their defaults.",
            ),
        ]

    def _power_items(self) -> list[Item]:
        if self.shutdown_message:
            return [Item(INFO, self.shutdown_message)]
        if not self.can_shut_down:
            return [Item(INFO, "Shutting down from here is not available on this system.")]
        if self.confirming_shutdown:
            return [
                Item(
                    BUTTON,
                    "Shut down now?",
                    "Tap again to confirm",
                    action=self.shut_down,
                    warning=True,
                ),
                Item(INFO, "Wait for the screen to go dark before unplugging."),
            ]
        return [
            Item(BUTTON, "Shut down", action=self.shut_down),
            Item(INFO, "Closes everything down safely so the power can be unplugged."),
        ]

    def open_go(self) -> None:
        def closed() -> None:
            self.child = None

        self.child = self.app.go_page(closed, adding=True)

    def open_add_aircraft(self) -> None:
        def closed() -> None:
            self.child = None

        self.child = self.app.add_aircraft_page(closed)

    def open_wifi(self) -> None:
        def closed() -> None:
            self.child = None

        self.child = WifiScreen(self.size, closed, self.wifi)

    def handle_event(self, event: pygame.event.Event) -> None:
        if self.child is not None:
            self.child.handle_event(event)
        else:
            super().handle_event(event)

    def draw(self, surface: pygame.Surface, fonts: theme.Fonts, now: float) -> None:
        if self.child is not None:
            self.child.draw(surface, fonts, now)
        else:
            super().draw(surface, fonts, now)


class WifiScreen(ScrollScreen):
    """Join a network in range, and manage the networks the display remembers."""

    title = "WI-FI"

    def __init__(self, size: tuple[int, int], close: Callable[[], None], wifi: Wifi) -> None:
        super().__init__(size, close)
        self.wifi = wifi
        self.networks: list[Network] = []
        self.saved: list[str] = []
        self.busy: str | None = None  # what is happening in the background, if anything
        self.message = ""
        self.failed = False
        self.scan()

    @property
    def confirming(self) -> str | None:
        """The saved network a first tap has chosen to remove, if one is waiting."""
        for ssid in self.saved:
            if self.armed(f"network:{ssid}"):
                return ssid
        return None

    def status(self) -> tuple[str, theme.Color] | None:
        if self.busy:
            return self.busy, theme.TEXT
        if self.message:
            return self.message, theme.EMERGENCY if self.failed else theme.STATUS_OK
        return None

    def _in_background(self, what: str, work: Callable[[], None]) -> None:
        if self.busy is None:
            self.busy = what

            def run() -> None:
                try:
                    work()
                finally:
                    self.busy = None

            threading.Thread(target=run, name="wifi", daemon=True).start()

    def _refresh(self) -> None:
        """Re-read what is in range and what is saved. Runs on the worker thread."""
        self.saved = self.wifi.saved()
        try:
            self.networks = self.wifi.scan()
        except RuntimeError as exc:
            if not self.failed:
                self.message, self.failed = str(exc), True

    def _report(self, result: tuple[bool, str]) -> None:
        ok, self.message = result
        self.failed = not ok

    def scan(self) -> None:
        def work() -> None:
            self.wifi.keep_retrying()
            self._refresh()

        self.message, self.failed = "", False
        self._in_background("Looking for networks...", work)

    def join(self, network: Network, password: str | None) -> None:
        def work() -> None:
            ok, message = self.wifi.connect(network.ssid, password)
            if not ok and network.wpa3_only:
                message = f"{network.ssid} is WPA3 only, which this display's Wi-Fi cannot join."
            self._report((ok, message))
            self._refresh()

        self._in_background(f"Connecting to {network.ssid}...", work)

    def choose(self, network: Network) -> None:
        if network.active or self.busy is not None:
            return
        if network.secured and not network.saved:
            self.open_keyboard(
                f"Password for {network.ssid}",
                "",
                lambda text: self.join(network, text) if text else None,
                "This network is WPA3 only. The display's Wi-Fi may not be able to join it."
                if network.wpa3_only
                else "If the password is wrong the display goes back to a network it knows.",
                secret=True,
            )
        else:
            self.join(network, None)

    def remove(self, ssid: str) -> None:
        """First tap asks, second tap on the same network removes it."""
        if self.busy is not None or not self.confirm(f"network:{ssid}"):
            return

        def work() -> None:
            self._report(self.wifi.forget(ssid))
            self._refresh()

        self._in_background(f"Removing {ssid}...", work)

    def add(self) -> None:
        """Ask for a name, then a password, and remember the network for later."""

        def named(ssid: str) -> None:
            ssid = ssid.strip()
            if not ssid:
                return

            def with_password(password: str) -> None:
                def work() -> None:
                    self._report(self.wifi.add(ssid, password or None))
                    self._refresh()

                self._in_background(f"Saving {ssid}...", work)

            self.open_keyboard(
                f"Password for {ssid}",
                "",
                with_password,
                "Leave empty for a network with no password.",
                secret=True,
            )

        self.open_keyboard(
            "Network name", "", named, "The display will join it whenever it is in range."
        )

    def items(self) -> list[Item]:
        # What is happening, and how it went, is in the header (see status), where it cannot
        # scroll out of sight. While something is in progress the rows are dimmed and inert.
        busy = self.busy is not None
        rows = [Item(INFO, "Tap a network in range to join it. Joined networks are remembered.")]

        rows.append(Item(HEADING, "Saved networks"))
        in_use = next((n.ssid for n in self.networks if n.active), None)
        for ssid in self.saved:
            key = f"network:{ssid}"
            if self.confirming == ssid:
                # As for favorite airports: the confirmation is the body of the row, away
                # from where the finger just was, and what was Remove now backs out.
                also = " It is in use now." if ssid == in_use else ""
                rows.append(
                    Item(
                        BUTTON,
                        f"Remove {ssid}?{also} Tap here.",
                        action=lambda s=ssid: self.remove(s),
                        side_label="Keep",
                        side_action=self.disarm,
                        warning=True,
                    )
                )
            else:
                # The body of the row does nothing: only its Remove button starts a removal,
                # so it cannot be mistaken for the rows below, where a tap joins.
                rows.append(
                    Item(
                        BUTTON,
                        ssid,
                        "in use" if ssid == in_use else "",
                        side_label="Remove",
                        side_action=lambda k=key: self.confirm(k),
                        disabled=busy,
                        plain=True,
                    )
                )
        if not self.saved and not self.busy:
            rows.append(Item(INFO, "None yet."))
        rows.append(Item(BUTTON, "Add a network", "by name", action=self.add, disabled=busy))
        rows.append(
            Item(INFO, "The display joins whichever saved network is in range, and keeps trying.")
        )

        rows.append(Item(HEADING, "Networks in range"))
        for network in self.networks:
            note = "Connected" if network.active else f"{network.signal}%"
            if network.saved and not network.active:
                note += "  saved"
            elif network.wpa3_only and not network.active:
                note += "  WPA3 only"
            elif network.secured and not network.active:
                note += "  locked"
            rows.append(
                Item(
                    BUTTON,
                    network.ssid,
                    note,
                    action=lambda n=network: self.choose(n),
                    disabled=busy,
                )
            )
        if not self.networks and not self.busy:
            rows.append(Item(INFO, "No networks found."))
        rows.append(Item(BUTTON, "Scan again", action=self.scan, disabled=busy))
        return rows

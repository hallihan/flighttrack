"""An on-screen keyboard for the touchscreen: a full-screen layer that edits one line of text."""

import time
from collections.abc import Callable

import pygame

from flighttrack.ui import theme

ROWS_LOWER = ("qwertyuiop", "asdfghjkl", "zxcvbnm")
ROWS_SYMBOLS = ("1234567890", "-/:;()$&@", ".,?!'\"_")
ROWS_MORE = ("[]{}#%^*+=", "\\|~<>`", ".,?!'\"_")
KEY_HEIGHT = 58
MAX_LENGTH = 300

# Special keys are named; everything else types its own label.
SHIFT, BACKSPACE, SYMBOLS, LETTERS, MORE, SPACE, CANCEL, DONE, LEFT, RIGHT = (
    "shift",
    "backspace",
    "symbols",
    "letters",
    "more",
    "space",
    "cancel",
    "done",
    "left",
    "right",
)
_LABELS = {
    SHIFT: "Shift",
    BACKSPACE: "Del",
    SYMBOLS: "123",
    LETTERS: "abc",
    MORE: "#+=",
    SPACE: "space",
    CANCEL: "Cancel",
    DONE: "Done",
    LEFT: "",
    RIGHT: "",
}
# Shift: off, on for the next letter only, or locked on (caps lock). Each tap moves it on.
SHIFT_OFF, SHIFT_ONCE, SHIFT_LOCK = range(3)
# Keys that keep acting while held, after a pause, at this rate.
REPEATING = (BACKSPACE, LEFT, RIGHT)
REPEAT_AFTER_S = 0.45
REPEAT_EVERY_S = 0.07
# Cancel throws away what was typed, so once something has been it takes a second tap, not
# at once (a bounce) and not long after.
DISCARD_MIN_S = 0.4
DISCARD_MAX_S = 5.0


class Keyboard:
    clock = staticmethod(time.monotonic)

    def __init__(
        self,
        size: tuple[int, int],
        title: str,
        text: str,
        on_done: Callable[[str], None],
        on_cancel: Callable[[], None],
        hint: str = "",
        secret: bool = False,
    ) -> None:
        """secret: a password or a key. It is shown as dots unless Show is tapped, and a
        page that mirrors the screen elsewhere must hide it (App.typing_in_private)."""
        self.size = size
        self.title = title
        self.text = text
        self.original = text
        self.cursor = len(text)  # where typing goes: an index into text
        self.hint = hint
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.done_label = _LABELS[DONE]  # a page may rename the key to what it does there
        self.done_enabled = True  # a page may gray the key out while it has nothing to do
        self.page = "letters"
        self.shift = SHIFT_OFF
        self._pressed: str | None = None
        self._held_since: float | None = None  # when the pressed key went down, by draw's clock
        self._repeats = 0
        self._discard_armed: float | None = None
        self._first = 0  # index of the first character shown, when the text is too long
        # Where the text was last drawn, for placing the cursor by touch.
        self._field: tuple[pygame.font.Font, int, pygame.Rect] | None = None
        self.keys: list[tuple[pygame.Rect, str]] = []
        # Used when the keyboard has the whole screen (draw). Cancel is up here, well away
        # from the space bar and the other keys a typing finger is aiming at.
        self.box = pygame.Rect(16, 72, size[0] - 32, 56)
        self.cancel_rect: pygame.Rect | None = pygame.Rect(size[0] - 148, 10, 132, 50)
        self.secret = secret
        self.reveal = False  # a secret shown as typed, after a tap on Show
        self.show_rect: pygame.Rect | None = (
            pygame.Rect(size[0] - 148 - 12 - 110, 10, 110, 50) if secret else None
        )
        self._layout()

    @property
    def shown_text(self) -> str:
        """The text as it is drawn: dots for a secret not revealed (one per character, so
        the cursor and a touch on the text land in the same places)."""
        return "\u2022" * len(self.text) if self.secret and not self.reveal else self.text

    @property
    def shifted(self) -> bool:
        return self.shift != SHIFT_OFF

    def _layout(self) -> None:
        """Place the keys for the current page along the bottom of the screen."""
        width, height = self.size
        rows = {"letters": ROWS_LOWER, "symbols": ROWS_SYMBOLS, "more": ROWS_MORE}[self.page]
        key_width = width // 10
        top = height - 4 * KEY_HEIGHT
        self.keys = []

        def row_of(chars: str, y: int, left: int) -> None:
            for i, char in enumerate(chars):
                label = char.upper() if self.shifted and self.page == "letters" else char
                self.keys.append(
                    (pygame.Rect(left + i * key_width, y, key_width, KEY_HEIGHT), label)
                )

        row_of(rows[0], top, (width - len(rows[0]) * key_width) // 2)
        row_of(rows[1], top + KEY_HEIGHT, (width - len(rows[1]) * key_width) // 2)
        # Third row: a wide key at each end, characters between.
        side = (width - len(rows[2]) * key_width) // 2
        y = top + 2 * KEY_HEIGHT
        left_key = (
            SHIFT if self.page == "letters" else (MORE if self.page == "symbols" else SYMBOLS)
        )
        self.keys.append((pygame.Rect(0, y, side, KEY_HEIGHT), left_key))
        row_of(rows[2], y, side)
        self.keys.append((pygame.Rect(width - side, y, side, KEY_HEIGHT), BACKSPACE))
        # Bottom row: nothing here throws work away. Cancel is not a key (see cancel_rect).
        y = top + 3 * KEY_HEIGHT
        switch = SYMBOLS if self.page == "letters" else LETTERS
        x = 0
        for name, key_w in (
            (switch, key_width * 3 // 2),
            (LEFT, key_width),
            (SPACE, width - key_width * 5),
            (RIGHT, key_width),
            (DONE, key_width * 3 // 2),
        ):
            self.keys.append((pygame.Rect(x, y, key_w, KEY_HEIGHT), name))
            x += key_w

    # ---- editing --------------------------------------------------------------------------

    def set_text(self, text: str) -> None:
        self.text, self.cursor, self._first = text, len(text), 0

    def _insert(self, piece: str) -> None:
        if len(self.text) < MAX_LENGTH:
            self.text = self.text[: self.cursor] + piece + self.text[self.cursor :]
            self.cursor += len(piece)

    def _delete(self) -> None:
        if self.cursor > 0:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1

    def _move(self, by: int) -> None:
        self.cursor = min(max(self.cursor + by, 0), len(self.text))

    def press(self, key: str) -> None:
        self._discard_armed = None
        if key == DONE:
            if self.done_enabled:
                self.on_done(self.text)
        elif key == CANCEL:
            self.on_cancel()
        elif key == BACKSPACE:
            self._delete()
        elif key == LEFT:
            self._move(-1)
        elif key == RIGHT:
            self._move(1)
        elif key == SHIFT:
            self.shift = (self.shift + 1) % 3
        elif key in (SYMBOLS, LETTERS, MORE):
            self.page = {SYMBOLS: "symbols", LETTERS: "letters", MORE: "more"}[key]
            if self.shift == SHIFT_ONCE:
                self.shift = SHIFT_OFF
        else:
            self._insert(" " if key == SPACE else key)
            if self.shift == SHIFT_ONCE and key != SPACE:
                self.shift = SHIFT_OFF  # one letter, as on a phone; locked stays on
        self._layout()

    def cancel(self) -> bool:
        """The Cancel button. With nothing typed it leaves at once; otherwise the first tap
        asks and a second, deliberate one discards. Returns whether it left."""
        now = self.clock()
        armed = self._discard_armed
        if self.text == self.original:
            self.on_cancel()
            return True
        if armed is not None and DISCARD_MIN_S <= now - armed <= DISCARD_MAX_S:
            self._discard_armed = None
            self.on_cancel()
            return True
        if armed is None or now - armed > DISCARD_MAX_S:
            self._discard_armed = now
        return False

    @property
    def confirming_discard(self) -> bool:
        armed = self._discard_armed
        if armed is not None and self.clock() - armed > DISCARD_MAX_S:
            self._discard_armed = armed = None
        return armed is not None

    def tap_field(self, pos: tuple[int, int]) -> bool:
        """Put the cursor where the text was touched. False if the touch was elsewhere."""
        if self._field is None or not self._field[2].collidepoint(pos):
            return False
        font, left, _ = self._field
        best, best_off = self._first, abs(pos[0] - left)
        text = self.shown_text
        for index in range(self._first + 1, len(text) + 1):
            off = abs(left + font.size(text[self._first : index])[0] - pos[0])
            if off > best_off:
                break  # the widths only grow: past the nearest gap between letters
            best, best_off = index, off
        self.cursor = best
        return True

    def tick(self, now: float) -> None:
        """Called every frame: makes a held Del or arrow key repeat. Works from the time
        held, not the number of frames, so it is the same at any frame rate."""
        if self._pressed not in REPEATING:
            return
        if self._held_since is None:
            self._held_since, self._repeats = now, 0
            return
        held = now - self._held_since - REPEAT_AFTER_S
        if held < 0:
            return
        due = int(held / REPEAT_EVERY_S) + 1
        while self._repeats < due:
            self._repeats += 1
            self.press(self._pressed)

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.cancel_rect is not None and self.cancel_rect.collidepoint(event.pos):
                self.cancel()
                return
            if self.show_rect is not None and self.show_rect.collidepoint(event.pos):
                self.reveal = not self.reveal
                return
            if self.tap_field(event.pos):
                self._discard_armed = None
                return
            # Keys act on touch-down: it is what makes typing feel immediate.
            for rect, key in self.keys:
                if rect.collidepoint(event.pos):
                    self._pressed, self._held_since = key, None
                    self.press(key)
                    break
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self._pressed = None
        elif event.type == pygame.KEYDOWN:  # a real keyboard, when developing
            if event.key == pygame.K_RETURN:
                self.on_done(self.text)
            elif event.key == pygame.K_ESCAPE:
                self.on_cancel()
            elif event.key == pygame.K_BACKSPACE:
                self._delete()
            elif event.key == pygame.K_LEFT:
                self._move(-1)
            elif event.key == pygame.K_RIGHT:
                self._move(1)
            elif event.unicode and event.unicode.isprintable():
                self._insert(event.unicode)

    # ---- drawing --------------------------------------------------------------------------

    def draw_field(
        self,
        surface: pygame.Surface,
        box: pygame.Rect,
        font: pygame.font.Font,
        now: float,
        placeholder: tuple[pygame.font.Font, str] | None = None,
    ) -> None:
        """The text and its cursor inside box (whose frame the caller draws). When the text
        is too long, the part round the cursor is what shows."""
        left, room = box.left + 14, box.width - 30
        text = self.shown_text
        self._first = min(self._first, self.cursor)
        while font.size(text[self._first : self.cursor])[0] > room:
            self._first += 1
        last = len(text)
        while last > self.cursor and font.size(text[self._first : last])[0] > room:
            last -= 1
        self._field = (font, left, box)
        shown = text[self._first : last]
        if shown:
            rendered = theme.text(font, shown, theme.TEXT)
            surface.blit(rendered, rendered.get_rect(midleft=(left, box.centery)))
        elif placeholder is not None and not text:
            hint = theme.text(placeholder[0], placeholder[1], theme.TEXT_DIM)
            surface.blit(hint, hint.get_rect(midleft=(left + 8, box.centery)))
        if int(now * 2) % 2 == 0 or self._pressed is not None:
            x = left + font.size(text[self._first : self.cursor])[0] + 1
            pygame.draw.line(surface, theme.ACCENT, (x, box.top + 11), (x, box.bottom - 11), 2)

    def draw(self, surface: pygame.Surface, fonts: theme.Fonts, now: float) -> None:
        surface.fill(theme.BACKGROUND)
        surface.blit(theme.text(fonts.title, self.title, theme.ACCENT), (20, 16))
        if self.hint:
            surface.blit(theme.text(fonts.small, self.hint, theme.TEXT_DIM), (20, 46))
        if self.cancel_rect is not None:
            asking = self.confirming_discard
            fill = theme.CONFIRM_FILL if asking else theme.BUTTON_FILL
            pygame.draw.rect(surface, fill, self.cancel_rect, border_radius=8)
            pygame.draw.rect(surface, theme.EDGE, self.cancel_rect, width=1, border_radius=8)
            label = theme.text(fonts.title, "Discard?" if asking else "Cancel", theme.TEXT)
            surface.blit(label, label.get_rect(center=self.cancel_rect.center))
        if self.show_rect is not None:
            fill = theme.TOGGLE_ON if self.reveal else theme.BUTTON_FILL
            pygame.draw.rect(surface, fill, self.show_rect, border_radius=8)
            pygame.draw.rect(surface, theme.EDGE, self.show_rect, width=1, border_radius=8)
            color = theme.ON_ACCENT if self.reveal else theme.TEXT
            label = theme.text(fonts.title, "Hide" if self.reveal else "Show", color)
            surface.blit(label, label.get_rect(center=self.show_rect.center))

        pygame.draw.rect(surface, theme.PANEL_FILL, self.box, border_radius=8)
        pygame.draw.rect(surface, theme.ACCENT, self.box, width=2, border_radius=8)
        self.draw_field(surface, self.box, fonts.large, now)
        self.draw_keys(surface, fonts, now)

    def draw_keys(self, surface: pygame.Surface, fonts: theme.Fonts, now: float) -> None:
        """Just the keys, for a page that lays out the rest of the screen itself."""
        self.tick(now)
        for rect, key in self.keys:
            face = rect.inflate(-6, -6)
            special = key in _LABELS
            if key == self._pressed or (key == SHIFT and self.shifted):
                fill = theme.BUTTON_PRESSED
            elif key == DONE:
                fill = theme.TOGGLE_ON if self.done_enabled else theme.BUTTON_DISABLED
            else:
                fill = theme.BUTTON_DISABLED if special else theme.BUTTON_FILL
            pygame.draw.rect(surface, fill, face, border_radius=7)
            pygame.draw.rect(surface, theme.EDGE, face, width=1, border_radius=7)
            if key in (LEFT, RIGHT):
                # Arrows are drawn, so they look the same whatever the font has.
                way = -1 if key == LEFT else 1
                cx, cy = face.center
                pygame.draw.polygon(
                    surface,
                    theme.TEXT,
                    [(cx + 7 * way, cy), (cx - 5 * way, cy - 9), (cx - 5 * way, cy + 9)],
                )
                continue
            label = self.done_label if key == DONE else _LABELS.get(key, key)
            if key == SHIFT and self.shift == SHIFT_LOCK:
                label = "CAPS"
            font = fonts.body if special else fonts.large
            dim = key == DONE and not self.done_enabled
            lit = key == DONE and self.done_enabled and fill == theme.TOGGLE_ON
            color = theme.TEXT_DIM if dim else theme.ON_ACCENT if lit else theme.TEXT
            text = theme.text(font, label, color)
            surface.blit(text, text.get_rect(center=face.center))

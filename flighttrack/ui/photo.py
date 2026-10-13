"""A full-screen look at an aircraft's picture, opened by tapping its thumbnail."""

import io
from collections.abc import Callable

import pygame

from flighttrack import enrich
from flighttrack.ui import theme
from flighttrack.ui.widgets import glass_face

CLOSE_PX = 60


class PhotoScreen:
    def __init__(
        self,
        size: tuple[int, int],
        close: Callable[[], None],
        title: str,
        photos: tuple[enrich.Photo, ...],
        full: Callable[[int], bytes | str],
        index: int = 0,
    ) -> None:
        """photos are what the pane showed as thumbnails; full(n) returns the larger version
        of the n-th as bytes once it has been fetched (see Enricher.full_photo). Until then,
        and if there is no larger one, the thumbnail is shown enlarged."""
        self.size = size
        self._close = close
        self.title = title
        self.photos = photos
        self._full = full
        self.index = index
        self.close_button = pygame.Rect(size[0] - CLOSE_PX - 10, 10, CLOSE_PX, CLOSE_PX)
        self._pressed = False
        # What is on screen for each picture: (the bytes it was made from, the image).
        self._shown: dict[int, tuple[bytes, pygame.Surface | None]] = {}

    def close(self) -> None:
        self._close()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.close()
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._pressed = True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self._pressed:
            self._pressed = False
            if self.close_button.collidepoint(event.pos):
                self.close()
            elif len(self.photos) > 1:
                self.index = (self.index + 1) % len(self.photos)  # a tap on the picture: the other

    def _fitted(self, data: bytes) -> pygame.Surface | None:
        """A picture decoded and scaled to fit the screen above the credit line."""
        try:
            loaded = pygame.image.load(io.BytesIO(data)).convert()
            room_w, room_h = self.size[0], self.size[1] - 34
            scale = min(room_w / loaded.get_width(), room_h / loaded.get_height())
            size = (
                max(round(loaded.get_width() * scale), 1),
                max(round(loaded.get_height() * scale), 1),
            )
            return pygame.transform.smoothscale(loaded, size)
        except (pygame.error, ValueError, ZeroDivisionError):
            return None

    def _image(self, index: int) -> tuple[pygame.Surface | None, bool]:
        """The picture to show, and whether a larger one is still on its way."""
        larger = self._full(index)
        small = self.photos[index].data
        data = larger if isinstance(larger, bytes) else small
        shown = self._shown.get(index)
        if shown is None or shown[0] is not data:
            image = self._fitted(data)
            if image is None and data is not small:
                image = self._fitted(small)  # the larger one would not open
            self._shown[index] = (data, image)
        return self._shown[index][1], larger == enrich.LOADING

    def draw(self, surface: pygame.Surface, fonts: theme.Fonts, now: float) -> None:
        width, height = self.size
        surface.fill((0, 0, 0))
        image, coming = self._image(self.index)
        if image is not None:
            surface.blit(image, image.get_rect(center=(width // 2, (height - 34) // 2)))
        else:
            sorry = theme.text(fonts.body, "This picture could not be shown.", theme.TEXT_DIM)
            surface.blit(sorry, sorry.get_rect(center=(width // 2, height // 2)))

        # Along the bottom, on its own: the credit in full.
        photo = self.photos[self.index]
        credit = photo.attribution
        while len(credit) > 8 and fonts.body.size(credit)[0] > width - 24:
            credit = credit[:-4].rstrip() + "..."
        words = theme.text(fonts.body, credit, theme.TEXT)
        surface.blit(words, words.get_rect(midleft=(12, height - 17)))

        # Over the picture's top-left corner: which aircraft, and what else can be done here.
        surface.blit(theme.shadowed_text(fonts.title, self.title, theme.MAP_TEXT), (12, 10))
        notes = []
        if len(self.photos) > 1:
            notes.append(f"{self.index + 1} of {len(self.photos)}: tap the picture for the other")
        if coming:
            notes.append("Loading a larger picture...")
        for line, note in enumerate(notes):
            surface.blit(
                theme.shadowed_text(fonts.small, note, theme.MAP_TEXT), (12, 38 + 18 * line)
            )

        # The way out, over the picture where it cannot be missed.
        rect = self.close_button
        surface.blit(glass_face(rect.size, "down" if self._pressed else "up"), rect.topleft)
        cx, cy = rect.center
        for dx in (-11, 11):
            pygame.draw.line(surface, (6, 10, 16), (cx - dx, cy - 11), (cx + dx, cy + 11), width=7)
        for dx in (-11, 11):
            pygame.draw.line(surface, theme.TEXT, (cx - dx, cy - 11), (cx + dx, cy + 11), width=4)

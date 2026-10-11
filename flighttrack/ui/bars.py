"""The status bars along the top and bottom of the screen."""

import pygame

from flighttrack.tiles.weather import LEGEND_BANDS
from flighttrack.ui import brand, theme
from flighttrack.ui import format as fmt

PADDING = 12
TITLE = "FlightTrack"
ICON_PX = 28  # the mark before the name
ICON_GAP = 6


def title_right(fonts: theme.Fonts) -> int:
    """Where the mark and the name end, from the left edge of the bar."""
    return PADDING + ICON_PX + ICON_GAP + fonts.title.size(TITLE)[0]


def draw_top(
    surface: pygame.Surface,
    rect: pygame.Rect,
    fonts: theme.Fonts,
    controls_right: int,
    count: int,
    data_age_s: float | None,
    demo: bool,
) -> None:
    """controls_right is where the row of buttons and pills after the title ends; the traffic
    summary has the space to its right, and says less when that is short."""
    pygame.draw.rect(surface, theme.BAR_FILL, rect)
    pygame.draw.line(surface, theme.EDGE, rect.bottomleft, rect.bottomright)
    mark = brand.icon(ICON_PX)
    surface.blit(mark, mark.get_rect(midleft=(rect.x + PADDING, rect.centery)))
    title = theme.text(fonts.title, TITLE, theme.ACCENT)
    surface.blit(
        title, title.get_rect(midleft=(rect.x + PADDING + ICON_PX + ICON_GAP, rect.centery))
    )

    for summary in top_summaries(count, data_age_s, demo):
        if fonts.body.size(summary)[0] <= rect.right - PADDING - controls_right - 6:
            break
    right = theme.text(fonts.body, summary, theme.STATUS_STALE if demo else theme.TEXT)
    surface.blit(right, right.get_rect(midright=(rect.right - PADDING, rect.centery)))


def top_summaries(count: int, data_age_s: float | None, demo: bool) -> list[str]:
    """Ways of putting the traffic summary, fullest first; the last is the shortest."""
    prefix = "DEMO  " if demo else ""
    ways = [f"{prefix}{count} AC"]
    if data_age_s is not None:
        ways.insert(0, f"{ways[0]}  {fmt.age(data_age_s)}")
    if demo:
        ways.append(f"DEMO {count}")
    return ways


def draw_bottom(
    surface: pygame.Surface,
    rect: pygame.Rect,
    fonts: theme.Fonts,
    left: str,
    center: str,
    status: theme.Color,
    right: str,
    note: str = "",
    note_color: theme.Color | None = None,
    right_edge: int | None = None,
) -> None:
    """note is a second, quieter item after the left one (the Wi-Fi network). It is cut
    short if it would run into the center text. right_edge is where the right-hand text
    must end, when something else (the diagnostics pill) has the end of the bar."""
    pygame.draw.rect(surface, theme.BAR_FILL, rect)
    pygame.draw.line(surface, theme.EDGE, rect.topleft, rect.topright)
    rendered = theme.text(fonts.body, left, theme.TEXT)
    placed = rendered.get_rect(midleft=(rect.x + PADDING, rect.centery))
    surface.blit(rendered, placed)
    rendered = theme.text(fonts.small, center, theme.TEXT_DIM)
    middle = rendered.get_rect(center=rect.center)
    surface.blit(rendered, middle)
    if note:
        room = middle.left - 12 - (placed.right + 14)
        shown = note
        while len(shown) > 4 and fonts.small.size(shown)[0] > room:
            shown = shown[:-4].rstrip() + "..."
        if fonts.small.size(shown)[0] <= room:
            text = theme.text(fonts.small, shown, note_color or theme.TEXT_DIM)
            surface.blit(text, text.get_rect(midleft=(placed.right + 14, rect.centery)))
    pygame.draw.circle(surface, status, (middle.right + 14, rect.centery), 5)
    rendered = theme.text(fonts.small, right, theme.TEXT_DIM)
    end = rect.right - PADDING if right_edge is None else right_edge
    surface.blit(rendered, rendered.get_rect(midright=(end, rect.centery)))


def draw_weather_legend(
    surface: pygame.Surface,
    area: pygame.Rect,
    fonts: theme.Fonts,
    colors: tuple[tuple[int, theme.Color], ...],
) -> None:
    """A strip explaining the radar colors, drawn inside area (the bottom of the map).
    colors are (dBZ, color) pairs for whichever radar source is on screen."""
    backing = pygame.Surface(area.size, pygame.SRCALPHA)
    backing.fill((6, 12, 20, 205))
    surface.blit(backing, area.topleft)
    pygame.draw.rect(surface, theme.EDGE, area, width=1, border_radius=4)

    bar = pygame.Rect(area.left + 8, area.top + 6, area.width - 16, 10)
    step = bar.width / len(colors)
    for i, (_, color) in enumerate(colors):
        cell = pygame.Rect(bar.left + round(i * step), bar.top, round(step) + 1, bar.height)
        pygame.draw.rect(surface, color, cell.clip(bar))
    pygame.draw.rect(surface, theme.EDGE, bar, width=1)
    first_dbz = colors[0][0]
    span = colors[-1][0] + 5 - first_dbz
    taken_to = area.left
    for start_dbz, end_dbz, label in LEGEND_BANDS:
        middle = bar.left + ((start_dbz + end_dbz) / 2 - first_dbz) / span * bar.width
        # The strip is narrower when GO, the cog and HOME are all beside it. A word that
        # would run into the one before is cut short, not printed over it.
        for wording in (label, label[:3] + "."):
            text = theme.text(fonts.small, wording, theme.TEXT)
            placed = text.get_rect(midtop=(middle, bar.bottom + 3))
            if placed.left >= taken_to + 4:
                surface.blit(text, placed)
                taken_to = placed.right
                break


def draw_reach_legend(surface: pygame.Surface, area: pygame.Rect, fonts: theme.Fonts) -> None:
    """A strip explaining the coverage tint: how low aircraft are heard, in feet above the
    ground (AGL), from the ground up. The title is two short lines at the left and each
    band's label sits in its own color, so it fits the narrow strip between the buttons."""
    from flighttrack.ui.overlays import REACH_BANDS

    backing = pygame.Surface(area.size, pygame.SRCALPHA)
    backing.fill((6, 12, 20, 205))
    surface.blit(backing, area.topleft)
    pygame.draw.rect(surface, theme.EDGE, area, width=1, border_radius=4)
    top = theme.text(fonts.small, "Heard to", theme.TEXT)
    bottom = theme.text(fonts.small, "ft AGL", theme.TEXT)
    surface.blit(top, top.get_rect(bottomleft=(area.left + 6, area.centery)))
    surface.blit(bottom, bottom.get_rect(topleft=(area.left + 6, area.centery)))
    left = area.left + 6 + max(top.get_width(), bottom.get_width()) + 6
    step = (area.right - 6 - left) / len(REACH_BANDS)
    for i, (_, color, label) in enumerate(REACH_BANDS):
        cell = pygame.Rect(round(left + i * step), area.top + 7, round(step) - 2, area.height - 14)
        pygame.draw.rect(surface, color, cell, border_radius=3)
        text = theme.text(fonts.small, label, theme.PANEL_FILL)
        surface.blit(text, text.get_rect(center=cell.center))

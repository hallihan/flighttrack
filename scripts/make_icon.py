"""Write the FlightTrack mark as the files the settings web page serves.

Run from the repository root after changing flighttrack/ui/brand.py:

    .venv/bin/python scripts/make_icon.py
"""

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from flighttrack.ui import brand

OUT = Path(__file__).resolve().parent.parent / "flighttrack" / "portal" / "static"


def main() -> None:
    pygame.display.init()
    pygame.display.set_mode((1, 1))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon.svg").write_text(brand.svg())
    for size in (32, 180):  # a browser tab, and a phone's home screen
        pygame.image.save(brand.icon(size, background=True), OUT / f"icon-{size}.png")
    print("written to", OUT)


if __name__ == "__main__":
    main()

"""Locating the display backlight control in sysfs."""

import os
from dataclasses import dataclass
from pathlib import Path

SYSFS_BACKLIGHT = Path("/sys/class/backlight")


@dataclass(frozen=True, slots=True)
class Backlight:
    name: str
    brightness_path: Path
    writable: bool

    def _maximum(self) -> int:
        return int((self.brightness_path.parent / "max_brightness").read_text())

    def set_fraction(self, fraction: float) -> float | None:
        """Set the brightness as a fraction of the panel's maximum. Returns the fraction the
        panel was actually set to (it works in whole steps), or None if it could not be."""
        try:
            maximum = self._maximum()
            level = max(1, round(maximum * min(max(fraction, 0.0), 1.0)))
            self.brightness_path.write_text(f"{level}\n")
        except (OSError, ValueError):
            return None
        return level / maximum

    def read_fraction(self) -> float | None:
        """The panel's brightness now, as a fraction of its maximum, whoever set it."""
        try:
            return int(self.brightness_path.read_text()) / self._maximum()
        except (OSError, ValueError, ZeroDivisionError):
            return None


def find_backlight(root: Path = SYSFS_BACKLIGHT) -> Backlight | None:
    """The first backlight device present, or None (e.g. on a development machine)."""
    try:
        devices = sorted(p for p in root.iterdir() if (p / "brightness").exists())
    except OSError:
        return None
    if not devices:
        return None
    path = devices[0] / "brightness"
    return Backlight(devices[0].name, path, os.access(path, os.W_OK))

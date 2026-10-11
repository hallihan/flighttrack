"""What is common to everything this display keeps on its SD card to save downloading it
again: how long it is good for, and how much room all of it together may take."""

import shutil
from pathlib import Path

# Maps and aircraft photos are used for this long, then fetched afresh the next time they are
# wanted (and the old one used if that fails).
MAX_AGE_S = 30 * 86400.0

# All the caches together may take the lesser of this, and half of the room on the card that
# is either free or already theirs.
MAX_CACHE_BYTES = 32 * 10**9
SHARE_OF_FREE = 0.5


def budget(path: Path, used: int) -> int:
    """How many bytes the caches may take in all, given how many they take now. path is
    anywhere on the card."""
    try:
        free = shutil.disk_usage(path).free
    except OSError:
        return 512 * 10**6  # cannot tell: something modest
    return int(min(MAX_CACHE_BYTES, (free + used) * SHARE_OF_FREE))


def dir_bytes(path: Path, pattern: str = "*") -> int:
    """The size of the files in a folder and below. Walks it: not for the main thread."""
    total = 0
    try:
        for found in path.rglob(pattern):
            if found.is_file():
                total += found.stat().st_size
    except OSError:
        pass
    return total

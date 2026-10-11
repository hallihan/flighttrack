"""Fetching map tiles on worker threads, with a cache in memory and a size-capped one on disk.

The UI asks for tiles and carries on; finished tiles are collected with drain(). Tiles are
handed over as raw bytes: decoding belongs to the main thread, which owns pygame.

This is the only thing the machine does, on an SD card, so memory is used freely and the disk
sparingly. A tile asked for again comes from memory. Map imagery is written to disk once, when
first fetched, so it survives a restart, and reading it back does not touch the disk's
metadata more than once a day. Weather tiles, obsolete within minutes, stay in memory and
reach the disk only if they are pushed out of memory while still current.
"""

import logging
import os
import queue
import shutil
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

import requests

from flighttrack import cache, faa, net
from flighttrack.cache import MAX_AGE_S
from flighttrack.feeds.http import USER_AGENT
from flighttrack.tiles import TileKey, TileLayer

log = logging.getLogger(__name__)

# Room left within the caches' budget for the small ones that are not tiles.
RESERVED_FOR_OTHERS = 64 * 10**6
SHOWN, QUIET = 0, 5  # priorities: what is on screen now, before what might be wanted next
RETRY_MISSING_AFTER_S = 30.0  # after a failure to fetch: the network may be back
# After the server says a tile does not exist (a weather frame not yet published, or outside
# a layer's coverage), asking again soon is pointless and impolite.
RETRY_ABSENT_AFTER_S = 600.0
PRUNE_EVERY_WRITES = 200
# Tiles kept in memory: this much at most, and less on a machine with little to spare.
MAX_MEMORY_BYTES = 128 * 1024 * 1024
MIN_MEMORY_BYTES = 16 * 1024 * 1024
MEMORY_SHARE = 0.125  # of what the system reports as available at start-up
# A cached file is marked as used (for pruning the least recently used) at most this often.
TOUCH_EVERY_S = 24 * 3600.0
_IMAGE_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


def memory_budget(meminfo: Path = Path("/proc/meminfo")) -> int:
    """How many bytes of tiles to keep in memory: a share of what is free, within limits."""
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
                return int(min(max(available * MEMORY_SHARE, MIN_MEMORY_BYTES), MAX_MEMORY_BYTES))
    except (OSError, ValueError, IndexError):
        pass
    return MAX_MEMORY_BYTES // 2  # not Linux, or unreadable: a middling amount


def _http_fetch() -> Callable[[str], bytes | None]:
    """A fetch function with one keep-alive session per worker thread."""
    local = threading.local()

    def fetch(url: str) -> bytes | None:
        session = getattr(local, "session", None)
        if session is None:
            session = local.session = requests.Session()
            session.headers["User-Agent"] = USER_AGENT
        response = net.get(session, url, net.TILE_SPACING_S, timeout=(4.0, 10.0))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    return fetch


class TileStore:
    def __init__(
        self,
        cache_dir: Path,
        *,
        workers: int = 3,
        max_bytes: int | None = None,
        fetch: Callable[[str], bytes | None] | None = None,
        memory_bytes: int | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        # The most the tiles on disk may take. None: whatever the caches' budget allows
        # (see cache.budget), worked out afresh each time, since it follows the free space.
        self._max_bytes = max_bytes
        # Bytes on disk by layer, kept up to date as tiles are written and removed. None
        # until the cache has been walked once, at start-up.
        self._on_disk: dict[str, int] | None = None
        self._memory_max = memory_budget() if memory_bytes is None else memory_bytes
        # Newest last. With each tile, the file to write it to if it is pushed out of memory
        # (weather that has never been on disk), or None if it is there already.
        # Last, when it was fetched or read (wall time), so that one kept for weeks in a
        # display that is never restarted still goes stale.
        self._memory: OrderedDict[TileKey, tuple[bytes, Path | None, float]] = OrderedDict()
        self._memory_used = 0
        self.disk_reads = self.disk_writes = self.memory_hits = self.fetches = 0  # diagnostics
        self._fetch = fetch or _http_fetch()
        # Jobs in order of priority, then of asking.
        self._jobs: queue.PriorityQueue[tuple[int, int, Callable[[], None] | None]] = (
            queue.PriorityQueue()
        )
        self._asked = 0
        self._quiet: set[TileKey] = set()  # on their way, but nobody is waiting to draw them
        # Out-of-date tiles still in use because a fresh one could not be had: not to be
        # asked about again before this (monotonic) time.
        self._kept_old: dict[TileKey, float] = {}
        self._ready: queue.SimpleQueue[tuple[TileKey, bytes]] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._pending: set[TileKey] = set()
        # Keys that could not be had: when that was found out, and how long to leave them.
        self._missing: dict[TileKey, tuple[float, float]] = {}
        self._writes = 0
        self._threads = [
            threading.Thread(target=self._work, name=f"tiles-{i}", daemon=True)
            for i in range(workers)
        ]

    def _put(self, priority: int, job: Callable[[], None] | None) -> None:
        with self._lock:
            self._asked += 1
            order = self._asked
        self._jobs.put((priority, order, job))

    def start(self) -> None:
        for thread in self._threads:
            thread.start()
        self._put(SHOWN, self._measure)
        self._put(QUIET, self.prune)

    def stop(self) -> None:
        for _ in self._threads:
            self._put(-1, None)

    def path(self, layer: TileLayer, z: int, x: int, y: int) -> Path:
        return self._cache_dir / layer.name / str(z) / str(x) / f"{y}.{layer.extension}"

    def _left_alone(self, key: TileKey) -> bool:
        """Whether a tile recently could not be had, and is not to be asked for again yet.
        Caller holds the lock."""
        failed = self._missing.get(key)
        return failed is not None and time.monotonic() - failed[0] < failed[1]

    def request(self, layer: TileLayer, z: int, x: int, y: int) -> None:
        """Ask for a tile to draw. Does nothing if it is already on its way or recently
        failed. It turns up in drain()."""
        key: TileKey = (layer.name, z, x, y)
        with self._lock:
            if self._left_alone(key):
                return
            if key in self._pending and key not in self._quiet:
                return
            # Wanted now, even if it was already being fetched quietly: jump the queue.
            self._quiet.discard(key)
            self._pending.add(key)
        self._put(SHOWN, lambda: self._load(key, layer, z, x, y))

    def prefetch(self, layer: TileLayer, z: int, x: int, y: int) -> bool:
        """Get a tile into memory in case it is wanted soon: after everything that is wanted
        now, and without handing it over for drawing. Returns whether it was asked for."""
        key: TileKey = (layer.name, z, x, y)
        with self._lock:
            if key in self._pending or key in self._memory or self._left_alone(key):
                return False
            self._pending.add(key)
            self._quiet.add(key)
        self._put(QUIET, lambda: self._load(key, layer, z, x, y))
        return True

    @property
    def busy(self) -> int:
        """How many tiles are on their way."""
        return len(self._pending)

    def clear(self) -> None:
        """Forget every tile, in memory and on disk. They are fetched again as needed."""
        with self._lock:
            self._memory.clear()
            self._memory_used = 0
            self._missing.clear()
            self._kept_old.clear()
            if self._on_disk is not None:
                self._on_disk = {}

        def job() -> None:
            try:
                for path in self._cache_dir.iterdir():
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
            except OSError:
                pass

        self._put(SHOWN, job)

    def disk_usage(self) -> dict[str, int] | None:
        """Bytes of tiles on disk, by layer. None until the cache has been measured (once,
        at start-up, by a worker)."""
        with self._lock:
            return None if self._on_disk is None else dict(self._on_disk)

    def disk_bytes(self) -> int:
        usage = self.disk_usage()
        return sum(usage.values()) if usage is not None else 0

    def limit(self) -> int:
        """The most the tiles on disk may take, as things stand."""
        if self._max_bytes is not None:
            return self._max_bytes
        return max(cache.budget(self._cache_dir.parent, self.disk_bytes()) - RESERVED_FOR_OTHERS, 0)

    def _measure(self) -> None:
        """Walk the cache once to learn what is there."""
        found: dict[str, int] = {}
        try:
            for layer_dir in self._cache_dir.iterdir():
                if layer_dir.is_dir():
                    found[layer_dir.name] = cache.dir_bytes(layer_dir)
        except OSError:
            pass
        with self._lock:
            self._on_disk = found

    def _count(self, layer_name: str, change: int) -> None:
        """Caller holds the lock."""
        if self._on_disk is not None:
            now = self._on_disk.get(layer_name, 0) + change
            if now > 0:
                self._on_disk[layer_name] = now
            else:
                self._on_disk.pop(layer_name, None)

    def drain(self) -> list[tuple[TileKey, bytes]]:
        """Tiles that have arrived since the last call."""
        arrived = []
        while True:
            try:
                arrived.append(self._ready.get_nowait())
            except queue.Empty:
                return arrived

    def forget_layers(self, prefix: str, keep: str) -> None:
        """Delete cached layers whose name starts with prefix, except one (old radar frames)."""

        def job() -> None:
            with self._lock:
                for key in [k for k in self._memory if k[0].startswith(prefix) and k[0] != keep]:
                    self._memory_used -= len(self._memory.pop(key)[0])
            try:
                stale = [
                    p
                    for p in self._cache_dir.iterdir()
                    if p.is_dir() and p.name.startswith(prefix) and p.name != keep
                ]
            except OSError:
                return
            for directory in stale:
                shutil.rmtree(directory, ignore_errors=True)
                with self._lock:
                    if self._on_disk is not None:
                        self._on_disk.pop(directory.name, None)

        self._put(SHOWN, job)

    def _work(self) -> None:
        while True:
            _, _, job = self._jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception:  # a bad tile or a full disk must not kill the worker
                log.exception("tile job failed")

    def _remember(
        self, key: TileKey, data: bytes, spill_to: Path | None, at: float | None = None
    ) -> None:
        """Keep a tile in memory, pushing out the least recently used if there is no room.
        Weather pushed out while it has no copy on disk is written there on the way."""
        spilled: list[tuple[Path, bytes]] = []
        with self._lock:
            if key in self._memory:
                self._memory_used -= len(self._memory.pop(key)[0])
            if len(data) > self._memory_max:
                if spill_to is not None:
                    spilled.append((spill_to, data))
            else:
                self._memory[key] = (data, spill_to, time.time() if at is None else at)
                self._memory_used += len(data)
            while self._memory_used > self._memory_max:
                _, (old, path, _) = self._memory.popitem(last=False)
                self._memory_used -= len(old)
                if path is not None:
                    spilled.append((path, old))
        for path, old in spilled:
            self._write(path, old)

    @property
    def memory_used(self) -> tuple[int, int]:
        """Bytes of tiles held in memory, and the most that may be."""
        return self._memory_used, self._memory_max

    def _read_disk(self, path: Path) -> tuple[bytes, float] | None:
        """A tile from the disk cache, and when it was fetched."""
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError:
            return None
        self.disk_reads += 1
        try:
            # Marked as used, for pruning, but not on every read: each one is a write to
            # the card. Once a day is as fine as pruning needs. Only the access time is
            # moved: the modification time stays as the day it was fetched.
            if time.time() - stat.st_atime > TOUCH_EVERY_S:
                os.utime(path, (time.time(), stat.st_mtime))
        except OSError:
            pass
        return data, stat.st_mtime

    @staticmethod
    def _stale(layer: TileLayer, fetched_at: float, now: float) -> bool:
        """Whether a tile fetched then should be fetched afresh now."""
        if layer.short_lived:
            return False  # weather is replaced by a new layer, not refreshed
        if layer.cycle_s is not None:
            return faa.out_of_date(fetched_at, now, layer.cycle_s)
        return now - fetched_at > MAX_AGE_S

    def _load(self, key: TileKey, layer: TileLayer, z: int, x: int, y: int) -> None:
        path = self.path(layer, z, x, y)
        leave_for = RETRY_ABSENT_AFTER_S
        now = time.time()
        old: bytes | None = None  # one we have that is past its time: better than nothing
        with self._lock:
            held = self._memory.get(key)
            if held is not None:
                self._memory.move_to_end(key)
                self.memory_hits += 1
            keep_until = self._kept_old.get(key)
        making_do = keep_until is not None and time.monotonic() < keep_until
        data: bytes | None = None
        if held is not None:
            if making_do or not self._stale(layer, held[2], now):
                data = held[0]
            else:
                old = held[0]
        if data is None and old is None:
            read = self._read_disk(path)
            if read is not None:
                if making_do or not self._stale(layer, read[1], now):
                    data = read[0]
                    self._remember(key, data, None, read[1])
                else:
                    old = read[0]
        if data is None:
            try:
                self.fetches += 1
                data = self._fetch(layer.url(z, x, y))
            except requests.RequestException as exc:
                log.warning("tile %s/%d/%d/%d failed: %s", *key, exc)
                leave_for = RETRY_MISSING_AFTER_S
            if data is not None and not data.startswith(_IMAGE_SIGNATURES):
                log.warning("tile %s/%d/%d/%d is not an image; ignoring it", *key)
                data = None
            if data is not None:
                if layer.short_lived:
                    self._remember(key, data, path)
                else:
                    self._write(path, data)
                    self._remember(key, data, None)
                with self._lock:
                    self._kept_old.pop(key, None)
            elif old is not None:
                # No fresh one to be had: the old one serves, and is asked about again
                # after the usual wait, not on every frame.
                data = old
                if held is None:
                    self._remember(key, old, None, self._fetched_at(path, now))
                with self._lock:
                    self._kept_old[key] = time.monotonic() + leave_for
        with self._lock:
            self._pending.discard(key)
            quiet = key in self._quiet
            self._quiet.discard(key)
            if data is None:
                self._missing[key] = (time.monotonic(), leave_for)
            else:
                self._missing.pop(key, None)
        if data is not None and not quiet:
            self._ready.put((key, data))

    @staticmethod
    def _fetched_at(path: Path, default: float) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return default

    def _write(self, path: Path, data: bytes) -> None:
        try:
            before = path.stat().st_size if path.exists() else 0  # a refreshed tile replaces one
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not cache tile at %s: %s", path, exc)
            return
        layer_name = path.relative_to(self._cache_dir).parts[0]
        with self._lock:
            self.disk_writes += 1
            self._writes += 1
            self._count(layer_name, len(data) - before)
            due = self._writes % PRUNE_EVERY_WRITES == 0
        if due:
            self.prune()

    def prune(self) -> None:
        """Delete the least recently used tiles until the cache is under its limit. The
        cache is only walked when the running total says it is over."""
        limit = self.limit()
        usage = self.disk_usage()
        if usage is not None and sum(usage.values()) <= limit:
            return
        files = []
        try:
            for path in self._cache_dir.rglob("*"):
                if path.is_file():
                    stat = path.stat()
                    # Last used: read (the access time, which we set) or written.
                    files.append((max(stat.st_atime, stat.st_mtime), stat.st_size, path))
        except OSError:
            return
        total = sum(size for _, size, _ in files)
        if total <= limit:
            return
        target = limit * 0.9
        for _, size, path in sorted(files, key=lambda item: item[0]):
            if total <= target:
                break
            try:
                path.unlink()
                total -= size
                with self._lock:
                    self._count(path.relative_to(self._cache_dir).parts[0], -size)
            except (OSError, ValueError):
                pass
        log.info("pruned the tile cache to %.0f MB", total / 1e6)

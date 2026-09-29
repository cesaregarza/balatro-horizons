"""Bounded verified journal snapshots for retrospective reads."""

import json
import zlib
from collections import OrderedDict
from threading import RLock

from balatro_horizons.review.detail_projection import ordinary_events
from balatro_horizons.storage.journal import locked


def file_identity(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class VerifiedReadCache:
    """Retain compressed snapshots only while the source's full identity holds."""

    def __init__(self, capacity=4, max_file_bytes=96 * 1024 * 1024,
                 max_cache_bytes=32 * 1024 * 1024):
        if min(capacity, max_file_bytes, max_cache_bytes) < 1:
            raise ValueError("cache bounds must be positive")
        self.capacity = capacity
        self.max_file_bytes = max_file_bytes
        self.max_cache_bytes = max_cache_bytes
        self._entries = OrderedDict()
        self._cached_bytes = 0
        self._lock = RLock()

    def events(self, store, episode_id, *, compact=False):
        directory = store.episode_path(episode_id)
        path = directory / "events.jsonl"
        with self._lock, locked(directory / ".writer.lock"):
            before = file_identity(path)
            if before is None:
                return []
            if before[2] > self.max_file_bytes:
                records = store.events(episode_id)
                return ordinary_events(records) if compact else records
            key = (episode_id, before)
            snapshot = self._entries.get(key)
            if snapshot is not None:
                self._entries.move_to_end(key)
                return _decode(snapshot[int(compact)])

            # The existing reader verifies every hash link. Keep only a compressed
            # byte snapshot afterward, avoiding a second retained object graph.
            rows = store.events(episode_id)
            raw = path.read_bytes()
            after = file_identity(path)
            if after != before:
                return ordinary_events(rows) if compact else rows
            ordinary = ordinary_events(rows)
            projected = b"\n".join(json.dumps(row, separators=(",", ":")).encode()
                                   for row in ordinary)
            snapshot = (zlib.compress(raw, level=1), zlib.compress(projected, level=1))
            size = sum(map(len, snapshot))
            if size <= self.max_cache_bytes:
                self._entries[key] = snapshot
                self._cached_bytes += size
                self._evict(episode_id, key)
            return ordinary if compact else rows

    def _evict(self, episode_id, current):
        for key in tuple(self._entries):
            if key[0] == episode_id and key != current:
                self._remove(key)
        while (len(self._entries) > self.capacity
               or self._cached_bytes > self.max_cache_bytes):
            self._remove(next(iter(self._entries)))

    def _remove(self, key):
        self._cached_bytes -= sum(map(len, self._entries.pop(key)))


def _decode(compressed):
    return [json.loads(line) for line in zlib.decompress(compressed).splitlines()]

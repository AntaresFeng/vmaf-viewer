from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from threading import RLock

from .models import FileRecord, ParsedVmaf
from .parser import parse_vmaf_file

MIB = 1024 * 1024
DEFAULT_CACHE_MAX_BYTES = 256 * MIB
DEFAULT_CACHE_MIN_ENTRIES = 6

_Fingerprint = tuple[int, float]
_InflightKey = tuple[int, str]


@dataclass(frozen=True)
class _CacheEntry:
    fingerprint: _Fingerprint
    parsed: ParsedVmaf
    estimated_bytes: int


@dataclass
class _PendingLoad:
    fingerprint: _Fingerprint
    future: Future[ParsedVmaf]
    cacheable: bool = True


def estimate_parsed_vmaf_bytes(parsed: ParsedVmaf) -> int:
    """Estimate the retained Python memory for a parsed VMAF log.

    The numeric columns dominate the retained model. Each list element is
    budgeted as a 24-byte Python number plus its 8-byte list pointer. Metric
    names and the small fixed containers are conservatively covered separately.
    """

    metric_value_count = sum(len(values) for values in parsed.metrics.values())
    return (
        len(parsed.frame_numbers) * 32
        + metric_value_count * 32
        + len(parsed.metrics) * 60
        + 200
    )


class VmafCache:
    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_CACHE_MAX_BYTES,
        min_entries: int = DEFAULT_CACHE_MIN_ENTRIES,
    ) -> None:
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be a positive integer")
        if (
            isinstance(min_entries, bool)
            or not isinstance(min_entries, int)
            or min_entries < 0
        ):
            raise ValueError("min_entries must be a non-negative integer")

        self.max_bytes = max_bytes
        self.min_entries = min_entries
        self._items: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[_InflightKey, _PendingLoad] = {}
        self._estimated_bytes = 0
        self._epoch = 0
        self._lock = RLock()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def estimated_bytes(self) -> int:
        with self._lock:
            return self._estimated_bytes

    def get(self, record: FileRecord) -> ParsedVmaf:
        fingerprint = (record.size, record.mtime)

        while True:
            with self._lock:
                cached = self._items.get(record.id)
                if cached is not None:
                    if cached.fingerprint == fingerprint:
                        self._items.move_to_end(record.id)
                        return cached.parsed
                    self._remove_locked(record.id)

                epoch = self._epoch
                inflight_key = (epoch, record.id)
                pending = self._inflight.get(inflight_key)
                if pending is None:
                    pending = _PendingLoad(
                        fingerprint=fingerprint,
                        future=Future(),
                    )
                    self._inflight[inflight_key] = pending
                    break

            try:
                parsed = pending.future.result()
            except BaseException:
                if pending.fingerprint == fingerprint:
                    raise
                continue
            if pending.fingerprint == fingerprint:
                return parsed

        try:
            parsed = parse_vmaf_file(record)
            estimated_bytes = estimate_parsed_vmaf_bytes(parsed)
        except BaseException as exc:
            with self._lock:
                if self._inflight.get(inflight_key) is pending:
                    del self._inflight[inflight_key]
            pending.future.set_exception(exc)
            raise

        with self._lock:
            is_current = self._inflight.get(inflight_key) is pending
            if is_current:
                del self._inflight[inflight_key]
            if is_current and pending.cacheable and epoch == self._epoch:
                self._remove_locked(record.id)
                self._items[record.id] = _CacheEntry(
                    fingerprint=fingerprint,
                    parsed=parsed,
                    estimated_bytes=estimated_bytes,
                )
                self._estimated_bytes += estimated_bytes
                self._evict_locked()

        pending.future.set_result(parsed)
        return parsed

    def prune(self, valid_ids: set[str]) -> None:
        with self._lock:
            for file_id in list(self._items):
                if file_id not in valid_ids:
                    self._remove_locked(file_id)
            for (_, file_id), pending in self._inflight.items():
                if file_id not in valid_ids:
                    pending.cacheable = False

    def clear(self) -> None:
        with self._lock:
            self._epoch += 1
            self._items.clear()
            self._estimated_bytes = 0
            for pending in self._inflight.values():
                pending.cacheable = False

    def _remove_locked(self, file_id: str) -> None:
        cached = self._items.pop(file_id, None)
        if cached is not None:
            self._estimated_bytes -= cached.estimated_bytes

    def _evict_locked(self) -> None:
        while (
            self._estimated_bytes > self.max_bytes
            and len(self._items) > self.min_entries
        ):
            _, cached = self._items.popitem(last=False)
            self._estimated_bytes -= cached.estimated_bytes

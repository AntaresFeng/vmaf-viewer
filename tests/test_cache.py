from __future__ import annotations

import gc
import threading
import time
import weakref
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from vmaf_viewer import cache as cache_module
from vmaf_viewer.cache import VmafCache, estimate_parsed_vmaf_bytes
from vmaf_viewer.models import FileRecord, ParsedVmaf
from vmaf_viewer.parser import VmafParseError


def _record(file_id: str, *, size: int = 1, mtime: float = 1.0) -> FileRecord:
    return FileRecord(
        id=file_id,
        name=f"{file_id}.json",
        path=Path(f"{file_id}.json"),
        relative_path=f"{file_id}.json",
        size=size,
        mtime=mtime,
    )


def _parsed(
    record: FileRecord,
    *,
    frame_count: int = 1,
    metric_count: int = 1,
) -> ParsedVmaf:
    metrics = {
        f"metric_{index}": [float(index)] * frame_count
        for index in range(metric_count)
    }
    return ParsedVmaf(
        file=record,
        frame_numbers=list(range(frame_count)),
        metrics=metrics,
        primary_metric="metric_0" if metrics else None,
    )


@pytest.mark.parametrize(
    ("frame_count", "metric_count"),
    [(2, 3), (13_594, 31)],
)
def test_estimate_parsed_vmaf_bytes_uses_column_lengths(
    frame_count: int, metric_count: int
) -> None:
    parsed = _parsed(
        _record("estimate"),
        frame_count=frame_count,
        metric_count=metric_count,
    )

    assert estimate_parsed_vmaf_bytes(parsed) == (
        frame_count * 32
        + frame_count * metric_count * 32
        + metric_count * 60
        + 200
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_bytes": 0}, "max_bytes must be a positive integer"),
        ({"max_bytes": True}, "max_bytes must be a positive integer"),
        ({"min_entries": -1}, "min_entries must be a non-negative integer"),
        ({"min_entries": False}, "min_entries must be a non-negative integer"),
    ],
)
def test_cache_rejects_invalid_limits(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        VmafCache(**kwargs)


def test_lru_hit_refreshes_recency_and_evicts_oldest(monkeypatch) -> None:
    calls: Counter[str] = Counter()

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        calls[record.id] += 1
        return _parsed(record)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache(max_bytes=700, min_entries=1)
    first = _record("first")
    second = _record("second")
    third = _record("third")

    first_parsed = cache.get(first)
    second_parsed = cache.get(second)
    assert cache.get(first) is first_parsed
    cache.get(third)

    assert cache.get(second) is not second_parsed
    assert calls == Counter(first=1, second=2, third=1)


def test_replacing_stale_entry_updates_estimated_bytes(monkeypatch) -> None:
    def fake_parse(record: FileRecord) -> ParsedVmaf:
        return _parsed(record, frame_count=record.size)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache(max_bytes=10_000, min_entries=1)
    original = _record("same", size=1)
    changed = replace(original, size=2)

    cache.get(original)
    assert cache.estimated_bytes == 324

    cache.get(changed)
    assert cache.entry_count == 1
    assert cache.estimated_bytes == 388


def test_soft_budget_keeps_six_most_recent_entries(monkeypatch) -> None:
    calls: Counter[str] = Counter()

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        calls[record.id] += 1
        return _parsed(record)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache(max_bytes=1, min_entries=6)
    records = [_record(f"file-{index}") for index in range(7)]

    for record in records:
        cache.get(record)

    assert cache.entry_count == 6
    assert cache.estimated_bytes == 6 * 324
    cache.get(records[0])
    assert calls[records[0].id] == 2
    assert cache.entry_count == 6


def test_prune_releases_removed_entry(monkeypatch) -> None:
    monkeypatch.setattr(
        cache_module,
        "parse_vmaf_file",
        lambda record: _parsed(record, frame_count=100),
    )
    cache = VmafCache()
    parsed = cache.get(_record("removed"))
    parsed_ref = weakref.ref(parsed)
    del parsed

    cache.prune(set())
    gc.collect()

    assert cache.entry_count == 0
    assert cache.estimated_bytes == 0
    assert parsed_ref() is None


def test_single_flight_parses_same_version_once(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    call_count = 0
    call_lock = threading.Lock()

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        nonlocal call_count
        with call_lock:
            call_count += 1
        started.set()
        assert release.wait(timeout=2)
        return _parsed(record)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache()
    record = _record("shared")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(cache.get, record) for _ in range(8)]
        assert started.wait(timeout=2)
        release.set()
        results = [future.result(timeout=2) for future in futures]

    assert call_count == 1
    assert all(result is results[0] for result in results)


def test_single_flight_does_not_serialize_different_files(monkeypatch) -> None:
    parse_barrier = threading.Barrier(2)

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        parse_barrier.wait(timeout=2)
        return _parsed(record)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cache.get, _record("first"))
        second = executor.submit(cache.get, _record("second"))
        assert first.result(timeout=3).file.id == "first"
        assert second.result(timeout=3).file.id == "second"


def test_single_flight_propagates_failure_and_allows_retry(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    call_count = 0

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            started.set()
            assert release.wait(timeout=2)
            raise VmafParseError("broken")
        return _parsed(record)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache()
    record = _record("retry")

    with ThreadPoolExecutor(max_workers=2) as executor:
        loader = executor.submit(cache.get, record)
        assert started.wait(timeout=2)
        waiter = executor.submit(cache.get, record)
        time.sleep(0.05)
        release.set()
        with pytest.raises(VmafParseError, match="broken"):
            loader.result(timeout=2)
        with pytest.raises(VmafParseError, match="broken"):
            waiter.result(timeout=2)

    assert call_count == 1
    assert cache.get(record).file.id == record.id
    assert call_count == 2


def test_newer_fingerprint_reparses_after_inflight_version(monkeypatch) -> None:
    old_started = threading.Event()
    release_old = threading.Event()
    parsed_sizes: list[int] = []

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        parsed_sizes.append(record.size)
        if record.size == 1:
            old_started.set()
            assert release_old.wait(timeout=2)
        return _parsed(record, frame_count=record.size)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache()
    old = _record("changing", size=1)
    new = replace(old, size=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        old_future = executor.submit(cache.get, old)
        assert old_started.wait(timeout=2)
        new_future = executor.submit(cache.get, new)
        release_old.set()
        old_result = old_future.result(timeout=2)
        new_result = new_future.result(timeout=2)

    assert old_result.total_frames == 1
    assert new_result.total_frames == 2
    assert parsed_sizes == [1, 2]
    assert cache.get(new) is new_result


@pytest.mark.parametrize("invalidation", ["clear", "prune"])
def test_inflight_invalidation_prevents_cache_repopulation(
    monkeypatch, invalidation: str
) -> None:
    started = threading.Event()
    release = threading.Event()
    call_count = 0

    def fake_parse(record: FileRecord) -> ParsedVmaf:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            started.set()
            assert release.wait(timeout=2)
        return _parsed(record)

    monkeypatch.setattr(cache_module, "parse_vmaf_file", fake_parse)
    cache = VmafCache()
    record = _record("invalidated")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(cache.get, record)
        assert started.wait(timeout=2)
        if invalidation == "clear":
            cache.clear()
        else:
            cache.prune(set())
        release.set()
        first_result = future.result(timeout=2)

    assert cache.entry_count == 0
    assert cache.get(record) is not first_result
    assert call_count == 2

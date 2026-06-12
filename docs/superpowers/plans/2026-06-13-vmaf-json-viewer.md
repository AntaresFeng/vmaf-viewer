# VMAF JSON Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `uv`-managed local web app that scans local VMAF JSON files and compares 4-6 distorted encodes through summary metrics, per-frame charts, zoom charts, histograms, and CDFs.

**Architecture:** A FastAPI backend serves both JSON APIs and static frontend assets. Backend modules scan `videos/`, parse libvmaf JSON into compact numeric arrays, cache parsed data by path/mtime/size, compute common-range comparison statistics, and downsample chart series. The frontend is a static HTML/CSS/JavaScript workspace using ECharts, with default ranking by mean VMAF and chart controls for video and metric visibility.

**Tech Stack:** Python 3.11+, `uv`, FastAPI, Uvicorn, orjson, pytest, httpx, vanilla JavaScript, vendored Apache ECharts.

---

## Scope Check

The approved spec describes one cohesive subsystem: a local VMAF JSON comparison viewer. The work can be implemented as one plan because the backend parsing/statistics/API and frontend charts are tightly connected and produce one testable local app.

## File Structure

- Create `pyproject.toml`: `uv` project metadata, dependencies, test config, and `vmaf-viewer` console script.
- Create `src/vmaf_viewer/__init__.py`: package marker and version.
- Create `src/vmaf_viewer/models.py`: dataclasses and typed request/response helpers shared by parser, stats, compare, and API layers.
- Create `src/vmaf_viewer/scanner.py`: scan configured data directory for `*_vmaf.json` and produce stable file records.
- Create `src/vmaf_viewer/parser.py`: parse libvmaf JSON into numeric arrays and detect primary score metrics.
- Create `src/vmaf_viewer/stats.py`: compute finite-value summaries, percentiles, threshold counts, histograms, CDFs, and downsampled series.
- Create `src/vmaf_viewer/cache.py`: cache parsed files by path, size, and modification time.
- Create `src/vmaf_viewer/compare.py`: combine parsed files into common-frame comparison payloads.
- Create `src/vmaf_viewer/app.py`: FastAPI application, API routes, static file serving, and command entry point.
- Create `src/vmaf_viewer/static/index.html`: app shell.
- Create `src/vmaf_viewer/static/styles.css`: dense workbench layout and chart/table styling.
- Create `src/vmaf_viewer/static/app.js`: frontend state, API calls, table rendering, ECharts chart rendering, and controls.
- Create `src/vmaf_viewer/static/vendor/.gitkeep`: keeps vendor directory present before fetching ECharts.
- Create `scripts/fetch_echarts.py`: download ECharts into the static vendor directory for local runtime.
- Create `tests/fixtures/*.json`: small VMAF fixture files.
- Create `tests/test_scanner.py`: scanner tests.
- Create `tests/test_parser.py`: parser and primary metric detection tests.
- Create `tests/test_stats.py`: statistics, histogram, CDF, and downsampling tests.
- Create `tests/test_compare.py`: common-range comparison tests.
- Create `tests/test_api.py`: FastAPI API tests.
- Modify `.gitignore`: ignore local runtime/cache files if introduced by tools, while keeping `docs/superpowers/` tracked.

## Task 1: Initialize `uv` Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/vmaf_viewer/__init__.py`
- Create: `src/vmaf_viewer/static/vendor/.gitkeep`
- Modify: `.gitignore`

- [ ] **Step 1: Write project metadata**

Create `pyproject.toml` with this content:

```toml
[project]
name = "vmaf-json-viewer"
version = "0.1.0"
description = "Local web viewer for comparing libvmaf JSON outputs"
readme = "AGENTS.md"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "orjson>=3.10.0",
    "uvicorn[standard]>=0.30.0",
]

[project.scripts]
vmaf-viewer = "vmaf_viewer.app:main"

[tool.uv]
package = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Create package marker**

Create `src/vmaf_viewer/__init__.py` with this content:

```python
"""Local VMAF JSON comparison viewer."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Create static vendor directory marker**

Create `src/vmaf_viewer/static/vendor/.gitkeep` as an empty file.

- [ ] **Step 4: Ensure local runtime files stay ignored**

Update `.gitignore` so it includes these entries, keeping existing entries intact:

```gitignore
.uv-cache/
.superpowers/
```

Do not ignore `docs/superpowers/`.

- [ ] **Step 5: Sync dependencies with `uv`**

Run:

```powershell
uv sync
```

Expected: command exits 0 and creates `uv.lock`.

- [ ] **Step 6: Add test dependencies with `uv`**

Run:

```powershell
uv add --dev pytest httpx
```

Expected: command exits 0 and updates `pyproject.toml` plus `uv.lock`.

- [ ] **Step 7: Verify package import**

Run:

```powershell
uv run python -c "import vmaf_viewer; print(vmaf_viewer.__version__)"
```

Expected output:

```text
0.1.0
```

- [ ] **Step 8: Commit**

Run:

```powershell
git add .gitignore pyproject.toml uv.lock src/vmaf_viewer/__init__.py src/vmaf_viewer/static/vendor/.gitkeep
git commit -m "chore: initialize uv project"
```

## Task 2: Add Fixture Data And File Scanner

**Files:**
- Create: `tests/fixtures/alpha_vmaf.json`
- Create: `tests/fixtures/beta_vmaf.json`
- Create: `tests/fixtures/not_vmaf.txt`
- Create: `src/vmaf_viewer/models.py`
- Create: `src/vmaf_viewer/scanner.py`
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Add small VMAF fixtures**

Create `tests/fixtures/alpha_vmaf.json` with this content:

```json
{
  "version": "fixture",
  "fps": 1200.0,
  "frames": [
    {"frameNum": 0, "metrics": {"vmaf": 97.0, "integer_motion": 1.0}},
    {"frameNum": 1, "metrics": {"vmaf": 96.0, "integer_motion": 1.5}},
    {"frameNum": 2, "metrics": {"vmaf": 90.0, "integer_motion": 2.0}},
    {"frameNum": 3, "metrics": {"vmaf": 80.0, "integer_motion": 2.5}},
    {"frameNum": 4, "metrics": {"vmaf": 70.0, "integer_motion": 3.0}}
  ],
  "pooled_metrics": {
    "vmaf": {"min": 70.0, "max": 97.0, "mean": 86.6, "harmonic_mean": 85.0}
  },
  "aggregate_metrics": {}
}
```

Create `tests/fixtures/beta_vmaf.json` with this content:

```json
{
  "version": "fixture",
  "fps": 1000.0,
  "frames": [
    {"frameNum": 0, "metrics": {"vmaf": 92.0, "integer_motion": 1.0}},
    {"frameNum": 1, "metrics": {"vmaf": 91.0, "integer_motion": 1.5}},
    {"frameNum": 2, "metrics": {"vmaf": 89.0, "integer_motion": 2.0}},
    {"frameNum": 3, "metrics": {"vmaf": 88.0, "integer_motion": 2.5}}
  ],
  "pooled_metrics": {
    "vmaf": {"min": 88.0, "max": 92.0, "mean": 90.0, "harmonic_mean": 89.9}
  },
  "aggregate_metrics": {}
}
```

Create `tests/fixtures/not_vmaf.txt` with this content:

```text
not a json result
```

- [ ] **Step 2: Write failing scanner tests**

Create `tests/test_scanner.py` with this content:

```python
from pathlib import Path

from vmaf_viewer.scanner import scan_vmaf_files


def test_scan_vmaf_files_finds_only_vmaf_json_files():
    root = Path("tests/fixtures")

    records = scan_vmaf_files(root)

    assert [record.name for record in records] == ["alpha_vmaf.json", "beta_vmaf.json"]
    assert all(record.path.name.endswith("_vmaf.json") for record in records)
    assert all(record.size > 0 for record in records)
    assert all(record.mtime > 0 for record in records)


def test_scan_vmaf_files_uses_stable_relative_id():
    root = Path("tests/fixtures")

    first = scan_vmaf_files(root)
    second = scan_vmaf_files(root)

    assert [record.id for record in first] == [record.id for record in second]
    assert first[0].relative_path == "alpha_vmaf.json"


def test_scan_vmaf_files_missing_directory_returns_empty_list(tmp_path):
    missing = tmp_path / "missing"

    assert scan_vmaf_files(missing) == []
```

- [ ] **Step 3: Run scanner tests and verify failure**

Run:

```powershell
uv run pytest tests/test_scanner.py -q
```

Expected: FAIL with an import error because `vmaf_viewer.scanner` does not exist yet.

- [ ] **Step 4: Implement shared models**

Create `src/vmaf_viewer/models.py` with this content:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileRecord:
    id: str
    name: str
    path: Path
    relative_path: str
    size: int
    mtime: float

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "relative_path": self.relative_path,
            "size": self.size,
            "mtime": self.mtime,
        }


@dataclass(frozen=True)
class ParsedVmaf:
    file: FileRecord
    frame_numbers: list[int]
    metrics: dict[str, list[float]]
    primary_metric: str | None

    @property
    def total_frames(self) -> int:
        return len(self.frame_numbers)


@dataclass(frozen=True)
class ThresholdSummary:
    threshold: float
    count: int
    ratio: float
```

- [ ] **Step 5: Implement scanner**

Create `src/vmaf_viewer/scanner.py` with this content:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from .models import FileRecord


def _stable_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]


def scan_vmaf_files(root: Path) -> list[FileRecord]:
    root = root.resolve()
    if not root.exists() or not root.is_dir():
        return []

    records: list[FileRecord] = []
    for path in sorted(root.rglob("*_vmaf.json")):
        if not path.is_file():
            continue
        stat = path.stat()
        relative_path = path.relative_to(root).as_posix()
        records.append(
            FileRecord(
                id=_stable_id(relative_path),
                name=path.name,
                path=path,
                relative_path=relative_path,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    return records
```

- [ ] **Step 6: Run scanner tests and verify pass**

Run:

```powershell
uv run pytest tests/test_scanner.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add tests/fixtures tests/test_scanner.py src/vmaf_viewer/models.py src/vmaf_viewer/scanner.py
git commit -m "feat: scan local VMAF JSON files"
```

## Task 3: Parse VMAF JSON Files

**Files:**
- Create: `src/vmaf_viewer/parser.py`
- Test: `tests/test_parser.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_parser.py` with this content:

```python
from pathlib import Path

import pytest

from vmaf_viewer.parser import VmafParseError, parse_vmaf_file, select_primary_metric
from vmaf_viewer.scanner import scan_vmaf_files


def _record(name: str):
    return next(record for record in scan_vmaf_files(Path("tests/fixtures")) if record.name == name)


def test_select_primary_metric_prefers_vmaf():
    assert select_primary_metric(["integer_motion", "vmaf_hd", "vmaf"]) == "vmaf"


def test_select_primary_metric_falls_back_to_vmaf_hd():
    assert select_primary_metric(["integer_motion", "vmaf_hd"]) == "vmaf_hd"


def test_select_primary_metric_falls_back_to_first_metric_containing_vmaf():
    assert select_primary_metric(["integer_motion", "float_vmaf_custom"]) == "float_vmaf_custom"


def test_parse_vmaf_file_extracts_frames_metrics_and_primary_metric():
    parsed = parse_vmaf_file(_record("alpha_vmaf.json"))

    assert parsed.total_frames == 5
    assert parsed.frame_numbers == [0, 1, 2, 3, 4]
    assert parsed.primary_metric == "vmaf"
    assert parsed.metrics["vmaf"] == [97.0, 96.0, 90.0, 80.0, 70.0]
    assert parsed.metrics["integer_motion"] == [1.0, 1.5, 2.0, 2.5, 3.0]


def test_parse_vmaf_file_rejects_invalid_json(tmp_path):
    bad = tmp_path / "bad_vmaf.json"
    bad.write_text("{", encoding="utf-8")
    record = scan_vmaf_files(tmp_path)[0]

    with pytest.raises(VmafParseError, match="Invalid JSON"):
        parse_vmaf_file(record)


def test_parse_vmaf_file_rejects_missing_frames(tmp_path):
    bad = tmp_path / "bad_vmaf.json"
    bad.write_text('{"version":"fixture"}', encoding="utf-8")
    record = scan_vmaf_files(tmp_path)[0]

    with pytest.raises(VmafParseError, match="missing frames"):
        parse_vmaf_file(record)
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```powershell
uv run pytest tests/test_parser.py -q
```

Expected: FAIL with an import error because `vmaf_viewer.parser` does not exist yet.

- [ ] **Step 3: Implement parser**

Create `src/vmaf_viewer/parser.py` with this content:

```python
from __future__ import annotations

import math
from collections.abc import Iterable

import orjson

from .models import FileRecord, ParsedVmaf


class VmafParseError(ValueError):
    """Raised when a VMAF JSON file cannot be parsed into frame metrics."""


def select_primary_metric(metric_names: Iterable[str]) -> str | None:
    names = list(metric_names)
    if "vmaf" in names:
        return "vmaf"
    if "vmaf_hd" in names:
        return "vmaf_hd"
    for name in names:
        if "vmaf" in name:
            return name
    return None


def parse_vmaf_file(record: FileRecord) -> ParsedVmaf:
    try:
        data = orjson.loads(record.path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise VmafParseError(f"Invalid JSON in {record.relative_path}") from exc

    frames = data.get("frames")
    if not isinstance(frames, list):
        raise VmafParseError(f"{record.relative_path} is missing frames")

    frame_numbers: list[int] = []
    metric_names: list[str] = []
    metric_seen: set[str] = set()

    for item in frames:
        metrics = item.get("metrics") if isinstance(item, dict) else None
        if not isinstance(metrics, dict):
            continue
        frame_numbers.append(int(item.get("frameNum", len(frame_numbers))))
        for name in metrics:
            if name not in metric_seen:
                metric_seen.add(name)
                metric_names.append(name)

    values: dict[str, list[float]] = {name: [] for name in metric_names}
    for item in frames:
        metrics = item.get("metrics") if isinstance(item, dict) else None
        if not isinstance(metrics, dict):
            for name in metric_names:
                values[name].append(math.nan)
            continue
        for name in metric_names:
            raw = metrics.get(name)
            values[name].append(float(raw) if isinstance(raw, int | float) else math.nan)

    return ParsedVmaf(
        file=record,
        frame_numbers=frame_numbers,
        metrics=values,
        primary_metric=select_primary_metric(metric_names),
    )
```

- [ ] **Step 4: Run parser tests and scanner tests**

Run:

```powershell
uv run pytest tests/test_parser.py tests/test_scanner.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add tests/test_parser.py src/vmaf_viewer/parser.py
git commit -m "feat: parse VMAF JSON metrics"
```

## Task 4: Add Statistics And Series Utilities

**Files:**
- Create: `src/vmaf_viewer/stats.py`
- Test: `tests/test_stats.py`

- [ ] **Step 1: Write failing statistics tests**

Create `tests/test_stats.py` with this content:

```python
import math

from vmaf_viewer.stats import (
    build_cdf,
    build_histogram,
    downsample_series,
    summarize_values,
)


def test_summarize_values_computes_core_stats_and_thresholds():
    summary = summarize_values([97.0, 96.0, 90.0, 80.0, 70.0], [95.0, 90.0, 80.0, 60.0])

    assert summary["mean"] == 86.6
    assert summary["min"] == 70.0
    assert summary["max"] == 97.0
    assert summary["p1"] == 70.4
    assert summary["p5"] == 72.0
    assert summary["p10"] == 74.0
    assert summary["thresholds"][95.0]["count"] == 3
    assert summary["thresholds"][95.0]["ratio"] == 0.6
    assert summary["thresholds"][60.0]["count"] == 0


def test_summarize_values_ignores_nan_values():
    summary = summarize_values([100.0, math.nan, 80.0], [90.0])

    assert summary["mean"] == 90.0
    assert summary["thresholds"][90.0]["count"] == 1


def test_build_histogram_counts_values_in_shared_buckets():
    histogram = build_histogram([0.0, 0.5, 1.0, 99.9, 100.0], bucket_size=1.0)

    assert histogram[0] == {"start": 0.0, "end": 1.0, "count": 2}
    assert histogram[1] == {"start": 1.0, "end": 2.0, "count": 1}
    assert histogram[-1] == {"start": 99.0, "end": 100.0, "count": 2}


def test_build_cdf_uses_histogram_bucket_ends():
    cdf = build_cdf([0.0, 50.0, 100.0], bucket_size=50.0)

    assert cdf == [
        {"score": 50.0, "ratio": 1 / 3},
        {"score": 100.0, "ratio": 1.0},
    ]


def test_downsample_series_preserves_first_last_min_and_max():
    frames = list(range(10))
    values = [100.0, 99.0, 20.0, 98.0, 97.0, 96.0, 95.0, 10.0, 94.0, 93.0]

    series = downsample_series(frames, values, max_points=6)

    assert series[0] == [0, 100.0]
    assert series[-1] == [9, 93.0]
    assert [2, 20.0] in series
    assert [7, 10.0] in series
```

- [ ] **Step 2: Run stats tests and verify failure**

Run:

```powershell
uv run pytest tests/test_stats.py -q
```

Expected: FAIL with an import error because `vmaf_viewer.stats` does not exist yet.

- [ ] **Step 3: Implement statistics utilities**

Create `src/vmaf_viewer/stats.py` with this content:

```python
from __future__ import annotations

import math
from typing import Iterable


def finite_values(values: Iterable[float]) -> list[float]:
    return [float(value) for value in values if math.isfinite(float(value))]


def percentile(sorted_values: list[float], percent: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * (percent / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def summarize_values(values: Iterable[float], thresholds: Iterable[float]) -> dict:
    clean = finite_values(values)
    sorted_values = sorted(clean)
    count = len(clean)
    total = sum(clean)

    threshold_map: dict[float, dict[str, float | int]] = {}
    for threshold in thresholds:
        threshold_value = float(threshold)
        threshold_count = sum(1 for value in clean if value <= threshold_value)
        threshold_map[threshold_value] = {
            "count": threshold_count,
            "ratio": threshold_count / count if count else 0.0,
        }

    return {
        "count": count,
        "mean": total / count if count else math.nan,
        "min": sorted_values[0] if count else math.nan,
        "max": sorted_values[-1] if count else math.nan,
        "p1": percentile(sorted_values, 1.0),
        "p5": percentile(sorted_values, 5.0),
        "p10": percentile(sorted_values, 10.0),
        "thresholds": threshold_map,
    }


def build_histogram(values: Iterable[float], bucket_size: float = 1.0) -> list[dict[str, float | int]]:
    clean = finite_values(values)
    if bucket_size <= 0:
        raise ValueError("bucket_size must be positive")

    bucket_count = int(math.ceil(100.0 / bucket_size))
    buckets = [
        {"start": round(index * bucket_size, 6), "end": round((index + 1) * bucket_size, 6), "count": 0}
        for index in range(bucket_count)
    ]

    for value in clean:
        clamped = min(max(value, 0.0), 100.0)
        index = min(int(clamped // bucket_size), bucket_count - 1)
        buckets[index]["count"] += 1
    return buckets


def build_cdf(values: Iterable[float], bucket_size: float = 1.0) -> list[dict[str, float]]:
    histogram = build_histogram(values, bucket_size=bucket_size)
    total = sum(int(bucket["count"]) for bucket in histogram)
    if total == 0:
        return []

    cumulative = 0
    result: list[dict[str, float]] = []
    for bucket in histogram:
        cumulative += int(bucket["count"])
        if cumulative == 0:
            continue
        result.append({"score": float(bucket["end"]), "ratio": cumulative / total})
    return result


def downsample_series(frames: list[int], values: list[float], max_points: int = 2000) -> list[list[float]]:
    pairs = [[int(frame), float(value)] for frame, value in zip(frames, values) if math.isfinite(float(value))]
    if len(pairs) <= max_points:
        return pairs
    if max_points < 4:
        return [pairs[0], pairs[-1]]

    interior_slots = max_points - 2
    bucket_count = max(1, interior_slots // 2)
    bucket_size = max(1, math.ceil((len(pairs) - 2) / bucket_count))
    sampled: list[list[float]] = [pairs[0]]

    for start in range(1, len(pairs) - 1, bucket_size):
        bucket = pairs[start : min(start + bucket_size, len(pairs) - 1)]
        low = min(bucket, key=lambda item: item[1])
        high = max(bucket, key=lambda item: item[1])
        for point in sorted({tuple(low), tuple(high)}):
            sampled.append([point[0], point[1]])

    sampled.append(pairs[-1])
    sampled.sort(key=lambda item: item[0])
    if len(sampled) > max_points:
        sampled = sampled[: max_points - 1] + [pairs[-1]]
    return sampled
```

- [ ] **Step 4: Run stats tests and verify pass**

Run:

```powershell
uv run pytest tests/test_stats.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add tests/test_stats.py src/vmaf_viewer/stats.py
git commit -m "feat: compute VMAF comparison statistics"
```

## Task 5: Add Parsed File Cache And Comparison Service

**Files:**
- Create: `src/vmaf_viewer/cache.py`
- Create: `src/vmaf_viewer/compare.py`
- Test: `tests/test_compare.py`

- [ ] **Step 1: Write failing comparison tests**

Create `tests/test_compare.py` with this content:

```python
from pathlib import Path

from vmaf_viewer.cache import VmafCache
from vmaf_viewer.compare import compare_files
from vmaf_viewer.scanner import scan_vmaf_files


def _records():
    return scan_vmaf_files(Path("tests/fixtures"))


def test_compare_files_uses_shortest_common_range_and_mean_ranking():
    cache = VmafCache()
    result = compare_files(_records(), cache, thresholds=[95.0, 90.0, 80.0, 60.0])

    assert result["common_range"] == {"start": 0, "end": 3, "frame_count": 4}
    assert [row["name"] for row in result["summary"]] == ["alpha_vmaf.json", "beta_vmaf.json"]
    assert result["summary"][0]["stats"]["mean"] == 90.75
    assert result["summary"][1]["stats"]["mean"] == 90.0
    assert result["warnings"] == ["Frame counts differ; using first 4 common frames."]


def test_compare_files_returns_histogram_cdf_and_line_series():
    cache = VmafCache()
    records = _records()
    result = compare_files(records, cache, thresholds=[90.0], max_points=10)

    assert set(result["histogram"]) == {record.id for record in records}
    assert set(result["cdf"]) == {record.id for record in records}
    assert set(result["series"]) == {record.id for record in records}
    assert result["series"][records[0].id]["metric"] == "vmaf"
    assert result["series"][records[0].id]["points"][0] == [0, 97.0]


def test_compare_files_warns_when_no_primary_metric(tmp_path):
    bad = tmp_path / "bad_vmaf.json"
    bad.write_text(
        '{"version":"fixture","fps":1,"frames":[{"frameNum":0,"metrics":{"integer_motion":1.0}}],"pooled_metrics":{}}',
        encoding="utf-8",
    )
    cache = VmafCache()
    result = compare_files(scan_vmaf_files(tmp_path), cache, thresholds=[90.0])

    assert result["summary"] == []
    assert result["warnings"] == ["bad_vmaf.json has no VMAF score metric."]
```

- [ ] **Step 2: Run comparison tests and verify failure**

Run:

```powershell
uv run pytest tests/test_compare.py -q
```

Expected: FAIL with import errors for `vmaf_viewer.cache` and `vmaf_viewer.compare`.

- [ ] **Step 3: Implement parsed file cache**

Create `src/vmaf_viewer/cache.py` with this content:

```python
from __future__ import annotations

from .models import FileRecord, ParsedVmaf
from .parser import parse_vmaf_file


class VmafCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[int, float, ParsedVmaf]] = {}

    def get(self, record: FileRecord) -> ParsedVmaf:
        cached = self._items.get(record.id)
        if cached is not None:
            size, mtime, parsed = cached
            if size == record.size and mtime == record.mtime:
                return parsed

        parsed = parse_vmaf_file(record)
        self._items[record.id] = (record.size, record.mtime, parsed)
        return parsed

    def clear(self) -> None:
        self._items.clear()
```

- [ ] **Step 4: Implement comparison service**

Create `src/vmaf_viewer/compare.py` with this content:

```python
from __future__ import annotations

from .cache import VmafCache
from .models import FileRecord, ParsedVmaf
from .stats import build_cdf, build_histogram, downsample_series, summarize_values


def _metric_for(parsed: ParsedVmaf, requested_metric: str | None) -> str | None:
    if requested_metric and requested_metric in parsed.metrics:
        return requested_metric
    return parsed.primary_metric


def compare_files(
    records: list[FileRecord],
    cache: VmafCache,
    thresholds: list[float],
    metric: str | None = None,
    max_points: int = 2000,
) -> dict:
    parsed_items: list[tuple[ParsedVmaf, str]] = []
    warnings: list[str] = []

    for record in records:
        parsed = cache.get(record)
        selected_metric = _metric_for(parsed, metric)
        if selected_metric is None:
            warnings.append(f"{record.name} has no VMAF score metric.")
            continue
        if selected_metric not in parsed.metrics:
            warnings.append(f"{record.name} is missing metric {selected_metric}.")
            continue
        parsed_items.append((parsed, selected_metric))

    if not parsed_items:
        return {
            "files": [],
            "common_range": {"start": 0, "end": -1, "frame_count": 0},
            "summary": [],
            "series": {},
            "histogram": {},
            "cdf": {},
            "warnings": warnings,
        }

    common_count = min(parsed.total_frames for parsed, _metric_name in parsed_items)
    total_counts = {parsed.total_frames for parsed, _metric_name in parsed_items}
    if len(total_counts) > 1:
        warnings.append(f"Frame counts differ; using first {common_count} common frames.")

    summary_rows: list[dict] = []
    series: dict[str, dict] = {}
    histogram: dict[str, list[dict]] = {}
    cdf: dict[str, list[dict]] = {}
    files: list[dict] = []

    for parsed, metric_name in parsed_items:
        values = parsed.metrics[metric_name][:common_count]
        frames = parsed.frame_numbers[:common_count]
        stats = summarize_values(values, thresholds)
        api_file = parsed.file.to_api()
        api_file["total_frames"] = parsed.total_frames
        api_file["primary_metric"] = parsed.primary_metric
        files.append(api_file)
        summary_rows.append(
            {
                "id": parsed.file.id,
                "name": parsed.file.name,
                "relative_path": parsed.file.relative_path,
                "metric": metric_name,
                "total_frames": parsed.total_frames,
                "common_frames": common_count,
                "stats": stats,
            }
        )
        series[parsed.file.id] = {
            "metric": metric_name,
            "points": downsample_series(frames, values, max_points=max_points),
        }
        histogram[parsed.file.id] = build_histogram(values, bucket_size=1.0)
        cdf[parsed.file.id] = build_cdf(values, bucket_size=1.0)

    summary_rows.sort(key=lambda row: row["stats"]["mean"], reverse=True)

    return {
        "files": files,
        "common_range": {"start": 0, "end": common_count - 1, "frame_count": common_count},
        "summary": summary_rows,
        "series": series,
        "histogram": histogram,
        "cdf": cdf,
        "warnings": warnings,
    }
```

- [ ] **Step 5: Run comparison tests and verify pass**

Run:

```powershell
uv run pytest tests/test_compare.py -q
```

Expected: PASS.

- [ ] **Step 6: Run all backend unit tests**

Run:

```powershell
uv run pytest tests/test_scanner.py tests/test_parser.py tests/test_stats.py tests/test_compare.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

Run:

```powershell
git add tests/test_compare.py src/vmaf_viewer/cache.py src/vmaf_viewer/compare.py
git commit -m "feat: compare VMAF files over common ranges"
```

## Task 6: Add FastAPI Backend

**Files:**
- Create: `src/vmaf_viewer/app.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_api.py` with this content:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from vmaf_viewer.app import create_app


def test_api_files_returns_scanned_json_files():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.get("/api/files")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["files"]] == ["alpha_vmaf.json", "beta_vmaf.json"]
    assert body["data_dir"].endswith("tests/fixtures")


def test_api_compare_returns_summary_and_charts():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/compare",
        json={"file_ids": [item["id"] for item in files], "thresholds": [95, 90, 80, 60], "max_points": 100},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["common_range"]["frame_count"] == 4
    assert [row["name"] for row in body["summary"]] == ["alpha_vmaf.json", "beta_vmaf.json"]
    assert set(body["series"]) == {item["id"] for item in files}


def test_api_metrics_returns_metric_names_for_one_file():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    file_id = client.get("/api/files").json()["files"][0]["id"]

    response = client.get(f"/api/file/{file_id}/metrics")

    assert response.status_code == 200
    assert response.json()["metrics"] == ["vmaf", "integer_motion"]


def test_api_series_returns_requested_metric_range():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))
    files = client.get("/api/files").json()["files"]

    response = client.post(
        "/api/series",
        json={
            "file_ids": [files[0]["id"]],
            "metrics": ["integer_motion"],
            "start": 1,
            "end": 3,
            "max_points": 100,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["series"][files[0]["id"]]["integer_motion"]["points"] == [[1, 1.5], [2, 2.0], [3, 2.5]]
```

- [ ] **Step 2: Run API tests and verify failure**

Run:

```powershell
uv run pytest tests/test_api.py -q
```

Expected: FAIL with an import error because `vmaf_viewer.app` does not exist yet.

- [ ] **Step 3: Implement FastAPI app**

Create `src/vmaf_viewer/app.py` with this content:

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .cache import VmafCache
from .compare import compare_files
from .scanner import scan_vmaf_files
from .stats import downsample_series


DEFAULT_THRESHOLDS = [95.0, 90.0, 80.0, 60.0]


class CompareRequest(BaseModel):
    file_ids: list[str]
    metric: str | None = None
    thresholds: list[float] = Field(default_factory=lambda: DEFAULT_THRESHOLDS.copy())
    max_points: int = 2000


class SeriesRequest(BaseModel):
    file_ids: list[str]
    metrics: list[str]
    start: int = 0
    end: int | None = None
    max_points: int = 2000


class AppState:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cache = VmafCache()

    def records(self):
        return scan_vmaf_files(self.data_dir)

    def selected_records(self, file_ids: list[str]):
        by_id = {record.id: record for record in self.records()}
        missing = [file_id for file_id in file_ids if file_id not in by_id]
        if missing:
            raise HTTPException(status_code=404, detail=f"Unknown file id: {missing[0]}")
        return [by_id[file_id] for file_id in file_ids]


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = Path.cwd()
    resolved_data_dir = data_dir or Path(os.environ.get("VMAF_VIEWER_DATA_DIR", root / "videos"))
    state = AppState(resolved_data_dir)
    app = FastAPI(title="VMAF JSON Viewer")
    static_dir = Path(__file__).with_name("static")

    @app.get("/api/files")
    def files():
        records = state.records()
        return {
            "data_dir": str(state.data_dir),
            "files": [record.to_api() for record in records],
        }

    @app.post("/api/compare")
    def compare(request: CompareRequest):
        records = state.selected_records(request.file_ids)
        return compare_files(
            records,
            state.cache,
            thresholds=[float(value) for value in request.thresholds],
            metric=request.metric,
            max_points=request.max_points,
        )

    @app.get("/api/file/{file_id}/metrics")
    def metrics(file_id: str):
        record = state.selected_records([file_id])[0]
        parsed = state.cache.get(record)
        return {
            "id": file_id,
            "metrics": list(parsed.metrics),
            "primary_metric": parsed.primary_metric,
            "total_frames": parsed.total_frames,
        }

    @app.post("/api/series")
    def series(request: SeriesRequest):
        records = state.selected_records(request.file_ids)
        response: dict[str, dict[str, dict[str, list[list[float]]]]] = {}
        for record in records:
            parsed = state.cache.get(record)
            end = request.end if request.end is not None else parsed.total_frames - 1
            start = max(0, request.start)
            stop = min(end + 1, parsed.total_frames)
            frames = parsed.frame_numbers[start:stop]
            response[record.id] = {}
            for metric in request.metrics:
                if metric not in parsed.metrics:
                    continue
                values = parsed.metrics[metric][start:stop]
                response[record.id][metric] = {
                    "points": downsample_series(frames, values, max_points=request.max_points)
                }
        return {"series": response}

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app


def main() -> None:
    uvicorn.run("vmaf_viewer.app:create_app", factory=True, host="127.0.0.1", port=8765, reload=True)
```

- [ ] **Step 4: Run API tests and verify pass**

Run:

```powershell
uv run pytest tests/test_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```powershell
uv run pytest
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add tests/test_api.py src/vmaf_viewer/app.py
git commit -m "feat: serve VMAF viewer API"
```

## Task 7: Vendor ECharts For Local Runtime

**Files:**
- Create: `scripts/fetch_echarts.py`
- Create by command: `src/vmaf_viewer/static/vendor/echarts.min.js`

- [ ] **Step 1: Add ECharts fetch script**

Create `scripts/fetch_echarts.py` with this content:

```python
from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve


ECHARTS_URL = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
OUTPUT = Path("src/vmaf_viewer/static/vendor/echarts.min.js")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(ECHARTS_URL, OUTPUT)
    size = OUTPUT.stat().st_size
    if size < 500_000:
        raise SystemExit(f"Downloaded ECharts file is unexpectedly small: {size} bytes")
    print(f"Saved {OUTPUT} ({size} bytes)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Fetch ECharts with `uv`**

Run:

```powershell
uv run python scripts/fetch_echarts.py
```

Expected: command exits 0 and prints a saved `src/vmaf_viewer/static/vendor/echarts.min.js` file larger than 500,000 bytes.

- [ ] **Step 3: Verify ECharts file is present**

Run:

```powershell
Test-Path src/vmaf_viewer/static/vendor/echarts.min.js
```

Expected output:

```text
True
```

- [ ] **Step 4: Commit**

Run:

```powershell
git add scripts/fetch_echarts.py src/vmaf_viewer/static/vendor/echarts.min.js
git commit -m "chore: vendor ECharts asset"
```

## Task 8: Build Static Frontend Shell

**Files:**
- Create: `src/vmaf_viewer/static/index.html`
- Create: `src/vmaf_viewer/static/styles.css`
- Create: `src/vmaf_viewer/static/app.js`

- [ ] **Step 1: Create app HTML shell**

Create `src/vmaf_viewer/static/index.html` with this content:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>VMAF JSON Viewer</title>
    <link rel="stylesheet" href="/static/styles.css">
  </head>
  <body>
    <header class="topbar">
      <div>
        <h1>VMAF JSON Viewer</h1>
        <p id="scanPath">Scanning...</p>
      </div>
      <div class="toolbar">
        <label class="thresholds">
          Thresholds
          <input id="thresholdInput" value="95,90,80,60">
        </label>
        <button id="refreshButton" type="button">Refresh</button>
        <span id="selectedCount">0 selected</span>
      </div>
    </header>
    <main class="layout">
      <aside class="sidebar">
        <input id="fileFilter" class="search" placeholder="Filter JSON files">
        <div id="fileList" class="file-list"></div>
      </aside>
      <section class="workspace">
        <div id="messages" class="messages"></div>
        <section class="panel">
          <div class="panel-header">
            <h2>Summary</h2>
            <span>Sorted by mean VMAF</span>
          </div>
          <div class="table-wrap">
            <table id="summaryTable">
              <thead></thead>
              <tbody></tbody>
            </table>
          </div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h2>Per-frame VMAF</h2>
            <div id="videoLegend" class="chip-row"></div>
          </div>
          <div id="lineChart" class="chart large"></div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h2>Local Zoom</h2>
            <div id="metricToggles" class="chip-row"></div>
          </div>
          <div id="zoomChart" class="chart large"></div>
        </section>
        <section class="panel">
          <div class="panel-header">
            <h2>Distribution</h2>
            <div class="tabs">
              <button id="histogramTab" class="active" type="button">Histogram</button>
              <button id="cdfTab" type="button">CDF</button>
            </div>
          </div>
          <div id="histogramChart" class="chart"></div>
          <div id="cdfChart" class="chart hidden"></div>
        </section>
      </section>
    </main>
    <script src="/static/vendor/echarts.min.js"></script>
    <script src="/static/app.js"></script>
  </body>
</html>
```

- [ ] **Step 2: Create workbench CSS**

Create `src/vmaf_viewer/static/styles.css` with this content:

```css
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  color: #1f2933;
  background: #f5f7fa;
  font-family: Inter, "Segoe UI", Arial, sans-serif;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  min-height: 72px;
  padding: 12px 20px;
  border-bottom: 1px solid #d8dee8;
  background: #ffffff;
}

h1,
h2 {
  margin: 0;
  letter-spacing: 0;
}

h1 {
  font-size: 20px;
}

h2 {
  font-size: 15px;
}

.topbar p,
.panel-header span {
  margin: 4px 0 0;
  color: #667085;
  font-size: 12px;
}

.toolbar,
.chip-row,
.tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.thresholds {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #475467;
  font-size: 13px;
}

input,
button {
  font: inherit;
}

button {
  border: 1px solid #c8d1df;
  background: #ffffff;
  color: #1f2933;
  border-radius: 6px;
  padding: 7px 10px;
  cursor: pointer;
}

button.active,
.chip.active {
  border-color: #2563eb;
  background: #eff6ff;
  color: #1d4ed8;
}

#thresholdInput,
.search {
  border: 1px solid #c8d1df;
  border-radius: 6px;
  padding: 7px 9px;
}

#thresholdInput {
  width: 140px;
}

.layout {
  display: grid;
  grid-template-columns: 310px minmax(0, 1fr);
  min-height: calc(100vh - 72px);
}

.sidebar {
  border-right: 1px solid #d8dee8;
  background: #ffffff;
  padding: 14px;
  overflow: auto;
}

.search {
  width: 100%;
  margin-bottom: 12px;
}

.file-list {
  display: grid;
  gap: 8px;
}

.file-item {
  width: 100%;
  text-align: left;
  border: 1px solid #d8dee8;
  background: #ffffff;
  border-radius: 6px;
  padding: 10px;
}

.file-item strong {
  display: block;
  overflow-wrap: anywhere;
  font-size: 13px;
}

.file-item span {
  display: block;
  margin-top: 4px;
  color: #667085;
  font-size: 12px;
}

.workspace {
  display: grid;
  gap: 14px;
  padding: 14px;
  overflow: auto;
}

.panel {
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #ffffff;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid #edf0f5;
}

.messages {
  display: grid;
  gap: 8px;
}

.message {
  border: 1px solid #f5c2c7;
  border-radius: 6px;
  background: #fff5f5;
  color: #842029;
  padding: 8px 10px;
  font-size: 13px;
}

.table-wrap {
  overflow: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th,
td {
  padding: 8px 10px;
  border-bottom: 1px solid #edf0f5;
  text-align: right;
  white-space: nowrap;
}

th:first-child,
td:first-child {
  text-align: left;
}

.best {
  color: #047857;
  font-weight: 700;
}

.weak {
  color: #b42318;
}

.chart {
  width: 100%;
  height: 340px;
}

.chart.large {
  height: 420px;
}

.hidden {
  display: none;
}

.chip {
  border: 1px solid #c8d1df;
  border-radius: 999px;
  background: #ffffff;
  padding: 5px 9px;
  font-size: 12px;
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .sidebar {
    border-right: 0;
    border-bottom: 1px solid #d8dee8;
  }
}
```

- [ ] **Step 3: Create frontend boot script**

Create `src/vmaf_viewer/static/app.js` with this content:

```javascript
const state = {
  files: [],
  selected: new Set(),
  comparison: null,
  hiddenFiles: new Set(),
  activeMetrics: new Set(),
  thresholds: [95, 90, 80, 60],
  distribution: "histogram",
};

const el = {
  scanPath: document.querySelector("#scanPath"),
  thresholdInput: document.querySelector("#thresholdInput"),
  refreshButton: document.querySelector("#refreshButton"),
  selectedCount: document.querySelector("#selectedCount"),
  fileFilter: document.querySelector("#fileFilter"),
  fileList: document.querySelector("#fileList"),
  messages: document.querySelector("#messages"),
  summaryHead: document.querySelector("#summaryTable thead"),
  summaryBody: document.querySelector("#summaryTable tbody"),
  videoLegend: document.querySelector("#videoLegend"),
  metricToggles: document.querySelector("#metricToggles"),
  histogramTab: document.querySelector("#histogramTab"),
  cdfTab: document.querySelector("#cdfTab"),
  histogramChart: document.querySelector("#histogramChart"),
  cdfChart: document.querySelector("#cdfChart"),
};

const charts = {
  line: echarts.init(document.querySelector("#lineChart")),
  zoom: echarts.init(document.querySelector("#zoomChart")),
  histogram: echarts.init(document.querySelector("#histogramChart")),
  cdf: echarts.init(document.querySelector("#cdfChart")),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function parseThresholds() {
  const values = el.thresholdInput.value
    .split(",")
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item));
  state.thresholds = values.length ? values : [95, 90, 80, 60];
}

function formatNumber(value, digits = 2) {
  return Number.isFinite(value) ? value.toFixed(digits) : "n/a";
}

function renderMessages(messages = []) {
  el.messages.innerHTML = "";
  for (const message of messages) {
    const node = document.createElement("div");
    node.className = "message";
    node.textContent = message;
    el.messages.appendChild(node);
  }
}

function renderFiles() {
  const query = el.fileFilter.value.trim().toLowerCase();
  el.fileList.innerHTML = "";
  const files = state.files.filter((file) => file.name.toLowerCase().includes(query));
  for (const file of files) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `file-item ${state.selected.has(file.id) ? "active" : ""}`;
    button.innerHTML = `<strong>${file.name}</strong><span>${file.relative_path} - ${(file.size / 1024).toFixed(1)} KB</span>`;
    button.addEventListener("click", () => {
      if (state.selected.has(file.id)) {
        state.selected.delete(file.id);
      } else {
        state.selected.add(file.id);
      }
      renderFiles();
      updateSelectedCount();
      requestComparison();
    });
    el.fileList.appendChild(button);
  }
}

function updateSelectedCount() {
  el.selectedCount.textContent = `${state.selected.size} selected`;
}

async function loadFiles() {
  const body = await api("/api/files");
  state.files = body.files;
  el.scanPath.textContent = body.data_dir;
  renderFiles();
  updateSelectedCount();
  if (state.files.length === 0) {
    renderMessages(["No *_vmaf.json files found."]);
  } else {
    renderMessages([]);
  }
}

async function requestComparison() {
  parseThresholds();
  if (state.selected.size === 0) {
    state.comparison = null;
    renderSummary();
    renderCharts();
    return;
  }
  const body = await api("/api/compare", {
    method: "POST",
    body: JSON.stringify({
      file_ids: Array.from(state.selected),
      thresholds: state.thresholds,
      max_points: 2000,
    }),
  });
  state.comparison = body;
  renderMessages(body.warnings || []);
  renderSummary();
  renderControls();
  renderCharts();
}

function thresholdKeys() {
  return state.thresholds.map((value) => Number(value));
}

function renderSummary() {
  const rows = state.comparison?.summary || [];
  const thresholds = thresholdKeys();
  const headers = ["Video", "Mean", "Min", "Max", "P1", "P5", "P10", ...thresholds.map((t) => `<=${t}`), "Frames"];
  el.summaryHead.innerHTML = `<tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr>`;
  el.summaryBody.innerHTML = "";
  const bestMean = Math.max(...rows.map((row) => row.stats.mean), -Infinity);
  for (const row of rows) {
    const stats = row.stats;
    const thresholdCells = thresholds
      .map((threshold) => {
        const item = stats.thresholds[String(threshold)] || stats.thresholds[threshold];
        return `<td>${item ? `${item.count} (${formatNumber(item.ratio * 100, 1)}%)` : "n/a"}</td>`;
      })
      .join("");
    const meanClass = stats.mean === bestMean ? "best" : bestMean - stats.mean >= 1 ? "weak" : "";
    el.summaryBody.insertAdjacentHTML(
      "beforeend",
      `<tr>
        <td>${row.name}</td>
        <td class="${meanClass}">${formatNumber(stats.mean)}</td>
        <td>${formatNumber(stats.min)}</td>
        <td>${formatNumber(stats.max)}</td>
        <td>${formatNumber(stats.p1)}</td>
        <td>${formatNumber(stats.p5)}</td>
        <td>${formatNumber(stats.p10)}</td>
        ${thresholdCells}
        <td>${row.common_frames}/${row.total_frames}</td>
      </tr>`
    );
  }
}

function renderControls() {
  const rows = state.comparison?.summary || [];
  el.videoLegend.innerHTML = "";
  for (const row of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chip ${state.hiddenFiles.has(row.id) ? "" : "active"}`;
    button.textContent = row.name;
    button.addEventListener("click", () => {
      if (state.hiddenFiles.has(row.id)) {
        state.hiddenFiles.delete(row.id);
      } else {
        state.hiddenFiles.add(row.id);
      }
      renderControls();
      renderCharts();
    });
    el.videoLegend.appendChild(button);
  }

  el.metricToggles.innerHTML = "";
  const metricButton = document.createElement("button");
  metricButton.type = "button";
  metricButton.className = "chip active";
  metricButton.textContent = "Primary VMAF";
  el.metricToggles.appendChild(metricButton);
}

function visibleRows() {
  return (state.comparison?.summary || []).filter((row) => !state.hiddenFiles.has(row.id));
}

function referenceLines() {
  return state.thresholds.map((threshold) => ({
    yAxis: threshold,
    lineStyle: { type: "dashed", color: "#98a2b3" },
    label: { formatter: String(threshold) },
  }));
}

function renderLineCharts() {
  const rows = visibleRows();
  const series = rows.map((row) => ({
    name: row.name,
    type: "line",
    showSymbol: false,
    sampling: "lttb",
    data: state.comparison.series[row.id]?.points || [],
    markLine: { symbol: "none", data: referenceLines() },
  }));
  const option = {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { top: 0, type: "scroll" },
    grid: { left: 52, right: 24, top: 42, bottom: 54 },
    xAxis: { type: "value", name: "Frame" },
    yAxis: { type: "value", min: 0, max: 100, name: "VMAF" },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 24 }],
    series,
  };
  charts.line.setOption(option, true);
  charts.zoom.setOption({ ...option, dataZoom: [{ type: "slider", height: 36, bottom: 8 }] }, true);
}

function renderDistributionCharts() {
  const rows = visibleRows();
  charts.histogram.setOption(
    {
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { top: 0, type: "scroll" },
      grid: { left: 52, right: 24, top: 42, bottom: 42 },
      xAxis: { type: "category", name: "VMAF bucket" },
      yAxis: { type: "value", name: "Frames" },
      series: rows.map((row) => ({
        name: row.name,
        type: "bar",
        data: (state.comparison.histogram[row.id] || []).map((bucket) => [bucket.start, bucket.count]),
      })),
    },
    true
  );
  charts.cdf.setOption(
    {
      animation: false,
      tooltip: { trigger: "axis" },
      legend: { top: 0, type: "scroll" },
      grid: { left: 52, right: 24, top: 42, bottom: 42 },
      xAxis: { type: "value", min: 0, max: 100, name: "VMAF" },
      yAxis: { type: "value", min: 0, max: 1, name: "Ratio" },
      series: rows.map((row) => ({
        name: row.name,
        type: "line",
        showSymbol: false,
        data: (state.comparison.cdf[row.id] || []).map((point) => [point.score, point.ratio]),
      })),
    },
    true
  );
}

function renderCharts() {
  if (!state.comparison) {
    for (const chart of Object.values(charts)) {
      chart.clear();
    }
    return;
  }
  renderLineCharts();
  renderDistributionCharts();
}

function setupEvents() {
  el.refreshButton.addEventListener("click", loadFiles);
  el.fileFilter.addEventListener("input", renderFiles);
  el.thresholdInput.addEventListener("change", requestComparison);
  el.histogramTab.addEventListener("click", () => {
    el.histogramTab.classList.add("active");
    el.cdfTab.classList.remove("active");
    el.histogramChart.classList.remove("hidden");
    el.cdfChart.classList.add("hidden");
    charts.histogram.resize();
  });
  el.cdfTab.addEventListener("click", () => {
    el.cdfTab.classList.add("active");
    el.histogramTab.classList.remove("active");
    el.cdfChart.classList.remove("hidden");
    el.histogramChart.classList.add("hidden");
    charts.cdf.resize();
  });
  window.addEventListener("resize", () => {
    for (const chart of Object.values(charts)) {
      chart.resize();
    }
  });
}

setupEvents();
loadFiles().catch((error) => renderMessages([error.message]));
```

- [ ] **Step 4: Start app and inspect page**

Run:

```powershell
uv run vmaf-viewer
```

Expected: Uvicorn serves `http://127.0.0.1:8765`.

Open `http://127.0.0.1:8765` in a browser. Expected: the workbench layout appears with top bar, left file list, summary panel, line chart panel, zoom panel, and distribution panel.

- [ ] **Step 5: Commit**

Stop the server with `Ctrl+C`, then run:

```powershell
git add src/vmaf_viewer/static/index.html src/vmaf_viewer/static/styles.css src/vmaf_viewer/static/app.js
git commit -m "feat: add VMAF viewer frontend shell"
```

## Task 9: Add Metric Lazy Loading And Zoom Range Requests

**Files:**
- Modify: `src/vmaf_viewer/static/app.js`
- Test: manual browser verification

- [ ] **Step 1: Add frontend metric cache and metric loading functions**

In `src/vmaf_viewer/static/app.js`, replace the `state` object with this version:

```javascript
const state = {
  files: [],
  selected: new Set(),
  comparison: null,
  hiddenFiles: new Set(),
  activeMetrics: new Set(["primary"]),
  metricsByFile: new Map(),
  extraSeries: new Map(),
  thresholds: [95, 90, 80, 60],
  distribution: "histogram",
};
```

Add these functions after `requestComparison()`:

```javascript
async function loadMetricsForSelected() {
  const ids = Array.from(state.selected);
  for (const id of ids) {
    if (state.metricsByFile.has(id)) {
      continue;
    }
    const body = await api(`/api/file/${id}/metrics`);
    state.metricsByFile.set(id, body.metrics);
  }
}

function sharedMetrics() {
  const ids = Array.from(state.selected);
  if (ids.length === 0) {
    return [];
  }
  const metricSets = ids.map((id) => new Set(state.metricsByFile.get(id) || []));
  const first = Array.from(metricSets[0]);
  return first.filter((metric) => metricSets.every((set) => set.has(metric)));
}

async function requestExtraSeries(metric) {
  const key = metric;
  if (state.extraSeries.has(key)) {
    return;
  }
  const body = await api("/api/series", {
    method: "POST",
    body: JSON.stringify({
      file_ids: Array.from(state.selected),
      metrics: [metric],
      start: 0,
      end: state.comparison ? state.comparison.common_range.end : null,
      max_points: 2000,
    }),
  });
  state.extraSeries.set(key, body.series);
}
```

- [ ] **Step 2: Update comparison request to load metrics**

In `requestComparison()`, after assigning `state.comparison = body;`, add:

```javascript
state.extraSeries.clear();
await loadMetricsForSelected();
```

- [ ] **Step 3: Replace metric toggle rendering**

Replace the metric toggle section inside `renderControls()` with this code:

```javascript
el.metricToggles.innerHTML = "";
const metrics = sharedMetrics().filter((metric) => metric !== "vmaf");
const primaryButton = document.createElement("button");
primaryButton.type = "button";
primaryButton.className = `chip ${state.activeMetrics.has("primary") ? "active" : ""}`;
primaryButton.textContent = "Primary VMAF";
primaryButton.addEventListener("click", () => {
  if (state.activeMetrics.has("primary")) {
    state.activeMetrics.delete("primary");
  } else {
    state.activeMetrics.add("primary");
  }
  renderControls();
  renderCharts();
});
el.metricToggles.appendChild(primaryButton);

for (const metric of metrics) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `chip ${state.activeMetrics.has(metric) ? "active" : ""}`;
  button.textContent = metric;
  button.addEventListener("click", async () => {
    if (state.activeMetrics.has(metric)) {
      state.activeMetrics.delete(metric);
    } else {
      state.activeMetrics.add(metric);
      await requestExtraSeries(metric);
    }
    renderControls();
    renderCharts();
  });
  el.metricToggles.appendChild(button);
}
```

- [ ] **Step 4: Include active extra metrics in line chart series**

In `renderLineCharts()`, after the primary `series` array is built, add:

```javascript
for (const metric of state.activeMetrics) {
  if (metric === "primary") {
    continue;
  }
  const metricSeries = state.extraSeries.get(metric) || {};
  for (const row of rows) {
    const points = metricSeries[row.id]?.[metric]?.points || [];
    series.push({
      name: `${row.name} ${metric}`,
      type: "line",
      showSymbol: false,
      data: points,
      lineStyle: { type: "dotted" },
    });
  }
}
```

- [ ] **Step 5: Add focused zoom series refresh on dataZoom**

After the first `charts.zoom.setOption(...)` call in `renderLineCharts()`, add:

```javascript
charts.zoom.off("datazoom");
charts.zoom.on("datazoom", async () => {
  const option = charts.zoom.getOption();
  const zoom = option.dataZoom?.[0];
  if (!zoom || !state.comparison) {
    return;
  }
  const endFrame = state.comparison.common_range.end;
  const start = Math.max(0, Math.floor((zoom.start / 100) * endFrame));
  const end = Math.min(endFrame, Math.ceil((zoom.end / 100) * endFrame));
  if (end - start > 5000) {
    return;
  }
  const metrics = Array.from(state.activeMetrics).filter((metric) => metric !== "primary");
  if (metrics.length === 0) {
    return;
  }
  const body = await api("/api/series", {
    method: "POST",
    body: JSON.stringify({
      file_ids: Array.from(state.selected),
      metrics,
      start,
      end,
      max_points: 5000,
    }),
  });
  for (const metric of metrics) {
    state.extraSeries.set(metric, body.series);
  }
  renderCharts();
});
```

- [ ] **Step 6: Browser verification**

Run:

```powershell
uv run vmaf-viewer
```

Open `http://127.0.0.1:8765`, select two fixture files or local files, and verify:

- metric chips appear after selecting files
- clicking `integer_motion` adds dotted metric lines
- hiding one video removes its primary and sub-metric lines
- zooming into a range below 5,000 frames keeps charts responsive

- [ ] **Step 7: Commit**

Stop the server with `Ctrl+C`, then run:

```powershell
git add src/vmaf_viewer/static/app.js
git commit -m "feat: lazy load metric series"
```

## Task 10: Polish API Errors And Empty States

**Files:**
- Modify: `src/vmaf_viewer/app.py`
- Modify: `src/vmaf_viewer/static/app.js`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add API error tests**

Append these tests to `tests/test_api.py`:

```python
def test_api_compare_rejects_empty_selection():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.post("/api/compare", json={"file_ids": [], "thresholds": [90]})

    assert response.status_code == 400
    assert response.json()["detail"] == "Select at least one VMAF JSON file."


def test_api_compare_rejects_unknown_file_id():
    client = TestClient(create_app(data_dir=Path("tests/fixtures")))

    response = client.post("/api/compare", json={"file_ids": ["missing"], "thresholds": [90]})

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown file id: missing"
```

- [ ] **Step 2: Run API tests and verify failure**

Run:

```powershell
uv run pytest tests/test_api.py -q
```

Expected: FAIL because empty selection currently returns an empty comparison payload.

- [ ] **Step 3: Add explicit empty-selection validation**

In `src/vmaf_viewer/app.py`, inside the `compare` route before `records = state.selected_records(request.file_ids)`, add:

```python
if not request.file_ids:
    raise HTTPException(status_code=400, detail="Select at least one VMAF JSON file.")
```

- [ ] **Step 4: Improve frontend empty state**

In `src/vmaf_viewer/static/app.js`, update the empty-selection branch inside `requestComparison()` to this:

```javascript
if (state.selected.size === 0) {
  state.comparison = null;
  renderMessages(state.files.length ? ["Select 1-6 VMAF JSON files to compare."] : ["No *_vmaf.json files found."]);
  renderSummary();
  renderCharts();
  return;
}
```

Also wrap the API call in `requestComparison()` with a `try`/`catch`:

```javascript
try {
  const body = await api("/api/compare", {
    method: "POST",
    body: JSON.stringify({
      file_ids: Array.from(state.selected),
      thresholds: state.thresholds,
      max_points: 2000,
    }),
  });
  state.comparison = body;
  state.extraSeries.clear();
  await loadMetricsForSelected();
  renderMessages(body.warnings || []);
  renderSummary();
  renderControls();
  renderCharts();
} catch (error) {
  renderMessages([error.message]);
}
```

- [ ] **Step 5: Run full test suite**

Run:

```powershell
uv run pytest
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add tests/test_api.py src/vmaf_viewer/app.py src/vmaf_viewer/static/app.js
git commit -m "fix: report viewer selection errors"
```

## Task 11: Add Run Documentation

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create README**

Create `README.md` with this content:

```markdown
# VMAF Compare

Local tools for comparing libvmaf JSON outputs from distorted video encodes.

## VMAF JSON Viewer

The viewer is a local web app managed with `uv`.

Install and sync dependencies:

```powershell
uv sync
```

Fetch the local ECharts asset:

```powershell
uv run python scripts/fetch_echarts.py
```

Run the viewer:

```powershell
uv run vmaf-viewer
```

Open:

```text
http://127.0.0.1:8765
```

By default, the app scans:

```text
videos/
```

Override the scan directory:

```powershell
$env:VMAF_VIEWER_DATA_DIR = "D:\path\to\vmaf-jsons"
uv run vmaf-viewer
```

The app compares selected `*_vmaf.json` files over their shortest common frame range and ranks videos by mean VMAF by default.

## Tests

Run all tests through `uv`:

```powershell
uv run pytest
```
```

- [ ] **Step 2: Verify README commands**

Run:

```powershell
uv run pytest
```

Expected: PASS.

Run:

```powershell
uv run python -c "from vmaf_viewer.app import create_app; print(create_app().title)"
```

Expected output:

```text
VMAF JSON Viewer
```

- [ ] **Step 3: Commit**

Run:

```powershell
git add README.md
git commit -m "docs: add viewer run instructions"
```

## Task 12: Final Verification

**Files:**
- No new files expected unless verification exposes a defect.

- [ ] **Step 1: Run complete automated tests**

Run:

```powershell
uv run pytest
```

Expected: PASS.

- [ ] **Step 2: Start the local app**

Run:

```powershell
uv run vmaf-viewer
```

Expected: Uvicorn serves `http://127.0.0.1:8765`.

- [ ] **Step 3: Browser smoke test**

Open `http://127.0.0.1:8765` and verify:

- the page loads without console errors
- scanned files appear when `videos/` contains `*_vmaf.json`
- selecting 2 or more files updates the selected count
- the summary table sorts by mean VMAF descending
- the warning area reports frame-count mismatch when selected files differ in length
- the per-frame chart shows threshold reference lines
- the zoom chart has a draggable dataZoom axis
- histogram tab shows frame-count bars
- CDF tab shows cumulative ratio lines
- hiding a video removes it from all charts
- enabling a sub-metric loads and renders dotted metric lines

- [ ] **Step 4: Stop server**

Stop the server with `Ctrl+C`.

- [ ] **Step 5: Check Git status**

Run:

```powershell
git status --short
```

Expected: no uncommitted changes.

## Self-Review

Spec coverage:

- Local service with `uv`: Task 1 and Task 11.
- Scan `videos/` for `*_vmaf.json`: Task 2 and Task 6.
- Common shortest-frame comparison: Task 5.
- Default ranking by mean VMAF: Task 5 and Task 8.
- Summary metrics mean/min/max/p1/p5/p10 and thresholds: Task 4 and Task 5.
- Per-frame line chart with threshold reference lines: Task 8.
- Local zoom chart with draggable axis: Task 8 and Task 9.
- Histogram and CDF: Task 4, Task 5, and Task 8.
- Hide videos and toggle sub-metrics: Task 8 and Task 9.
- Performance through caching, preaggregation, and downsampling: Task 4, Task 5, and Task 9.
- Error handling: Task 6 and Task 10.
- Tests with small fixtures: Tasks 2 through 6 and Task 10.

Placeholder scan:

- The plan avoids deferred implementation markers.
- Every file creation task includes concrete content or exact command output expectations.

Type consistency:

- `FileRecord`, `ParsedVmaf`, `VmafCache`, `compare_files`, and API property names are used consistently across tests, backend modules, and frontend payload access.

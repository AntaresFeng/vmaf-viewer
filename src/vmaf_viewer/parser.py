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


def _frame_num(item: dict, fallback: int, record: FileRecord) -> int:
    raw = item.get("frameNum", fallback)
    try:
        return int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VmafParseError(f"{record.relative_path} has invalid frameNum: {raw!r}") from exc


def parse_vmaf_file(record: FileRecord) -> ParsedVmaf:
    try:
        data = orjson.loads(record.path.read_bytes())
    except orjson.JSONDecodeError as exc:
        raise VmafParseError(f"Invalid JSON in {record.relative_path}") from exc

    frames = data.get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        raise VmafParseError(f"{record.relative_path} is missing frames")

    frame_numbers: list[int] = []
    metric_names: list[str] = []
    metric_seen: set[str] = set()

    for item in frames:
        metrics = item.get("metrics") if isinstance(item, dict) else None
        if not isinstance(metrics, dict):
            continue
        frame_numbers.append(_frame_num(item, len(frame_numbers), record))
        for name in metrics:
            if name not in metric_seen:
                metric_seen.add(name)
                metric_names.append(name)

    values: dict[str, list[float]] = {name: [] for name in metric_names}
    for item in frames:
        metrics = item.get("metrics") if isinstance(item, dict) else None
        if not isinstance(metrics, dict):
            continue
        for name in metric_names:
            raw = metrics.get(name)
            values[name].append(
                float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else math.nan
            )

    return ParsedVmaf(
        file=record,
        frame_numbers=frame_numbers,
        metrics=values,
        primary_metric=select_primary_metric(metric_names),
    )

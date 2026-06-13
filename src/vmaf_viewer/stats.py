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
        {
            "start": round(index * bucket_size, 6),
            "end": round(min((index + 1) * bucket_size, 100.0), 6),
            "count": 0,
        }
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
    if max_points < 2:
        raise ValueError("max_points must be at least 2")

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

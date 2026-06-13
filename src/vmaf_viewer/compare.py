from __future__ import annotations

import math

from .cache import VmafCache
from .models import FileRecord, ParsedVmaf
from .stats import build_cdf, build_histogram, downsample_series, finite_values, summarize_values


def _metric_for(parsed: ParsedVmaf, requested_metric: str | None) -> str | None:
    if requested_metric is None:
        return parsed.primary_metric
    if requested_metric in parsed.metrics:
        return requested_metric
    return None


def compare_files(
    records: list[FileRecord],
    cache: VmafCache,
    thresholds: list[float],
    metric: str | None = None,
    max_points: int = 2000,
) -> dict:
    if max_points < 2:
        raise ValueError("max_points must be at least 2")
    validated_thresholds = [float(threshold) for threshold in thresholds]
    if not all(math.isfinite(threshold) for threshold in validated_thresholds):
        raise ValueError("thresholds must be finite numbers")

    parsed_items: list[tuple[ParsedVmaf, str]] = []
    warnings: list[str] = []

    for record in records:
        parsed = cache.get(record)
        selected_metric = _metric_for(parsed, metric)
        if selected_metric is None:
            if metric is None:
                warnings.append(f"{record.name} has no VMAF score metric.")
            else:
                warnings.append(f"{record.name} is missing metric {metric}.")
            continue
        if selected_metric not in parsed.metrics:
            warnings.append(f"{record.name} is missing metric {selected_metric}.")
            continue
        parsed_items.append((parsed, selected_metric))

    finite_items: list[tuple[ParsedVmaf, str]] = []
    for parsed, metric_name in parsed_items:
        if finite_values(parsed.metrics[metric_name]):
            finite_items.append((parsed, metric_name))
        else:
            warnings.append(f"{parsed.file.name} has no finite values for metric {metric_name}.")
    parsed_items = finite_items

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

    while parsed_items:
        common_count = min(parsed.total_frames for parsed, _metric_name in parsed_items)
        common_finite_items: list[tuple[ParsedVmaf, str]] = []
        for parsed, metric_name in parsed_items:
            values = parsed.metrics[metric_name][:common_count]
            if finite_values(values):
                common_finite_items.append((parsed, metric_name))
            else:
                warnings.append(
                    f"{parsed.file.name} has no finite values for metric {metric_name} "
                    f"within the first {common_count} common frames."
                )
        if len(common_finite_items) == len(parsed_items):
            break
        parsed_items = common_finite_items

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
        stats = summarize_values(values, validated_thresholds)
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

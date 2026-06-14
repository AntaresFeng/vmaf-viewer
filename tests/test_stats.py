import math

import pytest

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


def test_summarize_values_handles_empty_or_all_nan_values():
    for values in ([], [math.nan, math.nan]):
        summary = summarize_values(values, [90.0])

        assert summary["count"] == 0
        assert math.isnan(summary["mean"])
        assert math.isnan(summary["min"])
        assert math.isnan(summary["max"])
        assert math.isnan(summary["p1"])
        assert summary["thresholds"][90.0] == {"count": 0, "ratio": 0.0}


def test_build_histogram_counts_values_in_shared_buckets():
    histogram = build_histogram([0.0, 0.5, 1.0, 99.9, 100.0], bucket_size=1.0)

    assert histogram[0] == {"start": 0.0, "end": 1.0, "count": 2}
    assert histogram[1] == {"start": 1.0, "end": 2.0, "count": 1}
    assert histogram[-1] == {"start": 99.0, "end": 100.0, "count": 2}


def test_build_histogram_and_cdf_clamp_final_bucket_end_to_100():
    histogram = build_histogram([100.0], bucket_size=30.0)
    cdf = build_cdf([100.0], bucket_size=30.0)

    assert histogram[-1] == {"start": 90.0, "end": 100.0, "count": 1}
    assert cdf == [{"score": 100.0, "ratio": 1.0}]


@pytest.mark.parametrize("builder", [build_histogram, build_cdf])
@pytest.mark.parametrize("bucket_size", [0.0, -1.0])
def test_bucketed_stats_reject_invalid_bucket_size(builder, bucket_size):
    with pytest.raises(ValueError, match="bucket_size must be positive"):
        builder([1.0], bucket_size=bucket_size)


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


@pytest.mark.parametrize("max_points", [0, 1])
def test_downsample_series_rejects_tiny_max_points(max_points):
    with pytest.raises(ValueError, match="max_points must be at least 2"):
        downsample_series([0, 1], [100.0, 99.0], max_points=max_points)

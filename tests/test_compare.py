from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from vmaf_viewer.cache import VmafCache
from vmaf_viewer.compare import compare_files
from vmaf_viewer.scanner import scan_vmaf_files


def _records():
    return scan_vmaf_files(Path("tests/fixtures"))


def _write_vmaf(path: Path, frames: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "fixture",
                "fps": 1,
                "frames": frames,
                "pooled_metrics": {},
            }
        ),
        encoding="utf-8",
    )


def test_compare_files_uses_each_files_full_samples_and_union_frame_domain():
    cache = VmafCache()
    result = compare_files(_records(), cache, thresholds=[95.0, 90.0, 80.0, 60.0])

    assert result["frame_domain"] == {"start": 0, "end": 4}
    assert [row["name"] for row in result["summary"]] == [
        "beta_vmaf.json",
        "alpha_vmaf.json",
    ]
    assert result["summary"][0]["stats"]["mean"] == 90.0
    assert result["summary"][0]["stats"]["count"] == 4
    assert result["summary"][1]["stats"]["mean"] == 86.6
    assert result["summary"][1]["stats"]["count"] == 5
    assert result["warnings"] == []


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


def test_compare_files_skips_files_missing_requested_metric_without_fallback(tmp_path):
    _write_vmaf(
        tmp_path / "has_hd_vmaf.json",
        [
            {"frameNum": 0, "metrics": {"vmaf": 12.0, "vmaf_hd": 91.0}},
            {"frameNum": 1, "metrics": {"vmaf": 13.0, "vmaf_hd": 89.0}},
        ],
    )
    _write_vmaf(
        tmp_path / "missing_hd_vmaf.json",
        [
            {"frameNum": 0, "metrics": {"vmaf": 99.0}},
            {"frameNum": 1, "metrics": {"vmaf": 98.0}},
        ],
    )
    records = scan_vmaf_files(tmp_path)

    result = compare_files(records, VmafCache(), thresholds=[90.0], metric="vmaf_hd")

    assert [row["name"] for row in result["summary"]] == ["has_hd_vmaf.json"]
    assert result["summary"][0]["metric"] == "vmaf_hd"
    assert set(result["series"]) == {records[0].id}
    assert result["warnings"] == ["missing_hd_vmaf.json is missing metric vmaf_hd."]


def test_compare_files_validates_max_points_before_parsing():
    with pytest.raises(ValueError, match="max_points must be at least 2"):
        compare_files([], VmafCache(), thresholds=[90.0], max_points=1)


@pytest.mark.parametrize("threshold", [math.nan, math.inf])
def test_compare_files_rejects_non_finite_thresholds(threshold):
    with pytest.raises(ValueError, match="thresholds must be finite numbers"):
        compare_files([], VmafCache(), thresholds=[threshold])


def test_compare_files_skips_metric_with_no_finite_values(tmp_path):
    _write_vmaf(
        tmp_path / "nan_vmaf.json",
        [
            {"frameNum": 0, "metrics": {"vmaf": "bad"}},
            {"frameNum": 1, "metrics": {"vmaf": None}},
            {"frameNum": 2, "metrics": {}},
        ],
    )

    result = compare_files(scan_vmaf_files(tmp_path), VmafCache(), thresholds=[90.0])

    assert result["summary"] == []
    assert result["series"] == {}
    assert result["histogram"] == {}
    assert result["cdf"] == {}
    assert result["frame_domain"] == {"start": None, "end": None}
    assert result["warnings"] == ["nan_vmaf.json has no finite values for metric vmaf."]


def test_compare_files_keeps_late_finite_values_beyond_shorter_file(tmp_path):
    _write_vmaf(
        tmp_path / "short_vmaf.json",
        [
            {"frameNum": 0, "metrics": {"vmaf": 91.0}},
            {"frameNum": 1, "metrics": {"vmaf": 92.0}},
        ],
    )
    _write_vmaf(
        tmp_path / "late_finite_vmaf.json",
        [
            {"frameNum": 0, "metrics": {"vmaf": None}},
            {"frameNum": 1, "metrics": {"vmaf": "bad"}},
            {"frameNum": 2, "metrics": {"vmaf": 88.0}},
        ],
    )

    result = compare_files(scan_vmaf_files(tmp_path), VmafCache(), thresholds=[90.0])

    assert [row["name"] for row in result["summary"]] == [
        "short_vmaf.json",
        "late_finite_vmaf.json",
    ]
    assert result["frame_domain"] == {"start": 0, "end": 2}
    assert result["summary"][1]["stats"]["count"] == 1
    assert result["summary"][1]["stats"]["mean"] == 88.0
    assert result["warnings"] == []


def test_compare_files_preserves_dense_and_subsampled_frame_numbers(tmp_path):
    _write_vmaf(
        tmp_path / "dense_vmaf.json",
        [
            {"frameNum": frame, "metrics": {"vmaf": 90.0 + frame}}
            for frame in range(6)
        ],
    )
    _write_vmaf(
        tmp_path / "subsampled_vmaf.json",
        [
            {"frameNum": frame, "metrics": {"vmaf": 80.0 + frame}}
            for frame in [0, 2, 4]
        ],
    )
    records = scan_vmaf_files(tmp_path)

    result = compare_files(records, VmafCache(), thresholds=[90.0], max_points=100)

    rows = {row["name"]: row for row in result["summary"]}
    series = {
        record.name: result["series"][record.id]["points"] for record in records
    }
    assert result["frame_domain"] == {"start": 0, "end": 5}
    assert rows["dense_vmaf.json"]["stats"]["count"] == 6
    assert rows["subsampled_vmaf.json"]["stats"]["count"] == 3
    assert [point[0] for point in series["dense_vmaf.json"]] == [0, 1, 2, 3, 4, 5]
    assert [point[0] for point in series["subsampled_vmaf.json"]] == [0, 2, 4]
    assert result["warnings"] == []


def test_vmaf_cache_reuses_parse_until_size_or_mtime_changes(tmp_path):
    path = tmp_path / "cached_vmaf.json"
    _write_vmaf(path, [{"frameNum": 0, "metrics": {"vmaf": 80.0}}])
    record = scan_vmaf_files(tmp_path)[0]
    cache = VmafCache()

    first = cache.get(record)

    assert cache.get(record) is first

    _write_vmaf(
        path,
        [
            {"frameNum": 0, "metrics": {"vmaf": 70.0}},
            {"frameNum": 1, "metrics": {"vmaf": 73.0}},
        ],
    )
    size_changed = replace(record, size=path.stat().st_size)
    by_size = cache.get(size_changed)

    assert by_size is not first
    assert by_size.metrics["vmaf"][0] == 70.0

    _write_vmaf(
        path,
        [
            {"frameNum": 0, "metrics": {"vmaf": 71.0}},
            {"frameNum": 1, "metrics": {"vmaf": 74.0}},
        ],
    )
    mtime_changed = replace(size_changed, mtime=size_changed.mtime + 1.0)
    by_mtime = cache.get(mtime_changed)

    assert by_mtime is not by_size
    assert by_mtime.metrics["vmaf"][0] == 71.0
    assert cache.get(mtime_changed) is by_mtime

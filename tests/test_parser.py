import math
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


def test_parse_vmaf_file_rejects_non_object_root_as_missing_frames(tmp_path):
    bad = tmp_path / "bad_vmaf.json"
    bad.write_text("[]", encoding="utf-8")
    record = scan_vmaf_files(tmp_path)[0]

    with pytest.raises(VmafParseError, match="missing frames"):
        parse_vmaf_file(record)


def test_parse_vmaf_file_rejects_invalid_frame_num(tmp_path):
    bad = tmp_path / "bad_vmaf.json"
    bad.write_text('{"frames":[{"frameNum":null,"metrics":{"vmaf":99}}]}', encoding="utf-8")
    record = scan_vmaf_files(tmp_path)[0]

    with pytest.raises(VmafParseError, match="bad_vmaf.json.*invalid frameNum"):
        parse_vmaf_file(record)


def test_parse_vmaf_file_skips_frames_without_metric_dict(tmp_path):
    fixture = tmp_path / "mixed_vmaf.json"
    fixture.write_text(
        """
        {
          "frames": [
            {"frameNum": 0, "metrics": {"vmaf": 95.0}},
            {"frameNum": 1},
            [],
            {"frameNum": 3, "metrics": null},
            {"frameNum": 4, "metrics": {"vmaf": 90.0}}
          ]
        }
        """,
        encoding="utf-8",
    )
    record = scan_vmaf_files(tmp_path)[0]

    parsed = parse_vmaf_file(record)

    assert parsed.frame_numbers == [0, 4]
    assert parsed.metrics["vmaf"] == [95.0, 90.0]
    assert all(len(values) == parsed.total_frames for values in parsed.metrics.values())


def test_parse_vmaf_file_treats_boolean_metrics_as_non_numeric(tmp_path):
    fixture = tmp_path / "bool_vmaf.json"
    fixture.write_text(
        """
        {
          "frames": [
            {
              "frameNum": 0,
              "metrics": {
                "vmaf": true,
                "integer_motion": 2,
                "float_metric": 3.5
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    record = scan_vmaf_files(tmp_path)[0]

    parsed = parse_vmaf_file(record)

    assert math.isnan(parsed.metrics["vmaf"][0])
    assert parsed.metrics["integer_motion"] == [2.0]
    assert parsed.metrics["float_metric"] == [3.5]

import json
import math
from pathlib import Path

import pytest

from vmaf_viewer.models import FileRecord
from vmaf_viewer.parser import (
    CsvVmafParser,
    JsonVmafParser,
    VmafParseError,
    VmafParserFactory,
    XmlVmafParser,
    parse_vmaf_file,
    select_primary_metric,
)
from vmaf_viewer.scanner import scan_vmaf_files


def _record(name: str):
    return next(
        record
        for record in scan_vmaf_files(Path("tests/fixtures"))
        if record.name == name
    )


def _record_for_path(path: Path) -> FileRecord:
    st = path.stat()
    return FileRecord(
        id=path.stem,
        name=path.name,
        path=path,
        relative_path=path.name,
        size=st.st_size,
        mtime=st.st_mtime,
    )


def test_parser_factory_selects_strategy_by_suffix():
    factory = VmafParserFactory()

    assert isinstance(factory.for_suffix(".json"), JsonVmafParser)
    assert isinstance(factory.for_suffix(".csv"), CsvVmafParser)
    assert isinstance(factory.for_suffix(".xml"), XmlVmafParser)
    assert isinstance(factory.for_suffix(".JSON"), JsonVmafParser)

    with pytest.raises(VmafParseError, match="Unsupported VMAF log format"):
        factory.for_suffix(".sub")


def test_select_primary_metric_prefers_vmaf():
    assert select_primary_metric(["integer_motion", "vmaf_hd", "vmaf"]) == "vmaf"


def test_select_primary_metric_falls_back_to_vmaf_hd():
    assert select_primary_metric(["integer_motion", "vmaf_hd"]) == "vmaf_hd"


def test_select_primary_metric_falls_back_to_vmaf_4k():
    assert select_primary_metric(["integer_motion", "vmaf_4k"]) == "vmaf_4k"


def test_select_primary_metric_vmaf_4k_beats_substring():
    assert select_primary_metric(["float_vmaf_custom", "vmaf_4k"]) == "vmaf_4k"


def test_select_primary_metric_falls_back_to_first_metric_containing_vmaf():
    assert (
        select_primary_metric(["integer_motion", "float_vmaf_custom"])
        == "float_vmaf_custom"
    )


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


@pytest.mark.parametrize("frame_num", [None, True, -1, 1.5, "1"])
def test_parse_vmaf_file_rejects_invalid_json_frame_num(tmp_path, frame_num):
    bad = tmp_path / "bad_vmaf.json"
    bad.write_text(
        json.dumps(
            {"frames": [{"frameNum": frame_num, "metrics": {"vmaf": 99}}]}
        ),
        encoding="utf-8",
    )
    record = scan_vmaf_files(tmp_path)[0]

    with pytest.raises(VmafParseError, match="bad_vmaf.json.*invalid frameNum"):
        parse_vmaf_file(record)


def test_parse_vmaf_file_rejects_missing_json_frame_num(tmp_path):
    bad = tmp_path / "missing_frame_num_vmaf.json"
    bad.write_text('{"frames":[{"metrics":{"vmaf":99}}]}', encoding="utf-8")

    with pytest.raises(VmafParseError, match="missing frameNum"):
        parse_vmaf_file(_record_for_path(bad))


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


def test_parse_vmaf_file_preserves_subsampled_json_frame_numbers(tmp_path):
    fixture = tmp_path / "subsampled_vmaf.json"
    fixture.write_text(
        '{"frames":['
        '{"frameNum":0,"metrics":{"vmaf":97}},'
        '{"frameNum":30,"metrics":{"vmaf":96}},'
        '{"frameNum":60,"metrics":{"vmaf":95}}'
        "]}",
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0, 30, 60]


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


def test_parse_vmaf_file_extracts_csv_frames_metrics_and_primary_metric(tmp_path):
    fixture = tmp_path / "alpha_vmaf.csv"
    fixture.write_text(
        "Frame,vmaf,integer_motion\n0,97.0,1.0\n1,96.0,1.5\n2,90.0,2.0\n",
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0, 1, 2]
    assert parsed.primary_metric == "vmaf"
    assert parsed.metrics["vmaf"] == [97.0, 96.0, 90.0]
    assert parsed.metrics["integer_motion"] == [1.0, 1.5, 2.0]


def test_parse_vmaf_file_treats_bad_csv_metric_cells_as_non_numeric(tmp_path):
    fixture = tmp_path / "mixed_vmaf.csv"
    fixture.write_text(
        "Frame,vmaf,integer_motion\n0,,1.0\n1,bad,2.0\n2,90.0,\n",
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert math.isnan(parsed.metrics["vmaf"][0])
    assert math.isnan(parsed.metrics["vmaf"][1])
    assert parsed.metrics["vmaf"][2] == 90.0
    assert parsed.metrics["integer_motion"][0] == 1.0
    assert parsed.metrics["integer_motion"][1] == 2.0
    assert math.isnan(parsed.metrics["integer_motion"][2])


def test_parse_vmaf_file_rejects_csv_missing_frame_num_column(tmp_path):
    fixture = tmp_path / "bad_vmaf.csv"
    fixture.write_text("vmaf\n99.0\n", encoding="utf-8")

    with pytest.raises(VmafParseError, match=r"missing 'Frame' column"):
        parse_vmaf_file(_record_for_path(fixture))


def test_parse_vmaf_file_rejects_non_utf8_csv_as_invalid_csv(tmp_path):
    fixture = tmp_path / "bad_vmaf.csv"
    fixture.write_bytes(b"Frame,vmaf\n0,\xff\n")

    with pytest.raises(VmafParseError, match="Invalid CSV"):
        parse_vmaf_file(_record_for_path(fixture))


def test_parse_vmaf_file_rejects_blank_csv_frame_num(tmp_path):
    fixture = tmp_path / "blank_frame_num_vmaf.csv"
    fixture.write_text("Frame,vmaf\n,97.0\n", encoding="utf-8")

    with pytest.raises(VmafParseError, match="invalid frameNum"):
        parse_vmaf_file(_record_for_path(fixture))


def test_parse_vmaf_file_preserves_subsampled_csv_frame_numbers(tmp_path):
    fixture = tmp_path / "subsampled_vmaf.csv"
    fixture.write_text(
        "Frame,vmaf\n0,97.0\n30,96.0\n60,95.0\n", encoding="utf-8"
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0, 30, 60]


def test_parse_vmaf_file_extracts_xml_frames_metrics_and_primary_metric(tmp_path):
    fixture = tmp_path / "alpha_vmaf.xml"
    fixture.write_text(
        """
        <VMAF version="fixture">
          <frames>
            <frame frameNum="0" vmaf="97.0" integer_motion="1.0"/>
            <frame frameNum="1" vmaf="96.0" integer_motion="1.5"/>
            <frame frameNum="2" vmaf="90.0" integer_motion="2.0"/>
          </frames>
        </VMAF>
        """,
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0, 1, 2]
    assert parsed.primary_metric == "vmaf"
    assert parsed.metrics["vmaf"] == [97.0, 96.0, 90.0]
    assert parsed.metrics["integer_motion"] == [1.0, 1.5, 2.0]


def test_parse_vmaf_file_rejects_missing_xml_frame_num(tmp_path):
    fixture = tmp_path / "missing_frame_num_vmaf.xml"
    fixture.write_text(
        """
        <VMAF version="fixture">
          <frames>
            <frame vmaf="97.0"/>
            <frame vmaf="96.0"/>
          </frames>
        </VMAF>
        """,
        encoding="utf-8",
    )

    with pytest.raises(VmafParseError, match="missing frameNum"):
        parse_vmaf_file(_record_for_path(fixture))


def test_parse_vmaf_file_preserves_subsampled_xml_frame_numbers(tmp_path):
    fixture = tmp_path / "subsampled_vmaf.xml"
    fixture.write_text(
        """
        <VMAF version="fixture">
          <frames>
            <frame frameNum="0" vmaf="97.0"/>
            <frame frameNum="30" vmaf="96.0"/>
            <frame frameNum="60" vmaf="95.0"/>
          </frames>
        </VMAF>
        """,
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0, 30, 60]


@pytest.mark.parametrize(
    ("frame_numbers", "message"),
    [([0, 0], "duplicate frameNum: 0"), ([1, 0], "out-of-order frameNum: 0 after 1")],
)
def test_parse_vmaf_file_rejects_non_increasing_frame_numbers(
    tmp_path, frame_numbers, message
):
    fixture = tmp_path / "bad_order_vmaf.json"
    fixture.write_text(
        json.dumps(
            {
                "frames": [
                    {"frameNum": frame_num, "metrics": {"vmaf": 99}}
                    for frame_num in frame_numbers
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(VmafParseError, match=message):
        parse_vmaf_file(_record_for_path(fixture))


def test_parse_vmaf_file_prefers_direct_xml_frames_container(tmp_path):
    fixture = tmp_path / "nested_frames_vmaf.xml"
    fixture.write_text(
        """
        <VMAF version="fixture">
          <metadata>
            <frames>
              <frame frameNum="99" vmaf="1.0"/>
            </frames>
          </metadata>
          <frames>
            <frame frameNum="0" vmaf="97.0"/>
          </frames>
        </VMAF>
        """,
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0]
    assert parsed.metrics["vmaf"] == [97.0]


def test_parse_vmaf_file_accepts_empty_xml_frames_container(tmp_path):
    fixture = tmp_path / "empty_vmaf.xml"
    fixture.write_text(
        """
        <VMAF version="fixture">
          <frames/>
        </VMAF>
        """,
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.total_frames == 0
    assert parsed.metrics == {}
    assert parsed.primary_metric is None


def test_parse_vmaf_file_accepts_namespaced_xml_by_local_name(tmp_path):
    fixture = tmp_path / "namespaced_vmaf.xml"
    fixture.write_text(
        """
        <v:VMAF xmlns:v="urn:vmaf" version="fixture">
          <v:frames>
            <v:frame frameNum="0" vmaf="91.0"/>
          </v:frames>
        </v:VMAF>
        """,
        encoding="utf-8",
    )

    parsed = parse_vmaf_file(_record_for_path(fixture))

    assert parsed.frame_numbers == [0]
    assert parsed.metrics["vmaf"] == [91.0]


def test_parse_vmaf_file_rejects_invalid_xml(tmp_path):
    fixture = tmp_path / "bad_vmaf.xml"
    fixture.write_text("<VMAF><frames>", encoding="utf-8")

    with pytest.raises(VmafParseError, match="Invalid XML"):
        parse_vmaf_file(_record_for_path(fixture))


def test_parse_vmaf_file_leaves_missing_xml_file_as_os_error(tmp_path):
    fixture = tmp_path / "missing_vmaf.xml"
    record = FileRecord(
        id="missing",
        name=fixture.name,
        path=fixture,
        relative_path=fixture.name,
        size=0,
        mtime=0,
    )

    with pytest.raises(OSError):
        parse_vmaf_file(record)

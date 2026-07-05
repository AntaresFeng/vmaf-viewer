from pathlib import Path

from vmaf_viewer.scanner import scan_vmaf_files


def test_scan_vmaf_files_finds_only_vmaf_json_files():
    root = Path("tests/fixtures")

    records = scan_vmaf_files(root)

    assert [record.name for record in records] == ["alpha_vmaf.json", "beta_vmaf.json"]
    assert all(record.path.name.endswith(".json") for record in records)
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


def test_scan_vmaf_files_finds_plain_json_without_vmaf_suffix(tmp_path):
    (tmp_path / "encode_001.json").write_text(
        '{"version":"1","fps":1,"frames":[],"pooled_metrics":{}}', encoding="utf-8"
    )

    records = scan_vmaf_files(tmp_path)

    assert len(records) == 1
    assert records[0].name == "encode_001.json"


def test_scan_vmaf_files_skips_dot_prefix_directories(tmp_path):
    (tmp_path / ".bilibili").mkdir(parents=True)
    (tmp_path / ".bilibili" / "temp.json").write_text(
        '{"version":"1","fps":1,"frames":[],"pooled_metrics":{}}', encoding="utf-8"
    )
    (tmp_path / "encode_002.json").write_text(
        '{"version":"1","fps":1,"frames":[],"pooled_metrics":{}}', encoding="utf-8"
    )

    records = scan_vmaf_files(tmp_path)

    names = [r.name for r in records]
    assert "temp.json" not in names
    assert "encode_002.json" in names


def test_scan_vmaf_files_finds_json_csv_and_xml_logs(tmp_path):
    for name in ["a.json", "b.csv", "c.xml", "ignored.txt"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    records = scan_vmaf_files(tmp_path)

    assert [record.name for record in records] == ["a.json", "b.csv", "c.xml"]

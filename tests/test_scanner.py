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

import os
from pathlib import Path
import platform

import pytest

from vmaf_viewer.scanner import scan_vmaf_files


def test_scan_vmaf_files_finds_only_vmaf_json_files():
    root = Path("tests/fixtures")

    records = scan_vmaf_files(root)

    assert [record.name for record in records] == ["alpha_vmaf.json", "beta_vmaf.json"]
    assert all(record.path.name.endswith(".json") for record in records)


def test_scan_vmaf_files_uses_stable_relative_id():
    root = Path("tests/fixtures")

    first = scan_vmaf_files(root)
    second = scan_vmaf_files(root)

    assert [record.id for record in first] == [record.id for record in second]
    assert first[0].relative_path == "alpha_vmaf.json"


def test_scan_vmaf_files_missing_directory_returns_empty_list(tmp_path):
    missing = tmp_path / "missing"

    assert scan_vmaf_files(missing) == []


def test_scan_vmaf_files_recursively_finds_logs_in_visible_directories(tmp_path):
    nested = tmp_path / "visible" / "nested" / "encode.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("", encoding="utf-8")

    records = scan_vmaf_files(tmp_path)

    assert [record.relative_path for record in records] == [
        "visible/nested/encode.json"
    ]


def test_scan_vmaf_files_skips_root_and_nested_dot_directories(tmp_path):
    paths = [
        tmp_path / ".hidden" / "root-secret.json",
        tmp_path / "visible" / ".bilibili" / "nested-secret.json",
        tmp_path / "visible" / "nested" / "result.json",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    records = scan_vmaf_files(tmp_path)

    assert [record.relative_path for record in records] == [
        "visible/nested/result.json"
    ]


def test_scan_vmaf_files_finds_json_csv_and_xml_logs(tmp_path):
    for name in ["a.json", "b.csv", "c.xml", "ignored.txt"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    records = scan_vmaf_files(tmp_path)

    assert [record.name for record in records] == ["a.json", "b.csv", "c.xml"]


@pytest.mark.skipif(
    platform.system() != "Windows",
    reason="os_sorted follows OS-native order; natural-order assertion is Windows-specific",
)
def test_scan_vmaf_files_uses_natural_sort(tmp_path):
    # encode10.json sorts before encode2.json under plain lexicographic order;
    # natural order (Windows Explorer / StrCmpLogicalW) puts encode2 first.
    for name in ["encode10.json", "encode2.json", "encode1.json"]:
        (tmp_path / name).write_text("{}", encoding="utf-8")

    records = scan_vmaf_files(tmp_path)

    assert [r.name for r in records] == [
        "encode1.json",
        "encode2.json",
        "encode10.json",
    ]


def test_scan_vmaf_files_skips_dangling_symlink(tmp_path):
    (tmp_path / "good.json").write_text("{}", encoding="utf-8")
    try:
        os.symlink(tmp_path / "missing.json", tmp_path / "dangling.json")
    except OSError:
        pytest.skip("creating symlinks requires privileges on Windows")

    records = scan_vmaf_files(tmp_path)

    assert [record.name for record in records] == ["good.json"]


def test_scan_vmaf_files_walks_tree_once(tmp_path, monkeypatch):
    for name in ["a.json", "b.csv", "c.xml"]:
        (tmp_path / name).write_text("{}", encoding="utf-8")

    calls = []
    original_walk = Path.walk

    def counting_walk(self, *args, **kwargs):
        calls.append(self)
        yield from original_walk(self, *args, **kwargs)

    monkeypatch.setattr(Path, "walk", counting_walk)

    records = scan_vmaf_files(tmp_path)

    assert len(calls) == 1
    assert len(records) == 3

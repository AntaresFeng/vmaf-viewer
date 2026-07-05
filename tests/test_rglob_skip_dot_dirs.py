"""Unit tests for rglob_skip_dot_dirs."""

from pathlib import Path

from vmaf_viewer.scanner import rglob_skip_dot_dirs


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_rglob_flat_json_matches_all_visible_json(tmp_path: Path):
    (tmp_path / "a.json").write_text("", encoding="utf-8")
    (tmp_path / "b.json").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")

    results = sorted(rglob_skip_dot_dirs(tmp_path, "*.json"))

    assert [p.name for p in results] == ["a.json", "b.json"]


def test_rglob_skips_dot_dir_at_root(tmp_path: Path):
    _touch(tmp_path / ".hidden" / "secret.json")
    _touch(tmp_path / "visible" / "ok.json")

    results = sorted(rglob_skip_dot_dirs(tmp_path, "**/*.json"))

    names = [p.name for p in results]
    assert "ok.json" in names
    assert "secret.json" not in names


def test_rglob_skips_dot_dir_nested(tmp_path: Path):
    _touch(tmp_path / "visible" / ".bilibili" / "temp.json")
    _touch(tmp_path / "visible" / "result.json")

    results = sorted(rglob_skip_dot_dirs(tmp_path, "**/*.json"))

    names = [p.name for p in results]
    assert "result.json" in names
    assert "temp.json" not in names


def test_rglob_relaxed_filename_no_vmaf_suffix(tmp_path: Path):
    _touch(tmp_path / "plain.json")

    results = list(rglob_skip_dot_dirs(tmp_path, "*.json"))

    assert len(results) == 1
    assert results[0].name == "plain.json"


def test_rglob_subdir_pattern(tmp_path: Path):
    _touch(tmp_path / "visible" / "result.json")
    _touch(tmp_path / "other" / "noise.json")

    results = sorted(rglob_skip_dot_dirs(tmp_path, "visible/*.json"))

    assert len(results) == 1
    assert results[0].name == "result.json"


def test_rglob_star_star_pattern(tmp_path: Path):
    _touch(tmp_path / "visible" / "result.json")
    _touch(tmp_path / "other" / "nested" / "deep.json")
    _touch(tmp_path / ".hidden" / "nope.json")

    results = sorted(rglob_skip_dot_dirs(tmp_path, "**/*.json"))

    names = [p.name for p in results]
    assert names == ["deep.json", "result.json"]


def test_rglob_case_sensitive_flag(tmp_path: Path):
    """Files that differ only in extension case are distinct on a
    case-sensitive filesystem; on a case-insensitive filesystem the second
    write overwrites the first, so use different base names to avoid that."""
    _touch(tmp_path / "a.json")
    _touch(tmp_path / "b.JSON")

    # Force case-insensitive matching — both extensions should match
    results_ci = sorted(rglob_skip_dot_dirs(tmp_path, "*.json", case_sensitive=False))
    assert len(results_ci) == 2

    # Force case-sensitive matching — only .json (lowercase) should match
    results_cs = sorted(rglob_skip_dot_dirs(tmp_path, "*.json", case_sensitive=True))
    assert len(results_cs) == 1
    assert results_cs[0].suffix == ".json"

import pytest

from vmaf_viewer.app import _select_startup_data_dir, create_app
from vmaf_viewer.cache import (
    DEFAULT_CACHE_MAX_BYTES,
    DEFAULT_CACHE_MIN_ENTRIES,
    MIB,
)


def test_select_startup_data_dir_prefers_flag_over_positional_and_env(tmp_path):
    flag_dir = tmp_path / "flag"
    positional_dir = tmp_path / "positional"
    env_dir = tmp_path / "env"

    assert (
        _select_startup_data_dir(
            flag_data_dir=str(flag_dir),
            positional_data_dir=str(positional_dir),
            environ={"VMAF_VIEWER_DATA_DIR": str(env_dir)},
            cwd=tmp_path,
        )
        == flag_dir
    )


def test_select_startup_data_dir_prefers_positional_over_env(tmp_path):
    positional_dir = tmp_path / "positional"
    env_dir = tmp_path / "env"

    assert (
        _select_startup_data_dir(
            flag_data_dir=None,
            positional_data_dir=str(positional_dir),
            environ={"VMAF_VIEWER_DATA_DIR": str(env_dir)},
            cwd=tmp_path,
        )
        == positional_dir
    )


def test_select_startup_data_dir_uses_env_then_default(tmp_path):
    env_dir = tmp_path / "env"

    assert (
        _select_startup_data_dir(
            flag_data_dir=None,
            positional_data_dir=None,
            environ={"VMAF_VIEWER_DATA_DIR": str(env_dir)},
            cwd=tmp_path,
        )
        == env_dir
    )
    assert (
        _select_startup_data_dir(
            flag_data_dir=None,
            positional_data_dir=None,
            environ={},
            cwd=tmp_path,
        )
        == tmp_path / "videos"
    )


def test_create_app_uses_default_cache_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("VMAF_VIEWER_CACHE_MAX_MIB", raising=False)
    monkeypatch.delenv("VMAF_VIEWER_CACHE_MIN_ENTRIES", raising=False)

    cache = create_app(data_dir=tmp_path).state.vmaf_viewer.cache

    assert cache.max_bytes == DEFAULT_CACHE_MAX_BYTES
    assert cache.min_entries == DEFAULT_CACHE_MIN_ENTRIES


def test_create_app_reads_cache_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("VMAF_VIEWER_CACHE_MAX_MIB", "12")
    monkeypatch.setenv("VMAF_VIEWER_CACHE_MIN_ENTRIES", "3")

    cache = create_app(data_dir=tmp_path).state.vmaf_viewer.cache

    assert cache.max_bytes == 12 * MIB
    assert cache.min_entries == 3


def test_create_app_explicit_cache_settings_override_environment(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("VMAF_VIEWER_CACHE_MAX_MIB", "12")
    monkeypatch.setenv("VMAF_VIEWER_CACHE_MIN_ENTRIES", "3")

    cache = create_app(
        data_dir=tmp_path,
        cache_max_bytes=20 * MIB,
        cache_min_entries=4,
    ).state.vmaf_viewer.cache

    assert cache.max_bytes == 20 * MIB
    assert cache.min_entries == 4


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("VMAF_VIEWER_CACHE_MAX_MIB", "nope", "must be an integer"),
        ("VMAF_VIEWER_CACHE_MAX_MIB", "0", "must be a positive integer"),
        (
            "VMAF_VIEWER_CACHE_MIN_ENTRIES",
            "-1",
            "must be a non-negative integer",
        ),
    ],
)
def test_create_app_rejects_invalid_cache_environment(
    monkeypatch, tmp_path, name: str, value: str, message: str
):
    monkeypatch.delenv("VMAF_VIEWER_CACHE_MAX_MIB", raising=False)
    monkeypatch.delenv("VMAF_VIEWER_CACHE_MIN_ENTRIES", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        create_app(data_dir=tmp_path)

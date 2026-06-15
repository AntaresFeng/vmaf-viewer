from pathlib import Path

from vmaf_viewer.app import _select_startup_data_dir


def test_select_startup_data_dir_prefers_flag_over_positional_and_env(tmp_path):
    flag_dir = tmp_path / "flag"
    positional_dir = tmp_path / "positional"
    env_dir = tmp_path / "env"

    assert _select_startup_data_dir(
        flag_data_dir=str(flag_dir),
        positional_data_dir=str(positional_dir),
        environ={"VMAF_VIEWER_DATA_DIR": str(env_dir)},
        cwd=tmp_path,
    ) == flag_dir


def test_select_startup_data_dir_prefers_positional_over_env(tmp_path):
    positional_dir = tmp_path / "positional"
    env_dir = tmp_path / "env"

    assert _select_startup_data_dir(
        flag_data_dir=None,
        positional_data_dir=str(positional_dir),
        environ={"VMAF_VIEWER_DATA_DIR": str(env_dir)},
        cwd=tmp_path,
    ) == positional_dir


def test_select_startup_data_dir_uses_env_then_default(tmp_path):
    env_dir = tmp_path / "env"

    assert _select_startup_data_dir(
        flag_data_dir=None,
        positional_data_dir=None,
        environ={"VMAF_VIEWER_DATA_DIR": str(env_dir)},
        cwd=tmp_path,
    ) == env_dir
    assert _select_startup_data_dir(
        flag_data_dir=None,
        positional_data_dir=None,
        environ={},
        cwd=tmp_path,
    ) == tmp_path / "videos"

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vmaf_workflow.cli import main
from vmaf_workflow.models import CommandResult

YTDLP_PREFLIGHT_JSON = (
    '{"formats":[{"format_id":"299","format_note":"1080p60",'
    '"resolution":"1920x1080","width":1920,"height":1080,'
    '"vcodec":"avc1.64002a","acodec":"none","fps":60,'
    '"vbr":5325.871,"filesize_approx":125179937,"ext":"mp4",'
    '"protocol":"https","container":"mp4_dash"}],'
    '"requested_downloads":[{"format_id":"299",'
    '"format_note":"1080p60","resolution":"1920x1080",'
    '"width":1920,"height":1080,"vcodec":"avc1.64002a",'
    '"acodec":"none","fps":60,"vbr":5325.871,'
    '"filesize_approx":125179937,"ext":"mp4",'
    '"protocol":"https","container":"mp4_dash"}]}'
)


def test_download_requires_at_least_one_source(tmp_path: Path, capsys) -> None:
    result = main(["download", "--videos-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 2
    assert "at least one" in captured.err


def test_download_dry_run_creates_project_configs_and_manifest(
    tmp_path: Path,
) -> None:
    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--bvid",
            "https://www.bilibili.com/video/BV1xx411c7mD/?p=2",
            "--ytid",
            "https://youtu.be/dQw4w9WgXcQ?si=abc",
            "--dry-run",
        ]
    )

    assert result == 0

    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    assert workflow_dir.is_dir()
    assert (workflow_dir / "bbdown.config").is_file()
    assert (workflow_dir / "yt-dlp.conf").is_file()

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_dir"] == str(project_dir)
    assert manifest["workflow_dir"] == str(workflow_dir)
    assert manifest["dry_run"] is True
    assert manifest["config_files"] == {
        "bbdown": str(workflow_dir / "bbdown.config"),
        "yt_dlp": str(workflow_dir / "yt-dlp.conf"),
    }
    assert manifest["bilibili"]["bvid"] == "BV1xx411c7mD"
    assert manifest["youtube"]["url"] == ("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert manifest["commands"] == []


def test_download_dry_run_uses_explicit_project_dir_without_next_video(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "manual-project"
    project_dir.mkdir()
    existing_file = project_dir / "already-downloaded.mp4"
    existing_file.write_text("keep", encoding="utf-8")

    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path / "videos"),
            "--project-dir",
            str(project_dir),
            "--bvid",
            "BV1xx411c7mD",
            "--dry-run",
        ]
    )

    assert result == 0
    workflow_dir = project_dir / ".workflow"
    assert (workflow_dir / "bbdown.config").is_file()
    assert (workflow_dir / "yt-dlp.conf").is_file()
    assert existing_file.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "videos" / "video0").exists()

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_dir"] == str(project_dir)
    assert manifest["workflow_dir"] == str(workflow_dir)


def test_download_help_includes_project_dir(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["download", "--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "--project-dir" in captured.out


def test_download_invalid_bvid_fails_without_creating_project(
    tmp_path: Path, capsys
) -> None:
    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--bvid",
            "not-a-bvid",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "BVID" in captured.err
    assert not (tmp_path / "video0").exists()


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        joined = " ".join(argv)
        if "-info" in argv:
            return CommandResult(
                tuple(argv),
                0,
                (
                    "0. [1080P 高帧率] [1920x1080] [AVC] "
                    "[60.000] [4200 kbps] [~100.00 MB]\n"
                ),
                "",
                stdin,
            )
        if "yt-dlp.exe" in joined and "-J" in argv:
            return CommandResult(
                tuple(argv),
                0,
                YTDLP_PREFLIGHT_JSON,
                "",
                stdin,
            )
        if "yt-dlp.exe" in joined and "--config-locations" in argv:
            _write_after_video_metadata(argv, YTDLP_PREFLIGHT_JSON)
            return CommandResult(tuple(argv), 0, "", "", stdin)
        return CommandResult(tuple(argv), 0, "", "", stdin)


def test_download_uses_runner_for_bilibili_and_youtube(tmp_path: Path) -> None:
    runner = FakeRunner()

    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--bvid",
            "BV1xx411c7mD",
            "--ytid",
            "dQw4w9WgXcQ",
        ],
        runner=runner,
    )

    assert result == 0
    assert any("-info" in argv for argv, _stdin in runner.calls)
    assert any(stdin == "0\n" for _argv, stdin in runner.calls)
    assert any("yt-dlp.exe" in " ".join(argv) for argv, _stdin in runner.calls)

    manifest = json.loads(
        (tmp_path / "video0" / ".workflow" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["dry_run"] is False
    assert manifest["bilibili"]["download_plan"][0]["source"] == "bilibili"
    assert manifest["youtube"]["download_plan"][0]["format_id"] == "299"
    assert manifest["youtube"]["download_plan"][0]["bitrate_kbps"] == 5325.871
    assert len(manifest["commands"]) >= 4


class InvalidYtDlpJsonRunner:
    def __init__(self, stdout: str = "not-json") -> None:
        self.calls = []
        self.stdout = stdout

    def run(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if "-J" in argv:
            return CommandResult(tuple(argv), 0, self.stdout, "", stdin)
        return CommandResult(tuple(argv), 0, "", "", stdin)


def test_download_records_youtube_preflight_json_failure_in_manifest(
    tmp_path: Path,
) -> None:
    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--ytid",
            "dQw4w9WgXcQ",
        ],
        runner=InvalidYtDlpJsonRunner(),
    )

    assert result == 1
    manifest = json.loads(
        (tmp_path / "video0" / ".workflow" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["youtube"]["downloads"] == [
        {
            "downloader": "yt-dlp",
            "stream": None,
            "status": "failed",
            "reason": "youtube_preflight_json_invalid",
            "command": manifest["commands"][0],
        }
    ]


def test_download_records_youtube_preflight_non_object_json_failure(
    tmp_path: Path,
) -> None:
    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--ytid",
            "dQw4w9WgXcQ",
        ],
        runner=InvalidYtDlpJsonRunner("[]"),
    )

    assert result == 1
    manifest = json.loads(
        (tmp_path / "video0" / ".workflow" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["youtube"]["downloads"][0]["reason"] == (
        "youtube_preflight_json_invalid"
    )


class YtDlpNoMetadataRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if "-J" in argv:
            return CommandResult(tuple(argv), 0, YTDLP_PREFLIGHT_JSON, "", stdin)
        return CommandResult(tuple(argv), 0, "", "", stdin)


def test_download_does_not_mark_youtube_plan_downloaded_without_metadata(
    tmp_path: Path,
) -> None:
    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--ytid",
            "dQw4w9WgXcQ",
        ],
        runner=YtDlpNoMetadataRunner(),
    )

    assert result == 1
    manifest = json.loads(
        (tmp_path / "video0" / ".workflow" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["youtube"]["download_plan"][0]["format_id"] == "299"
    assert manifest["youtube"]["downloads"] == [
        {
            "downloader": "yt-dlp",
            "stream": None,
            "status": "failed",
            "reason": "youtube_download_metadata_missing",
            "command": manifest["commands"][1],
        }
    ]


class MultiBilibiliRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        if "-info" in argv:
            return CommandResult(
                tuple(argv),
                0,
                "\n".join(
                    [
                        "0. [1080P 高清] [1920x1080] [AVC] [30.000] [3000 kbps] [~100.00 MB]",
                        "1. [1080P 高清] [1920x1080] [HEVC] [30.000] [2500 kbps] [~90.00 MB]",
                    ]
                ),
                "",
                stdin,
            )
        return CommandResult(tuple(argv), 0, "", "", stdin)


def test_download_rechecks_bilibili_info_once_for_all_planned_streams(
    tmp_path: Path,
) -> None:
    runner = MultiBilibiliRunner()

    result = main(
        [
            "download",
            "--videos-dir",
            str(tmp_path),
            "--bvid",
            "BV1xx411c7mD",
        ],
        runner=runner,
    )

    assert result == 0
    info_calls = [argv for argv, _stdin in runner.calls if "-info" in argv]
    assert len(info_calls) == 2
    assert [stdin for _argv, stdin in runner.calls if stdin is not None] == [
        "0\n",
        "1\n",
    ]


def _write_after_video_metadata(argv, payload: str) -> None:
    config_path = Path(argv[argv.index("--config-locations") + 1])
    prefix = "--print-to-file after_video:%()j "
    for line in config_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            Path(line.removeprefix(prefix)).write_text(payload, encoding="utf-8")
            return
    raise AssertionError("yt-dlp config did not include after_video metadata path")

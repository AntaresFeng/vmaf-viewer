from __future__ import annotations

import json
from pathlib import Path

import pytest

from vmaf_workflow.cli import main
from vmaf_workflow.models import CommandResult
from vmaf_workflow.remote_state import sha256_file
from vmaf_workflow.remote_workflow import RemoteRunInterrupted

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


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []
        self.encoding_calls = []

    def run(self, argv, stdin=None, *, output_encoding="utf-8"):
        self.calls.append((list(argv), stdin))
        self.encoding_calls.append((list(argv), output_encoding))
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


class FailIfCalledRunner:
    def run(self, argv, stdin=None):
        raise AssertionError(f"runner must not be called: {argv}")


def test_download_uses_runner_for_bilibili_and_youtube(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "vmaf_workflow.download.console_output_encoding",
        lambda: "cp936",
    )
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
    assert {
        encoding for argv, encoding in runner.encoding_calls if "BBDown.exe" in argv[0]
    } == {"cp936"}
    assert {
        encoding for argv, encoding in runner.encoding_calls if "yt-dlp.exe" in argv[0]
    } == {"utf-8"}

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


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--bvid", "BV1Q541167Qg", "BVID"),
        ("--ytid", "9bZkp7q19f0", "YouTube"),
    ],
)
def test_incremental_download_rejects_conflicting_source_atomically(
    tmp_path: Path,
    capsys,
    flag: str,
    value: str,
    message: str,
) -> None:
    project_dir = tmp_path / "video0"
    assert (
        main(
            [
                "download",
                "--project-dir",
                str(project_dir),
                "--bvid",
                "BV1xx411c7mD",
                "--ytid",
                "dQw4w9WgXcQ",
                "--dry-run",
            ]
        )
        == 0
    )
    before = _project_file_bytes(project_dir)

    result = main(
        ["download", "--project-dir", str(project_dir), flag, value],
        runner=FailIfCalledRunner(),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert message in captured.err
    assert _project_file_bytes(project_dir) == before


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


def test_remote_plan_generates_json_script_and_manifest_pointer(
    tmp_path: Path,
    capsys,
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "ref movie.mp4",
                "files": [
                    {
                        "path": "ref movie.mp4",
                        "role": "reference",
                        "width": 2160,
                        "height": 1080,
                        "resolution": "2160x1080",
                        "size_bytes": 10,
                    },
                    {
                        "path": "普通 1080.mp4",
                        "role": "distorted",
                        "width": 1920,
                        "height": 1080,
                        "resolution": "1920x1080",
                        "size_bytes": 20,
                    },
                    {
                        "path": "clip-1600.mp4",
                        "role": "distorted",
                        "width": 2560,
                        "height": 1600,
                        "resolution": "2560x1600",
                        "size_bytes": 30,
                    },
                    {
                        "path": "clip-2160p.webm",
                        "role": "distorted",
                        "size_bytes": 40,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "package-manifest.json").write_text(
        json.dumps(
            {
                "archive_path": str(workflow_dir / "video0-inputs.tar"),
                "archive_root": "video0",
                "media_files": [
                    {
                        "path": "ref movie.mp4",
                        "role": "reference",
                        "size_bytes": 10,
                    },
                    {
                        "path": "普通 1080.mp4",
                        "role": "distorted",
                        "size_bytes": 20,
                    },
                    {
                        "path": "clip-1600.mp4",
                        "role": "distorted",
                        "size_bytes": 30,
                    },
                    {
                        "path": "clip-2160p.webm",
                        "role": "distorted",
                        "size_bytes": 40,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "video0-inputs.tar").write_bytes(b"package")
    _add_package_inventory_hash(workflow_dir)
    (workflow_dir / "manifest.json").write_text(
        json.dumps({"keep": "existing"}),
        encoding="utf-8",
    )

    result = main(
        [
            "remote-plan",
            "--project-dir",
            str(project_dir),
            "--easyvmaf-repo",
            "/opt/easy Vmaf",
        ]
    )

    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "warning: resolution differs" in captured.err
    remote_plan_path = workflow_dir / "remote-plan.json"
    remote_script_path = workflow_dir / "remote-plan.sh"
    assert remote_plan_path.is_file()
    assert remote_script_path.is_file()

    remote_plan = json.loads(remote_plan_path.read_text(encoding="utf-8"))
    assert remote_plan["easyvmaf_repo"] == "/opt/easy Vmaf"
    assert remote_plan["package_archive"] == "video0-inputs.tar"
    assert remote_plan["result_archive"] == "video0-json.tar.gz"
    assert remote_plan["result_provenance"] == "vmaf-workflow-provenance.json"
    assert remote_plan["environment_preflight_argument"] == "--environment-only"
    assert remote_plan["preflight_argument"] == "--preflight-only"
    assert remote_plan["requirements"] == {
        "ffmpeg": {
            "minimum_major": 5,
            "required_filter": "libvmaf",
            "required_filters": ["libvmaf", "drawbox"],
        },
        "ffprobe": {"minimum_major": 5},
        "easyvmaf": {
            "repo": "/opt/easy Vmaf",
            "executable": "/opt/easy Vmaf/.venv/bin/easyvmaf",
            "required_branch": "master",
            "required_option": "-pre_filter",
        },
    }
    assert remote_plan["reference"]["path"] == "ref movie.mp4"
    assert [command["model"] for command in remote_plan["commands"]] == [
        "HD",
        "4K",
        "4K",
    ]
    assert [command["distorted"]["path"] for command in remote_plan["commands"]] == [
        "普通 1080.mp4",
        "clip-1600.mp4",
        "clip-2160p.webm",
    ]
    assert any("resolution differs" in warning for warning in remote_plan["warnings"])
    assert remote_plan["expected_results"] == [
        "video0/普通 1080_vmaf.json",
        "video0/clip-1600_vmaf.json",
        "video0/clip-2160p_vmaf.json",
    ]
    assert [
        command["expected_result"] for command in remote_plan["commands"]
    ] == remote_plan["expected_results"]

    script = remote_script_path.read_text(encoding="utf-8")
    script_bytes = remote_script_path.read_bytes()
    assert b"\r\n" not in script_bytes
    assert script_bytes.startswith(b"#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "set -euo pipefail" in script
    assert "export PATH=" not in script
    assert "export LD_LIBRARY_PATH=" not in script
    assert "require_command ffmpeg" in script
    assert "require_command ffprobe" in script
    assert "require_command git" in script
    assert "require_command sha256sum" in script
    assert "check_version ffmpeg 5" in script
    assert "check_version ffprobe 5" in script
    assert "ffmpeg -hide_banner -h filter=libvmaf" in script
    assert "ffmpeg -hide_banner -h filter=drawbox" in script
    assert "EASYVMAF_REPO='/opt/easy Vmaf'" in script
    assert "EASYVMAF_EXECUTABLE='/opt/easy Vmaf/.venv/bin/easyvmaf'" in script
    assert "EASYVMAF_REQUIRED_BRANCH=master" in script
    assert "MODE=${1:-run}" in script
    assert "usage: $0 [--environment-only|--preflight-only]" in script
    assert 'easyvmaf_help=$("$EASYVMAF_EXECUTABLE" --help' in script
    assert "easyVmaf does not provide required -pre_filter option" in script
    assert 'git -C "$EASYVMAF_REPO" symbolic-ref --quiet --short HEAD' in script
    assert "easyVmaf repository is in detached HEAD state" in script
    assert "easyVmaf branch mismatch: expected" in script
    assert 'info "easyVmaf branch: $easyvmaf_branch"' in script
    assert 'git -C "$EASYVMAF_REPO" rev-parse --short HEAD' in script
    assert "if [[ $MODE == --environment-only ]]" in script
    assert 'info "environment preflight complete"' in script
    assert '[[ -f "$PACKAGE_ARCHIVE" ]]' in script
    assert '[[ -f "$PROVENANCE_FILE" ]]' in script
    assert 'tar -tf "$PACKAGE_ARCHIVE"' in script
    assert "if [[ $MODE == --preflight-only ]]" in script
    assert 'info "preflight complete"' in script
    assert 'tar -xf "$PACKAGE_ARCHIVE"' in script
    assert "vmaf-workflow-provenance.json" in script
    assert '"$PROVENANCE_FILE"' in script
    assert script.index("if [[ $MODE == --environment-only ]]") < script.index(
        '[[ -f "$PACKAGE_ARCHIVE" ]]'
    )
    assert script.count('"$EASYVMAF_EXECUTABLE" -d ') == 3
    assert "-d 'video0/ref movie.mp4'" not in script
    assert "-d 'video0/普通 1080.mp4' -r 'video0/ref movie.mp4'" in script
    assert "-model HD" in script
    assert "-model 4K" in script
    assert "[[ -f 'video0/ref movie.mp4' ]]" in script
    assert "rm -f -- 'video0/普通 1080_vmaf.json'" in script
    assert "[[ -s 'video0/普通 1080_vmaf.json' ]]" in script
    assert "video0/*.json" not in script
    assert (
        "tar -czf \"$RESULT_ARCHIVE\" -- 'video0/普通 1080_vmaf.json' "
        "video0/clip-1600_vmaf.json video0/clip-2160p_vmaf.json"
    ) in script
    assert 'tar -tzf "$RESULT_ARCHIVE"' in script

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["keep"] == "existing"
    assert manifest["remote_plan"] == {
        "manifest": str(remote_plan_path),
        "script": str(remote_script_path),
    }


def test_status_maps_damaged_json_to_exit_code_2(
    tmp_path: Path,
    capsys,
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (project_dir / "distorted.mp4").write_bytes(b"media")
    (workflow_dir / "media-inventory.json").write_text(
        "not-json",
        encoding="utf-8",
    )

    result = main(["status", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "vmaf-workflow status" in captured.err
    assert "media-inventory.json" in captured.err


def test_run_maps_interrupt_to_exit_code_130(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "vmaf_workflow.cli.run_remote_project",
        lambda *_args: (_ for _ in ()).throw(RemoteRunInterrupted()),
    )

    result = main(
        ["run", "--project-dir", str(tmp_path / "video0")],
        runner=object(),
    )

    captured = capsys.readouterr()
    assert result == 130
    assert "interrupted" in captured.err


def _add_package_inventory_hash(workflow_dir: Path) -> None:
    manifest_path = workflow_dir / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inventory_sha256"] = sha256_file(workflow_dir / "media-inventory.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _write_after_video_metadata(argv, payload: str) -> None:
    config_path = Path(argv[argv.index("--config-locations") + 1])
    prefix = "--print-to-file after_video:%()j "
    for line in config_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            Path(line.removeprefix(prefix)).write_text(payload, encoding="utf-8")
            return
    raise AssertionError("yt-dlp config did not include after_video metadata path")


def _project_file_bytes(project_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project_dir).as_posix(): path.read_bytes()
        for path in project_dir.rglob("*")
        if path.is_file()
    }

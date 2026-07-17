from __future__ import annotations

import json
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from vmaf_workflow.cleanup import CleanupExecutionError, CleanupStateError
from vmaf_workflow.cli import main
from vmaf_workflow.config import EasyVmafSettings, RemoteSettings
from vmaf_workflow.models import CommandResult
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_plan import write_remote_plan
from vmaf_workflow.remote_workflow import (
    RemoteCommandError,
    RemoteRunInterrupted,
    RemoteWorkflowError,
)

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


def test_prepare_requires_reference(tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "video0"
    project_dir.mkdir()

    result = main(["prepare", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "reference" in captured.err
    assert not (project_dir / ".workflow" / "media-inventory.json").exists()


def test_prepare_records_reference_inside_project_and_excludes_workflow_files(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    temp_dir = project_dir / ".yt-dlp-temp"
    workflow_dir.mkdir(parents=True)
    temp_dir.mkdir()
    reference = project_dir / "reference.mp4"
    distorted = project_dir / "distorted.webm"
    ignored_workflow = workflow_dir / "cached.mp4"
    ignored_temp = temp_dir / "partial.mkv"
    reference.write_bytes(b"reference")
    distorted.write_bytes(b"distorted")
    ignored_workflow.write_bytes(b"ignore")
    ignored_temp.write_bytes(b"ignore")

    result = main(
        [
            "prepare",
            "--project-dir",
            str(project_dir),
            "--reference",
            str(reference),
        ]
    )

    assert result == 0
    inventory_path = workflow_dir / "media-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    files = {entry["path"]: entry for entry in inventory["files"]}
    assert sorted(files) == ["distorted.webm", "reference.mp4"]
    assert files["reference.mp4"]["role"] == "reference"
    assert files["reference.mp4"]["size_bytes"] == len(b"reference")
    assert files["reference.mp4"]["suffix"] == ".mp4"
    assert files["distorted.webm"]["role"] == "distorted"
    assert inventory["reference"] == "reference.mp4"

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["reference"] == {"path": "reference.mp4"}
    assert manifest["media_inventory"] == str(inventory_path)


def test_prepare_copies_external_reference_and_preserves_manifest(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    external_reference = tmp_path / "source-reference.mov"
    external_reference.write_bytes(b"external-ref")
    (project_dir / "encode.mkv").write_bytes(b"distorted")
    (workflow_dir / "manifest.json").write_text(
        json.dumps({"bilibili": {"bvid": "BV1xx411c7mD"}}),
        encoding="utf-8",
    )

    result = main(
        [
            "prepare",
            "--project-dir",
            str(project_dir),
            "--reference",
            str(external_reference),
        ]
    )

    assert result == 0
    copied_reference = project_dir / "source-reference.mov"
    assert copied_reference.read_bytes() == b"external-ref"

    inventory = json.loads(
        (workflow_dir / "media-inventory.json").read_text(encoding="utf-8")
    )
    files = {entry["path"]: entry for entry in inventory["files"]}
    assert files["source-reference.mov"]["role"] == "reference"
    assert files["encode.mkv"]["role"] == "distorted"

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bilibili"] == {"bvid": "BV1xx411c7mD"}
    assert manifest["reference"] == {"path": "source-reference.mov"}
    assert manifest["media_inventory"] == str(workflow_dir / "media-inventory.json")


def test_prepare_missing_reference_path_errors_without_inventory(
    tmp_path: Path, capsys
) -> None:
    project_dir = tmp_path / "video0"
    project_dir.mkdir()

    result = main(
        [
            "prepare",
            "--project-dir",
            str(project_dir),
            "--reference",
            str(tmp_path / "missing.mp4"),
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "reference" in captured.err
    assert not (project_dir / ".workflow" / "media-inventory.json").exists()


def test_package_creates_tar_from_inventory_and_records_manifest(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    nested_dir = project_dir / "nested"
    nested_dir.mkdir(parents=True)
    workflow_dir.mkdir()
    (project_dir / "reference.mp4").write_bytes(b"reference")
    (nested_dir / "encode.webm").write_bytes(b"distorted")
    (project_dir / "unlisted.mp4").write_bytes(b"do-not-package")
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "reference.mp4",
                "files": [
                    {
                        "path": "reference.mp4",
                        "role": "reference",
                        "size_bytes": len(b"reference"),
                        "suffix": ".mp4",
                    },
                    {
                        "path": "nested/encode.webm",
                        "role": "distorted",
                        "size_bytes": len(b"distorted"),
                        "suffix": ".webm",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "manifest.json").write_text(
        json.dumps({"reference": {"path": "reference.mp4"}}),
        encoding="utf-8",
    )

    result = main(["package", "--project-dir", str(project_dir)])

    assert result == 0
    package_path = workflow_dir / "video0-inputs.tar"
    package_manifest_path = workflow_dir / "package-manifest.json"
    assert package_path.is_file()
    assert package_manifest_path.is_file()

    with tarfile.open(package_path, "r") as archive:
        names = sorted(archive.getnames())
    assert names == sorted(
        [
            "video0/.workflow/manifest.json",
            "video0/.workflow/media-inventory.json",
            "video0/.workflow/package-manifest.json",
            "video0/nested/encode.webm",
            "video0/reference.mp4",
        ]
    )

    package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
    assert package_manifest["archive_path"] == str(package_path)
    assert package_manifest["archive_root"] == "video0"
    assert [entry["path"] for entry in package_manifest["media_files"]] == [
        "reference.mp4",
        "nested/encode.webm",
    ]

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["package"] == {
        "path": str(package_path),
        "manifest": str(package_manifest_path),
    }


def test_package_supports_explicit_output_path(tmp_path: Path) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (project_dir / "reference.mp4").write_bytes(b"reference")
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "reference.mp4",
                "files": [
                    {
                        "path": "reference.mp4",
                        "role": "reference",
                        "size_bytes": len(b"reference"),
                        "suffix": ".mp4",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "upload" / "custom.tar"

    result = main(
        [
            "package",
            "--project-dir",
            str(project_dir),
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    assert output_path.is_file()


def test_package_requires_media_inventory(tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "video0"
    project_dir.mkdir()

    result = main(["package", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "media-inventory" in captured.err
    assert not (project_dir / ".workflow" / "video0-inputs.tar").exists()


def test_package_rejects_inventory_paths_outside_project(
    tmp_path: Path, capsys
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "../escape.mp4",
                "files": [
                    {
                        "path": "../escape.mp4",
                        "role": "reference",
                        "size_bytes": 1,
                        "suffix": ".mp4",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = main(["package", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "outside project" in captured.err
    assert not (workflow_dir / "video0-inputs.tar").exists()


def test_remote_plan_requires_project_dir(capsys) -> None:
    result = main(["remote-plan"])

    captured = capsys.readouterr()
    assert result == 2
    assert "--project-dir" in captured.err


@pytest.mark.parametrize(
    "command",
    ["upload", "run", "fetch-results", "cleanup"],
)
def test_remote_commands_require_project_dir(command: str, capsys) -> None:
    result = main([command])

    captured = capsys.readouterr()
    assert result == 2
    assert "--project-dir" in captured.err


def test_cleanup_dispatches_project_and_prints_reclaimed_bytes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []
    project_dir = tmp_path / "video0"

    def fake_cleanup(project):
        calls.append(project)
        return {
            "cleanup": {
                "status": "completed",
                "last_reclaimed_bytes": 20,
                "archives": {
                    "package": {"size_bytes": 100},
                    "result": {"size_bytes": 20},
                },
            }
        }

    monkeypatch.setattr("vmaf_workflow.cli.cleanup_project", fake_cleanup)

    result = main(["cleanup", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 0
    assert calls[0].video_dir == project_dir
    assert captured.out == "cleanup completed: 20 bytes reclaimed\n"


def test_cleanup_maps_state_failure_to_exit_code_2(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "vmaf_workflow.cli.cleanup_project",
        lambda *_args: (_ for _ in ()).throw(
            CleanupStateError("fetch-results must be completed")
        ),
    )

    result = main(["cleanup", "--project-dir", str(tmp_path / "video0")])

    captured = capsys.readouterr()
    assert result == 2
    assert "fetch-results must be completed" in captured.err


def test_cleanup_maps_delete_failure_to_exit_code_1(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "vmaf_workflow.cli.cleanup_project",
        lambda *_args: (_ for _ in ()).throw(
            CleanupExecutionError("delete-result failed")
        ),
    )

    result = main(["cleanup", "--project-dir", str(tmp_path / "video0")])

    captured = capsys.readouterr()
    assert result == 1
    assert "delete-result failed" in captured.err


def test_upload_passes_config_defaults_and_cli_target_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    runner = object()

    def fake_upload(project, settings, actual_runner):
        calls.append((project, settings, actual_runner))
        return {}

    monkeypatch.setattr("vmaf_workflow.cli.upload_project", fake_upload)
    project_dir = tmp_path / "video0"

    result = main(
        [
            "upload",
            "--project-dir",
            str(project_dir),
            "--host",
            "gpu-alias",
            "--remote-dir",
            "/srv/vmaf jobs",
        ],
        runner=runner,
    )

    assert result == 0
    project, settings, actual_runner = calls[0]
    assert project.video_dir == project_dir
    assert settings == RemoteSettings(
        host="gpu-alias",
        work_dir=PurePosixPath("/srv/vmaf jobs"),
    )
    assert actual_runner is runner


@pytest.mark.parametrize("command", ["run", "fetch-results"])
def test_run_and_fetch_do_not_accept_remote_target_overrides(
    command: str,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                command,
                "--project-dir",
                "videos/video0",
                "--host",
                "other",
            ]
        )

    assert exc_info.value.code == 2


def test_upload_maps_remote_command_failure_to_exit_code_1(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "vmaf_workflow.cli.upload_project",
        lambda *_args: (_ for _ in ()).throw(
            RemoteCommandError("preflight failed")
        ),
    )

    result = main(
        ["upload", "--project-dir", str(tmp_path / "video0")],
        runner=object(),
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "preflight failed" in captured.err


def test_run_maps_local_state_failure_to_exit_code_2(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "vmaf_workflow.cli.run_remote_project",
        lambda *_args: (_ for _ in ()).throw(
            RemoteWorkflowError("remote state is required")
        ),
    )

    result = main(
        ["run", "--project-dir", str(tmp_path / "video0")],
        runner=object(),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "remote state is required" in captured.err


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


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("upload", "vmaf_workflow.cli.upload_project"),
        ("fetch-results", "vmaf_workflow.cli.fetch_results"),
    ],
)
def test_upload_and_fetch_map_interrupt_to_exit_code_130(
    tmp_path: Path,
    monkeypatch,
    capsys,
    command: str,
    target: str,
) -> None:
    monkeypatch.setattr(
        target,
        lambda *_args: (_ for _ in ()).throw(RemoteRunInterrupted()),
    )

    result = main(
        [command, "--project-dir", str(tmp_path / "video0")],
        runner=object(),
    )

    captured = capsys.readouterr()
    assert result == 130
    assert "interrupted" in captured.err


def test_remote_plan_requires_inventory_and_package_manifest(
    tmp_path: Path, capsys
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)

    result = main(["remote-plan", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "media-inventory" in captured.err

    (workflow_dir / "media-inventory.json").write_text(
        json.dumps({"reference": "ref.mp4", "files": []}),
        encoding="utf-8",
    )
    result = main(["remote-plan", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "package-manifest" in captured.err


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
    assert (
        remote_plan["result_provenance"]
        == "vmaf-workflow-provenance.json"
    )
    assert remote_plan["environment_preflight_argument"] == "--environment-only"
    assert remote_plan["preflight_argument"] == "--preflight-only"
    assert remote_plan["requirements"] == {
        "ffmpeg": {"minimum_major": 5, "required_filter": "libvmaf"},
        "ffprobe": {"minimum_major": 5},
        "easyvmaf": {
            "repo": "/opt/easy Vmaf",
            "executable": "/opt/easy Vmaf/.venv/bin/easyvmaf",
            "required_branch": "master",
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
    assert "EASYVMAF_REPO='/opt/easy Vmaf'" in script
    assert "EASYVMAF_EXECUTABLE='/opt/easy Vmaf/.venv/bin/easyvmaf'" in script
    assert "EASYVMAF_REQUIRED_BRANCH=master" in script
    assert 'MODE=${1:-run}' in script
    assert 'usage: $0 [--environment-only|--preflight-only]' in script
    assert '"$EASYVMAF_EXECUTABLE" --help' in script
    assert (
        'git -C "$EASYVMAF_REPO" symbolic-ref --quiet --short HEAD'
        in script
    )
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
        'tar -czf "$RESULT_ARCHIVE" -- \'video0/普通 1080_vmaf.json\' '
        "video0/clip-1600_vmaf.json video0/clip-2160p_vmaf.json"
    ) in script
    assert 'tar -tzf "$RESULT_ARCHIVE"' in script

    manifest = json.loads((workflow_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["keep"] == "existing"
    assert manifest["remote_plan"] == {
        "manifest": str(remote_plan_path),
        "script": str(remote_script_path),
    }


def test_remote_plan_includes_configured_easyvmaf_threads(tmp_path: Path) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "ref.mp4",
                "files": [
                    {"path": "ref.mp4", "role": "reference", "height": 1080},
                    {"path": "dist.mp4", "role": "distorted", "height": 1080},
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
                        "path": "ref.mp4",
                        "role": "reference",
                    },
                    {
                        "path": "dist.mp4",
                        "role": "distorted",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "video0-inputs.tar").write_bytes(b"package")

    plan = write_remote_plan(
        WorkflowProject(video_dir=project_dir, workflow_dir=workflow_dir),
        EasyVmafSettings(repo_dir=Path("/opt/easyVmaf"), threads=8),
    )

    assert plan["commands"][0]["command"][-2:] == ["-threads", "8"]
    script = (workflow_dir / "remote-plan.sh").read_text(encoding="utf-8")
    assert "-threads 8" in script


def test_remote_plan_includes_configured_easyvmaf_branch(tmp_path: Path) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "ref.mp4",
                "files": [
                    {"path": "ref.mp4", "role": "reference"},
                    {"path": "dist.mp4", "role": "distorted"},
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
                    {"path": "ref.mp4", "role": "reference"},
                    {"path": "dist.mp4", "role": "distorted"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "video0-inputs.tar").write_bytes(b"package")

    plan = write_remote_plan(
        WorkflowProject(video_dir=project_dir, workflow_dir=workflow_dir),
        EasyVmafSettings(
            repo_dir=Path("/opt/easyVmaf"),
            required_branch="release",
        ),
    )

    assert plan["requirements"]["easyvmaf"]["required_branch"] == "release"
    script = (workflow_dir / "remote-plan.sh").read_text(encoding="utf-8")
    assert "EASYVMAF_REQUIRED_BRANCH=release" in script


def test_remote_plan_rejects_package_manifest_that_does_not_match_inventory(
    tmp_path: Path,
    capsys,
) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "ref.mp4",
                "files": [
                    {"path": "ref.mp4", "role": "reference", "size_bytes": 10},
                    {"path": "dist.mp4", "role": "distorted", "size_bytes": 20},
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
                    {"path": "ref.mp4", "role": "reference", "size_bytes": 10},
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "video0-inputs.tar").write_bytes(b"package")

    result = main(["remote-plan", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "rerun package" in captured.err


def test_remote_plan_rejects_empty_distorted_set(tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps(
            {
                "reference": "ref.mp4",
                "files": [
                    {"path": "ref.mp4", "role": "reference", "size_bytes": 10},
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
                    {"path": "ref.mp4", "role": "reference", "size_bytes": 10},
                ],
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "video0-inputs.tar").write_bytes(b"package")

    result = main(["remote-plan", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "at least one distorted" in captured.err


def test_remote_plan_rejects_colliding_result_paths(tmp_path: Path, capsys) -> None:
    project_dir = tmp_path / "video0"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    files = [
        {"path": "ref.mp4", "role": "reference"},
        {"path": "same.mp4", "role": "distorted"},
        {"path": "same.webm", "role": "distorted"},
    ]
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps({"reference": "ref.mp4", "files": files}),
        encoding="utf-8",
    )
    (workflow_dir / "package-manifest.json").write_text(
        json.dumps(
            {
                "archive_path": str(workflow_dir / "video0-inputs.tar"),
                "archive_root": "video0",
                "media_files": files,
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "video0-inputs.tar").write_bytes(b"package")

    result = main(["remote-plan", "--project-dir", str(project_dir)])

    captured = capsys.readouterr()
    assert result == 2
    assert "same_vmaf.json" in captured.err
    assert "collision" in captured.err


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

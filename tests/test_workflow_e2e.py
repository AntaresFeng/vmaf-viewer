from __future__ import annotations

import io
import json
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from vmaf_viewer.scanner import scan_vmaf_files
from vmaf_workflow.cleanup import cleanup_project
from vmaf_workflow.cli import main
from vmaf_workflow.config import RemoteSettings
from vmaf_workflow.models import CommandResult
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_state import sha256_file
from vmaf_workflow.remote_workflow import (
    fetch_results,
    run_remote_project,
    upload_project,
)


YTDLP_PREFLIGHT = {
    "formats": [
        {
            "format_id": "271",
            "format_note": "1080p",
            "resolution": "1920x1080",
            "width": 1920,
            "height": 1080,
            "vcodec": "vp9",
            "acodec": "none",
            "fps": 30,
            "vbr": 2500,
            "filesize": 16,
            "ext": "webm",
            "protocol": "https",
            "container": "webm_dash",
        }
    ],
    "requested_downloads": [
        {
            "format_id": "271",
            "format_note": "1080p",
            "resolution": "1920x1080",
            "width": 1920,
            "height": 1080,
            "vcodec": "vp9",
            "acodec": "none",
            "fps": 30,
            "vbr": 2500,
            "filesize": 16,
            "ext": "webm",
            "protocol": "https",
            "container": "webm_dash",
        }
    ],
}


def test_workflow_runs_from_download_through_cleanup_and_viewer_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = EndToEndRunner()
    videos_dir = tmp_path / "videos"

    assert main(
        [
            "download",
            "--videos-dir",
            str(videos_dir),
            "--bvid",
            "BV1xx411c7mD",
            "--ytid",
            "dQw4w9WgXcQ",
        ],
        runner=runner,
    ) == 0

    project_dir = videos_dir / "video0"
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference-media")
    monkeypatch.setattr(
        "vmaf_workflow.prepare._probe_media",
        lambda _path: {
            "width": 1920,
            "height": 1080,
            "resolution": "1920x1080",
            "fps": 30.0,
            "codec": "h264",
            "container": "mov,mp4",
        },
    )

    assert main(
        [
            "prepare",
            "--project-dir",
            str(project_dir),
            "--reference",
            str(reference),
        ]
    ) == 0
    assert main(["package", "--project-dir", str(project_dir)]) == 0
    assert main(["remote-plan", "--project-dir", str(project_dir)]) == 0

    project = WorkflowProject(
        video_dir=project_dir,
        workflow_dir=project_dir / ".workflow",
    )
    transport = EndToEndTransport(project, tmp_path)
    upload_state = upload_project(
        project,
        RemoteSettings(),
        runner,
        transport=transport,
    )
    run_state = run_remote_project(
        project,
        RemoteSettings(),
        runner,
        transport=transport,
    )
    fetch_state = fetch_results(
        project,
        RemoteSettings(),
        runner,
        transport=transport,
    )
    cleanup_state = cleanup_project(project)
    refetched_state = fetch_results(
        project,
        RemoteSettings(),
        runner,
        transport=transport,
    )
    refetched_archive_size = project.default_result_archive_path.stat().st_size
    second_cleanup_state = cleanup_project(project)

    assert upload_state["upload"]["status"] == "completed"
    assert run_state["run"]["status"] == "completed"
    assert fetch_state["fetch"]["status"] == "completed"
    assert cleanup_state["cleanup"]["status"] == "completed"
    assert refetched_state["fetch"]["status"] == "completed"
    assert second_cleanup_state["cleanup"]["status"] == "completed"
    assert (
        second_cleanup_state["cleanup"]["last_reclaimed_bytes"]
        == refetched_archive_size
    )
    assert not project.default_package_path.exists()
    assert not project.default_result_archive_path.exists()
    assert len(scan_vmaf_files(project_dir)) == 2
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    assert manifest["package"]["path"] is None
    assert manifest["results"]["archive"] is None
    assert len(manifest["results"]["files"]) == 2


class EndToEndRunner:
    def run(self, argv, stdin=None) -> CommandResult:
        arguments = [str(value) for value in argv]
        if "-info" in arguments:
            return CommandResult(
                tuple(arguments),
                0,
                (
                    "0. [1080P 高码率] [1920x1080] [AVC] "
                    "[30.000] [3000 kbps] [~1.00 MB]\n"
                ),
                "",
                stdin,
            )
        if "-J" in arguments:
            return CommandResult(
                tuple(arguments),
                0,
                json.dumps(YTDLP_PREFLIGHT),
                "",
                stdin,
            )
        if stdin is not None and "--config-file" in arguments:
            config_path = Path(arguments[arguments.index("--config-file") + 1])
            lines = config_path.read_text(encoding="utf-8").splitlines()
            work_dir = Path(lines[lines.index("--work-dir") + 1])
            (work_dir / "BV1xx411c7mD-1080P-AVC.mp4").write_bytes(
                b"bilibili-media"
            )
        if "--config-locations" in arguments:
            config_path = Path(
                arguments[arguments.index("--config-locations") + 1]
            )
            self._write_youtube_outputs(config_path)
        return CommandResult(tuple(arguments), 0, "", "", stdin)

    def _write_youtube_outputs(self, config_path: Path) -> None:
        lines = config_path.read_text(encoding="utf-8").splitlines()
        home_line = next(line for line in lines if line.startswith("-P home:"))
        home = Path(home_line.removeprefix("-P home:"))
        (home / "dQw4w9WgXcQ-1080p-vp9.webm").write_bytes(b"youtube-media")
        metadata_line = next(
            line for line in lines if line.startswith("--print-to-file ")
        )
        metadata_path = Path(metadata_line.split("after_video:%()j ", 1)[1])
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(YTDLP_PREFLIGHT) + "\n",
            encoding="utf-8",
        )


class EndToEndTransport:
    def __init__(self, project: WorkflowProject, tmp_path: Path) -> None:
        self.project = project
        self.result_archive = tmp_path / "remote-results.tar.gz"
        self.hashes: dict[str, str] = {}

    def ensure_work_dir(self, log_path: Path) -> None:
        pass

    def upload_atomic(
        self,
        local_path: Path,
        remote_path: PurePosixPath,
        expected_sha256: str,
        log_path: Path,
    ) -> bool:
        self.hashes[remote_path.as_posix()] = expected_sha256
        return True

    def stream_script(
        self,
        script_path: PurePosixPath,
        argument: str | None,
        log_path: Path,
        append: bool = True,
    ) -> int:
        return 0

    def stream_run(
        self,
        script_path: PurePosixPath,
        log_path: Path,
        append: bool = True,
    ) -> int:
        plan = json.loads(self.project.remote_plan_path.read_text(encoding="utf-8"))
        provenance = json.loads(
            self.project.remote_provenance_path.read_text(encoding="utf-8")
        )
        with tarfile.open(self.result_archive, "w:gz") as archive:
            for result_name in plan["expected_results"]:
                _add_json_member(
                    archive,
                    result_name,
                    {"pooled_metrics": {"vmaf": {"mean": 95.0}}},
                )
            _add_json_member(
                archive,
                plan["result_provenance"],
                provenance,
            )
        remote_result = script_path.parent / plan["result_archive"]
        self.hashes[remote_result.as_posix()] = sha256_file(self.result_archive)
        return 0

    def remote_sha256(
        self,
        remote_path: PurePosixPath,
        log_path: Path,
    ) -> str | None:
        return self.hashes.get(remote_path.as_posix())

    def download(
        self,
        remote_path: PurePosixPath,
        local_path: Path,
        log_path: Path,
    ) -> None:
        shutil.copyfile(self.result_archive, local_path)


def _add_json_member(
    archive: tarfile.TarFile,
    name: str,
    payload: object,
) -> None:
    content = json.dumps(payload).encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(content)
    archive.addfile(info, io.BytesIO(content))

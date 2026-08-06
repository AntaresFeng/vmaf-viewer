from __future__ import annotations

import json
from pathlib import Path

import pytest

from vmaf_workflow.download import DownloadOutcome
from vmaf_workflow.packager import PackageError
from vmaf_workflow.pipeline import (
    PipelineBlockedError,
    PipelineRequest,
    PipelineValidationError,
    StageName,
    StageStatus,
    WorkflowPipeline,
    validate_pipeline_request,
)
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.status import WorkflowStatus


def test_validate_pipeline_request_normalizes_sources_and_reference(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"media")

    request = validate_pipeline_request(
        PipelineRequest(
            bvid="https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            ytid="dQw4w9WgXcQ",
            reference=reference,
        )
    )

    assert request.bvid == "BV1xx411c7mD"
    assert request.ytid == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert request.reference == reference


def test_pipeline_runs_all_stages_and_keeps_plan_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project(tmp_path)
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference")
    calls = []
    _patch_pipeline_stages(monkeypatch, project, calls, warning="resolution differs")

    pipeline = WorkflowPipeline(
        PipelineRequest(
            videos_dir=tmp_path / "videos",
            bvid="BV1xx411c7mD",
            reference=reference,
        )
    )

    assert pipeline.run() == 0
    assert calls == [stage.value for stage in StageName]
    assert pipeline.records[StageName.REMOTE_PLAN].status == StageStatus.WARNING
    assert pipeline.records[StageName.CLEANUP].status == StageStatus.SUCCESS
    assert all(
        record.elapsed_seconds is not None for record in pipeline.records.values()
    )


def test_pipeline_skips_cleanup_when_disabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project(tmp_path)
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference")
    calls = []
    _patch_pipeline_stages(monkeypatch, project, calls, final_stage="fetched")

    pipeline = WorkflowPipeline(
        PipelineRequest(
            bvid="BV1xx411c7mD",
            reference=reference,
            cleanup=False,
        )
    )

    assert pipeline.run() == 0
    assert "cleanup" not in calls
    assert pipeline.records[StageName.CLEANUP].status == StageStatus.SKIPPED


def test_pipeline_stops_and_retries_failed_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project(tmp_path)
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference")
    calls = []
    _patch_pipeline_stages(monkeypatch, project, calls)
    should_fail = {"value": True}

    def package(_project):
        calls.append("package")
        if should_fail["value"]:
            raise PackageError("package failed")

    monkeypatch.setattr("vmaf_workflow.pipeline.package_project", package)
    pipeline = WorkflowPipeline(
        PipelineRequest(bvid="BV1xx411c7mD", reference=reference)
    )

    assert pipeline.run() == 2
    assert pipeline.records[StageName.PACKAGE].status == StageStatus.FAILED
    assert "remote-plan" not in calls

    should_fail["value"] = False
    calls.clear()
    assert pipeline.run(retry=True) == 0
    assert calls[0] == "package"
    assert pipeline.records[StageName.PACKAGE].status == StageStatus.SUCCESS


def test_pipeline_resumes_from_existing_uploaded_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project(tmp_path)
    _write_complete_download_manifest(project)
    (project.video_dir / "bilibili.mp4").write_bytes(b"media")
    calls = []
    _patch_pipeline_stages(
        monkeypatch,
        project,
        calls,
        resume_status=WorkflowStatus(
            project.video_dir,
            "uploaded",
            "completed",
            (),
            "uv run vmaf-workflow run",
        ),
    )

    pipeline = WorkflowPipeline(PipelineRequest(project_dir=project.video_dir))

    assert pipeline.run() == 0
    assert calls == ["run", "fetch-results", "cleanup", "status"]
    assert pipeline.records[StageName.UPLOAD].status == StageStatus.SUCCESS
    assert pipeline.records[StageName.UPLOAD].elapsed_seconds is None


def test_pipeline_blocks_automatic_retry_of_running_remote_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project(tmp_path)
    _write_complete_download_manifest(project)
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.inspect_workflow_status",
        lambda _project: WorkflowStatus(
            project.video_dir,
            "running",
            "running",
            (),
            "uv run vmaf-workflow status",
        ),
    )

    pipeline = WorkflowPipeline(PipelineRequest(project_dir=project.video_dir))

    with pytest.raises(PipelineBlockedError, match="running"):
        pipeline.run()
    assert pipeline.records[StageName.RUN].status == StageStatus.BLOCKED


def test_pipeline_rejects_empty_resume_project_before_execution(tmp_path: Path) -> None:
    project = _project(tmp_path)

    with pytest.raises(PipelineValidationError, match="至少填写一个"):
        WorkflowPipeline(PipelineRequest(project_dir=project.video_dir))

    with pytest.raises(PipelineValidationError, match="参考视频"):
        WorkflowPipeline(
            PipelineRequest(project_dir=project.video_dir, bvid="BV1xx411c7mD")
        )


def test_pipeline_download_command_uses_only_incomplete_source(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    project.manifest_path.write_text(
        json.dumps(
            {
                "bilibili": {
                    "bvid": "BV1xx411c7mD",
                    "downloads": [{"status": "downloaded"}],
                },
                "youtube": {
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "downloads": [{"status": "failed"}],
                },
            }
        ),
        encoding="utf-8",
    )
    (project.video_dir / "bilibili.mp4").write_bytes(b"media")
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference")
    pipeline = WorkflowPipeline(
        PipelineRequest(project_dir=project.video_dir, reference=reference)
    )

    command = pipeline._command(StageName.DOWNLOAD)

    assert "--ytid" in command
    assert "dQw4w9WgXcQ" in command
    assert "--bvid" not in command


def _project(tmp_path: Path) -> WorkflowProject:
    video_dir = tmp_path / "videos" / "video0"
    workflow_dir = video_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    return WorkflowProject(video_dir, workflow_dir)


def _patch_pipeline_stages(
    monkeypatch,
    project: WorkflowProject,
    calls: list[str],
    *,
    warning: str | None = None,
    final_stage: str = "cleaned",
    resume_status: WorkflowStatus | None = None,
) -> None:
    def download(**kwargs):
        calls.append("download")
        return DownloadOutcome(
            project,
            {"bilibili": {"bvid": kwargs.get("bvid")}},
            0,
            kwargs.get("bvid"),
            kwargs.get("ytid"),
        )

    monkeypatch.setattr("vmaf_workflow.pipeline.download_sources", download)
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.prepare_project",
        lambda *_args: calls.append("prepare"),
    )
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.package_project",
        lambda *_args: calls.append("package"),
    )

    def plan(*_args):
        calls.append("remote-plan")
        return {"warnings": [] if warning is None else [warning]}

    monkeypatch.setattr("vmaf_workflow.pipeline.write_remote_plan", plan)
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.upload_project",
        lambda *_args: calls.append("upload"),
    )
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.run_remote_project",
        lambda *_args: calls.append("run"),
    )
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.fetch_results",
        lambda *_args: calls.append("fetch-results"),
    )
    monkeypatch.setattr(
        "vmaf_workflow.pipeline.cleanup_project",
        lambda *_args: calls.append("cleanup"),
    )

    def status(_project):
        if resume_status is not None and not calls:
            return resume_status
        calls.append("status")
        return WorkflowStatus(
            project.video_dir,
            final_stage,
            "completed",
            (),
            "uv run vmaf-viewer",
        )

    monkeypatch.setattr("vmaf_workflow.pipeline.inspect_workflow_status", status)


def _write_complete_download_manifest(project: WorkflowProject) -> None:
    project.manifest_path.write_text(
        json.dumps(
            {
                "bilibili": {
                    "bvid": "BV1xx411c7mD",
                    "downloads": [{"status": "downloaded"}],
                },
                "youtube": {"url": None, "downloads": []},
            }
        ),
        encoding="utf-8",
    )

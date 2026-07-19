from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Callable

from vmaf_workflow.cleanup import (
    CleanupExecutionError,
    CleanupStateError,
    cleanup_project,
)
from vmaf_workflow.config import WorkflowSettings, default_settings
from vmaf_workflow.download import (
    DownloadInputError,
    download_sources,
    requested_incomplete_sources,
)
from vmaf_workflow.download_state import (
    DownloadStateError,
    load_download_manifest,
    validate_source_identity,
)
from vmaf_workflow.packager import PackageError, package_project
from vmaf_workflow.prepare import (
    EXCLUDED_DIR_NAMES,
    MEDIA_SUFFIXES,
    PrepareError,
    prepare_project,
)
from vmaf_workflow.project import (
    WorkflowProject,
    next_video_dir,
    normalize_bvid,
    normalize_youtube_url,
    project_from_dir,
)
from vmaf_workflow.remote_plan import RemotePlanError, write_remote_plan
from vmaf_workflow.remote_transport import RemoteTargetError
from vmaf_workflow.remote_workflow import (
    RemoteCommandError,
    RemoteRunInterrupted,
    RemoteWorkflowError,
    fetch_results,
    run_remote_project,
    upload_project,
)
from vmaf_workflow.runner import ProcessInterrupted, SubprocessRunner
from vmaf_workflow.status import (
    WorkflowStatusError,
    inspect_workflow_status,
    load_optional_json_object,
)


class StageName(StrEnum):
    DOWNLOAD = "download"
    PREPARE = "prepare"
    PACKAGE = "package"
    REMOTE_PLAN = "remote-plan"
    UPLOAD = "upload"
    RUN = "run"
    FETCH_RESULTS = "fetch-results"
    CLEANUP = "cleanup"
    STATUS = "status"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


STAGES = tuple(StageName)


@dataclass
class StageRecord:
    name: StageName
    status: StageStatus = StageStatus.PENDING
    returncode: int | None = None
    elapsed_seconds: float | None = None
    error: str | None = None
    warnings: tuple[str, ...] = ()
    _started: float | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PipelineRequest:
    videos_dir: Path = Path("videos")
    project_dir: Path | None = None
    bvid: str | None = None
    ytid: str | None = None
    reference: Path | None = None
    cleanup: bool = True
    allow_running_retry: bool = False


@dataclass(frozen=True)
class PipelineEvent:
    kind: str
    stage: StageName | None = None
    record: StageRecord | None = None
    message: str | None = None
    stream: str | None = None
    project_dir: Path | None = None


@dataclass(frozen=True)
class ResumeDefaults:
    bvid: str | None
    ytid: str | None
    reference: Path | None


class PipelineValidationError(ValueError):
    pass


class PipelineBlockedError(RuntimeError):
    pass


class StageFailed(RuntimeError):
    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


EventSink = Callable[[PipelineEvent], None]


def validate_pipeline_request(request: PipelineRequest) -> PipelineRequest:
    return _validate_pipeline_request_context(request)[0]


def _validate_pipeline_request_context(
    request: PipelineRequest,
) -> tuple[PipelineRequest, dict | None, ResumeDefaults | None]:
    bvid = request.bvid.strip() if request.bvid else None
    ytid = request.ytid.strip() if request.ytid else None
    project_dir = Path(request.project_dir) if request.project_dir else None
    reference = Path(request.reference) if request.reference else None

    try:
        normalized_bvid = normalize_bvid(bvid) if bvid else None
        normalized_youtube = normalize_youtube_url(ytid) if ytid else None
    except ValueError as exc:
        raise PipelineValidationError(str(exc)) from exc

    if project_dir is not None and not project_dir.is_dir():
        raise PipelineValidationError(f"项目目录不存在: {project_dir}")

    manifest: dict | None = None
    resume_defaults: ResumeDefaults | None = None
    if project_dir is not None:
        project = project_from_dir(project_dir)
        manifest = _load_manifest(project.manifest_path)
        try:
            validate_source_identity(manifest, normalized_bvid, normalized_youtube)
        except DownloadStateError as exc:
            raise PipelineValidationError(str(exc)) from exc
        inventory = _load_resume_inventory(project.media_inventory_path)
        resume_defaults = _resume_defaults_from_state(project, manifest, inventory)

    if not any(
        (
            normalized_bvid,
            normalized_youtube,
            None if resume_defaults is None else resume_defaults.bvid,
            None if resume_defaults is None else resume_defaults.ytid,
        )
    ):
        raise PipelineValidationError("B站和 YouTube 来源至少填写一个")

    if reference is not None:
        reference = reference.expanduser()
        if not reference.is_file():
            raise PipelineValidationError(f"参考视频不存在: {reference}")
        if reference.suffix.lower() not in MEDIA_SUFFIXES:
            raise PipelineValidationError(f"不支持的参考视频格式: {reference.suffix}")

    needs_reference = project_dir is None
    if project_dir is not None:
        if manifest is None or any(requested_incomplete_sources(manifest)) or (
            _has_requested_unbound_source(
                manifest,
                normalized_bvid,
                normalized_youtube,
            )
        ):
            needs_reference = True
        else:
            try:
                status = inspect_workflow_status(project_from_dir(project_dir))
            except WorkflowStatusError as exc:
                raise PipelineValidationError(str(exc)) from exc
            needs_reference = _status_stage(status.stage) in {
                StageName.DOWNLOAD,
                StageName.PREPARE,
            }

    if needs_reference and reference is None and (
        resume_defaults is None or resume_defaults.reference is None
    ):
        raise PipelineValidationError("必须提供参考视频路径")

    validated = PipelineRequest(
        videos_dir=Path(request.videos_dir),
        project_dir=project_dir,
        bvid=normalized_bvid,
        ytid=normalized_youtube,
        reference=reference,
        cleanup=request.cleanup,
        allow_running_retry=request.allow_running_retry,
    )
    return validated, manifest, resume_defaults


def load_resume_defaults(project_dir: Path) -> ResumeDefaults:
    project = project_from_dir(project_dir)
    manifest = _load_manifest(project.manifest_path)
    inventory = _load_resume_inventory(project.media_inventory_path)
    return _resume_defaults_from_state(project, manifest, inventory)


def _resume_defaults_from_state(
    project: WorkflowProject,
    manifest: dict | None,
    inventory: dict | None,
) -> ResumeDefaults:
    bvid = _nested_string(manifest, "bilibili", "bvid")
    ytid = _nested_string(manifest, "youtube", "url")
    reference: Path | None = None
    raw_reference = None
    if inventory is not None:
        value = inventory.get("reference")
        raw_reference = value if isinstance(value, str) else None
    if raw_reference is None:
        raw_reference = _nested_string(manifest, "reference", "path")
    if raw_reference:
        candidate = project.video_dir / Path(raw_reference)
        if candidate.is_file():
            reference = candidate
    return ResumeDefaults(bvid, ytid, reference)


def preview_project_dir(videos_dir: Path) -> Path:
    return next_video_dir(videos_dir)


class WorkflowPipeline:
    def __init__(
        self,
        request: PipelineRequest,
        *,
        event_sink: EventSink | None = None,
        settings: WorkflowSettings | None = None,
        runner: SubprocessRunner | None = None,
    ) -> None:
        self.request, initial_manifest, initial_defaults = (
            _validate_pipeline_request_context(request)
        )
        self.settings = settings or default_settings()
        self.event_sink = event_sink or (lambda _event: None)
        self.runner = runner or SubprocessRunner(
            output_callback=self._on_process_output,
            mirror_console=False,
            inherit_stdin=False,
        )
        if runner is not None:
            existing_callback = runner.output_callback
            if existing_callback is None:
                runner.output_callback = self._on_process_output
            elif existing_callback != self._on_process_output:
                runner.output_callback = self._chain_output_callbacks(
                    existing_callback,
                    self._on_process_output,
                )
            runner.mirror_console = False
            runner.inherit_stdin = False
        self.records = {stage: StageRecord(stage) for stage in STAGES}
        self.project = (
            project_from_dir(self.request.project_dir)
            if self.request.project_dir is not None
            else None
        )
        self._cancelled = threading.Event()
        self._failed_stage: StageName | None = None
        self._manifest_loaded = self.project is not None
        self._manifest_cache: dict | None = initial_manifest
        self._resume_defaults_cache: ResumeDefaults | None = initial_defaults

    def cancel(self) -> None:
        self._cancelled.set()
        self.runner.cancel_current()
        self._emit(PipelineEvent("cancelling", stage=self.current_stage))

    @property
    def current_stage(self) -> StageName | None:
        for stage, record in self.records.items():
            if record.status == StageStatus.RUNNING:
                return stage
        return self._failed_stage

    def run(self, *, retry: bool = False) -> int:
        if retry:
            if self._failed_stage is None:
                raise PipelineValidationError("没有可重试的失败步骤")
            self._cancelled.clear()
            self.runner.reset_cancellation()
            start_stage = self._failed_stage
            self._reset_from(start_stage)
        else:
            start_stage = self._resume_stage()
            self.records = {stage: StageRecord(stage) for stage in STAGES}
            self._mark_prior_success(start_stage)

        self._emit(PipelineEvent("pipeline-started", project_dir=self._project_path()))
        start_index = STAGES.index(start_stage)
        for stage in STAGES[start_index:]:
            if stage == StageName.CLEANUP and not self.request.cleanup:
                self._finish_skipped(stage)
                continue
            if self._cancelled.is_set():
                return self._finish_cancelled(stage)
            returncode = self._run_stage(stage)
            if returncode != 0:
                return returncode

        self._failed_stage = None
        self._emit(PipelineEvent("pipeline-completed", project_dir=self._project_path()))
        return 0

    def _run_stage(self, stage: StageName) -> int:
        record = self.records[stage]
        record.status = StageStatus.RUNNING
        record.returncode = None
        record.elapsed_seconds = None
        record.error = None
        record.warnings = ()
        record._started = time.monotonic()
        self._emit(PipelineEvent("stage-started", stage, record, self._command(stage)))
        try:
            warnings = self._execute(stage)
            if self._cancelled.is_set():
                return self._finish_cancelled(stage)
        except (ProcessInterrupted, RemoteRunInterrupted, KeyboardInterrupt):
            return self._finish_cancelled(stage)
        except Exception as exc:  # stage adapters translate expected failures below
            returncode = _exception_returncode(exc)
            record.status = StageStatus.FAILED
            record.returncode = returncode
            record.error = str(exc)
            record.elapsed_seconds = time.monotonic() - record._started
            self._failed_stage = stage
            self._emit(PipelineEvent("stage-finished", stage, record, str(exc)))
            return returncode

        record.warnings = tuple(warnings)
        record.status = StageStatus.WARNING if warnings else StageStatus.SUCCESS
        record.returncode = 0
        record.elapsed_seconds = time.monotonic() - record._started
        self._emit(PipelineEvent("stage-finished", stage, record))
        return 0

    def _execute(self, stage: StageName) -> tuple[str, ...]:
        if stage == StageName.DOWNLOAD:
            bvid, ytid = self._download_inputs()
            outcome = download_sources(
                runner=self.runner,
                bvid=bvid,
                ytid=ytid,
                videos_dir=self.request.videos_dir,
                project_dir=None if self.project is None else self.project.video_dir,
                settings=self.settings,
            )
            self.project = outcome.project
            self._manifest_cache = outcome.manifest
            self._manifest_loaded = True
            self._resume_defaults_cache = None
            self._emit(PipelineEvent("project-selected", project_dir=self.project.video_dir))
            if outcome.returncode != 0:
                raise StageFailed("下载阶段失败，请检查下载器输出", outcome.returncode)
            return ()

        project = self._require_project()
        if stage == StageName.PREPARE:
            reference = self.request.reference or self._resume_defaults().reference
            if reference is None:
                raise PipelineValidationError("必须提供参考视频路径")
            prepare_project(project, reference)
            return ()
        if stage == StageName.PACKAGE:
            package_project(project)
            return ()
        if stage == StageName.REMOTE_PLAN:
            plan = write_remote_plan(project, self.settings.easyvmaf)
            return tuple(str(value) for value in plan.get("warnings", []))
        if stage == StageName.UPLOAD:
            upload_project(project, self.settings.remote, self.runner)
            return ()
        if stage == StageName.RUN:
            run_remote_project(project, self.settings.remote, self.runner)
            return ()
        if stage == StageName.FETCH_RESULTS:
            fetch_results(project, self.settings.remote, self.runner)
            return ()
        if stage == StageName.CLEANUP:
            cleanup_project(project)
            return ()
        if stage == StageName.STATUS:
            status = inspect_workflow_status(project)
            accepted = {"cleaned"} if self.request.cleanup else {"fetched", "cleaned"}
            if status.stage not in accepted or status.state != "completed":
                raise StageFailed(
                    f"最终状态不是 completed: {status.stage}/{status.state}",
                    2,
                )
            self._emit(PipelineEvent("output", stage, message=status.next_command))
            return ()
        raise AssertionError(f"unsupported stage: {stage}")

    def _resume_stage(self) -> StageName:
        if self.project is None:
            return StageName.DOWNLOAD
        try:
            status = inspect_workflow_status(self.project)
        except WorkflowStatusError as exc:
            raise PipelineValidationError(str(exc)) from exc
        if status.state == "running" and not self.request.allow_running_retry:
            self.records[_status_stage(status.stage)].status = StageStatus.BLOCKED
            raise PipelineBlockedError(
                "项目状态仍为 running；请确认没有远端任务运行后再允许重试"
            )
        manifest = self._manifest()
        if manifest is None or (
            any(requested_incomplete_sources(manifest))
            or self._requests_unbound_source(manifest)
        ):
            return StageName.DOWNLOAD
        return _status_stage(status.stage)

    def _download_inputs(self) -> tuple[str | None, str | None]:
        bvid = self.request.bvid
        ytid = self.request.ytid
        if self.project is None:
            return bvid, ytid
        manifest = self._manifest()
        defaults = self._resume_defaults()
        if manifest is None:
            return bvid or defaults.bvid, ytid or defaults.ytid
        if not _has_supported_media(self.project.video_dir):
            return bvid or defaults.bvid, ytid or defaults.ytid
        retry_bilibili, retry_youtube = requested_incomplete_sources(manifest)
        if retry_bilibili:
            bvid = defaults.bvid
        elif defaults.bvid:
            bvid = None
        if retry_youtube:
            ytid = defaults.ytid
        elif defaults.ytid:
            ytid = None
        return bvid, ytid

    def _manifest(self) -> dict | None:
        if not self._manifest_loaded:
            project = self._require_project()
            self._manifest_cache = _load_manifest(project.manifest_path)
            self._manifest_loaded = True
        return self._manifest_cache

    def _resume_defaults(self) -> ResumeDefaults:
        if self._resume_defaults_cache is None:
            project = self._require_project()
            inventory = _load_resume_inventory(project.media_inventory_path)
            self._resume_defaults_cache = _resume_defaults_from_state(
                project,
                self._manifest(),
                inventory,
            )
        return self._resume_defaults_cache

    def _requests_unbound_source(self, manifest: dict) -> bool:
        return _has_requested_unbound_source(
            manifest,
            self.request.bvid,
            self.request.ytid,
        )

    def _mark_prior_success(self, start_stage: StageName) -> None:
        for stage in STAGES[: STAGES.index(start_stage)]:
            self.records[stage] = StageRecord(stage, status=StageStatus.SUCCESS)
        if not self.request.cleanup:
            self.records[StageName.CLEANUP].status = StageStatus.SKIPPED
        self._emit(PipelineEvent("records-initialized", project_dir=self._project_path()))

    def _reset_from(self, start_stage: StageName) -> None:
        for stage in STAGES[STAGES.index(start_stage) :]:
            self.records[stage] = StageRecord(stage)
        if not self.request.cleanup:
            self.records[StageName.CLEANUP].status = StageStatus.SKIPPED
        self._failed_stage = None

    def _finish_skipped(self, stage: StageName) -> None:
        record = self.records[stage]
        record.status = StageStatus.SKIPPED
        record.returncode = 0
        record.elapsed_seconds = 0.0
        self._emit(PipelineEvent("stage-finished", stage, record))

    def _finish_cancelled(self, stage: StageName) -> int:
        record = self.records[stage]
        record.status = StageStatus.CANCELLED
        record.returncode = 130
        if record._started is not None:
            record.elapsed_seconds = time.monotonic() - record._started
        self._failed_stage = stage
        self._emit(PipelineEvent("stage-finished", stage, record, "已取消"))
        self._emit(PipelineEvent("pipeline-cancelled", stage, record))
        return 130

    def _on_process_output(self, stream: str, text: str) -> None:
        self._emit(
            PipelineEvent(
                "process-output",
                stage=self.current_stage,
                message=text,
                stream=stream,
            )
        )

    def _command(self, stage: StageName) -> str:
        project = self._project_path()
        argv = ["uv", "run", "vmaf-workflow", stage.value]
        if stage == StageName.DOWNLOAD:
            bvid, ytid = self._download_inputs()
            if project is not None:
                argv.extend(["--project-dir", str(project)])
            if bvid:
                argv.extend(["--bvid", bvid])
            if ytid:
                argv.extend(["--ytid", ytid])
        elif project is not None:
            argv.extend(["--project-dir", str(project)])
            if stage == StageName.PREPARE:
                reference = self.request.reference or self._resume_defaults().reference
                if reference is not None:
                    argv.extend(["--reference", str(reference)])
        return subprocess.list2cmdline(argv)

    @staticmethod
    def _chain_output_callbacks(first, second):
        def chained(stream: str, text: str) -> None:
            try:
                first(stream, text)
            finally:
                second(stream, text)

        return chained

    def _require_project(self) -> WorkflowProject:
        if self.project is None:
            raise PipelineValidationError("下载阶段尚未创建项目目录")
        return self.project

    def _project_path(self) -> Path | None:
        return None if self.project is None else self.project.video_dir

    def _emit(self, event: PipelineEvent) -> None:
        if event.record is not None:
            event = replace(event, record=replace(event.record))
        self.event_sink(event)


def _status_stage(stage: str) -> StageName:
    mapping = {
        "new": StageName.DOWNLOAD,
        "downloaded": StageName.PREPARE,
        "prepared": StageName.PACKAGE,
        "packaged": StageName.REMOTE_PLAN,
        "planned": StageName.UPLOAD,
        "uploaded": StageName.RUN,
        "running": StageName.RUN,
        "computed": StageName.FETCH_RESULTS,
        "fetched": StageName.CLEANUP,
        "cleaned": StageName.STATUS,
    }
    try:
        return mapping[stage]
    except KeyError as exc:
        raise PipelineValidationError(f"无法识别工作流阶段: {stage}") from exc


def _exception_returncode(exc: Exception) -> int:
    if isinstance(exc, StageFailed):
        return exc.returncode
    if isinstance(
        exc,
        (
            DownloadInputError,
            PipelineValidationError,
            PrepareError,
            PackageError,
            RemotePlanError,
            RemoteWorkflowError,
            RemoteTargetError,
            CleanupStateError,
            WorkflowStatusError,
        ),
    ):
        return 2
    if isinstance(exc, (RemoteCommandError, CleanupExecutionError, OSError)):
        return 1
    return 1


def _load_manifest(path: Path) -> dict | None:
    try:
        return load_download_manifest(path)
    except DownloadStateError as exc:
        raise PipelineValidationError(str(exc)) from exc


def _load_resume_inventory(path: Path) -> dict | None:
    try:
        return load_optional_json_object(path, "media-inventory.json")
    except WorkflowStatusError as exc:
        raise PipelineValidationError(str(exc)) from exc


def _nested_string(value: dict | None, section: str, key: str) -> str | None:
    if value is None:
        return None
    child = value.get(section)
    if not isinstance(child, dict):
        return None
    result = child.get(key)
    return result if isinstance(result, str) and result else None


def _has_requested_unbound_source(
    manifest: dict,
    bvid: str | None,
    ytid: str | None,
) -> bool:
    return bool(bvid and _nested_string(manifest, "bilibili", "bvid") is None) or bool(
        ytid and _nested_string(manifest, "youtube", "url") is None
    )


def _has_supported_media(root: Path) -> bool:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if not any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
            return True
    return False

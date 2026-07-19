from __future__ import annotations

import re
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
    Switch,
)

from vmaf_workflow.config import default_settings
from vmaf_workflow.pipeline import (
    PipelineBlockedError,
    PipelineEvent,
    PipelineRequest,
    PipelineValidationError,
    STAGES,
    StageName,
    StageStatus,
    WorkflowPipeline,
    load_resume_defaults,
    preview_project_dir,
    validate_pipeline_request,
)
from vmaf_workflow.project import video_project_dirs


LOG_MAX_LINES = 5_000
OUTPUT_FLUSH_INTERVAL_SECONDS = 1 / 30


STATUS_LABELS = {
    StageStatus.PENDING: "等待",
    StageStatus.RUNNING: "运行中",
    StageStatus.SUCCESS: "成功",
    StageStatus.WARNING: "警告",
    StageStatus.FAILED: "失败",
    StageStatus.CANCELLED: "已取消",
    StageStatus.SKIPPED: "已跳过",
    StageStatus.BLOCKED: "需确认",
}

STAGE_LABELS = {
    StageName.DOWNLOAD: "下载",
    StageName.PREPARE: "准备",
    StageName.PACKAGE: "打包",
    StageName.REMOTE_PLAN: "远程计划",
    StageName.UPLOAD: "上传",
    StageName.RUN: "远程计算",
    StageName.FETCH_RESULTS: "拉取结果",
    StageName.CLEANUP: "清理归档",
    StageName.STATUS: "最终验证",
}


class ConfirmScreen(ModalScreen[bool]):
    CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-box { width: 76; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #confirm-summary { height: auto; margin-bottom: 1; }
    #confirm-actions { height: auto; align: right middle; }
    """

    def __init__(self, title: str, summary: str, confirm_label: str = "确认") -> None:
        super().__init__()
        self.dialog_title = title
        self.summary = summary
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.dialog_title, classes="dialog-title")
            yield Static(self.summary, id="confirm-summary")
            with Horizontal(id="confirm-actions"):
                yield Button("取消", id="dialog-cancel")
                yield Button(
                    self.confirm_label,
                    id="dialog-confirm",
                    variant="primary",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "dialog-confirm")


class WorkflowTui(App[int]):
    TITLE = "VMAF Workflow"
    SUB_TITLE = "自动下载、远程计算与结果回收"
    BINDINGS = [("q", "request_quit", "退出"), ("ctrl+c", "cancel", "取消任务")]
    CSS = """
    Screen { background: $background; }
    #setup, #run-view { height: 1fr; padding: 1 2; }
    #setup-title, #run-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .field-label { margin-top: 1; color: $text-muted; }
    Input, Select { width: 100%; }
    #cleanup-row { height: auto; margin-top: 1; }
    #cleanup-row Label { margin-left: 1; }
    #project-preview, #validation { height: auto; margin-top: 1; }
    #validation { color: $error; }
    #setup-actions, #run-actions { height: auto; margin-top: 1; align: right middle; }
    #setup-actions Button, #run-actions Button { margin-left: 1; }
    #run-summary { height: auto; margin-bottom: 1; }
    #progress { margin-bottom: 1; }
    #steps { height: 14; margin-bottom: 1; }
    #log { height: 1fr; border: round $accent; }
    #current-output { height: 1; color: $text-muted; }
    #completion { height: auto; color: $success; margin-top: 1; }
    """

    def __init__(
        self,
        *,
        videos_dir: Path = Path("videos"),
        project_dir: Path | None = None,
        pipeline_factory: Callable[..., WorkflowPipeline] = WorkflowPipeline,
    ) -> None:
        super().__init__()
        self.videos_dir = Path(videos_dir)
        self.initial_project_dir = Path(project_dir) if project_dir else None
        self.pipeline_factory = pipeline_factory
        self.pipeline: WorkflowPipeline | None = None
        self.request: PipelineRequest | None = None
        self.exit_code = 0
        self.running = False
        self._started_at: float | None = None
        self._partial_output = {"stdout": "", "stderr": ""}
        self._rendered_log: deque[tuple[str, str]] = deque(maxlen=LOG_MAX_LINES)
        self._pending_output: deque[tuple[str, str]] = deque()
        self._output_lock = threading.Lock()
        self._output_flush_scheduled = False
        self._ui_thread_id: int | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="setup"):
            yield Label("创建或恢复 VMAF 工作流", id="setup-title")
            yield Label("运行模式", classes="field-label")
            yield Select(
                [("新建项目", "new"), ("恢复已有项目", "resume")],
                value="resume" if self.initial_project_dir else "new",
                allow_blank=False,
                id="mode",
            )
            yield Label("已有项目", classes="field-label", id="project-select-label")
            yield Select(
                self._project_options(),
                value=(
                    str(self.initial_project_dir)
                    if self.initial_project_dir is not None
                    else Select.BLANK
                ),
                id="project-select",
            )
            yield Label("项目路径", classes="field-label", id="project-path-label")
            yield Input(
                value=str(self.initial_project_dir or ""),
                placeholder=r"videos\video12 或其他项目路径",
                id="project-path",
            )
            yield Static("", id="project-preview")
            yield Label("B站 URL 或 BVID（可选）", classes="field-label")
            yield Input(
                placeholder="BV... 或 https://www.bilibili.com/video/...", id="bvid"
            )
            yield Label("YouTube URL 或视频 ID（可选）", classes="field-label")
            yield Input(
                placeholder="11 位 ID 或 https://www.youtube.com/watch?v=...", id="ytid"
            )
            yield Label("参考视频路径", classes="field-label")
            yield Input(placeholder=r"C:\path\reference.mp4", id="reference")
            with Horizontal(id="cleanup-row"):
                yield Switch(value=True, id="cleanup")
                yield Label("成功拉回结果后自动安全清理归档")
            yield Static("", id="validation")
            with Horizontal(id="setup-actions"):
                yield Button("退出", id="setup-quit")
                yield Button("开始", id="start", variant="primary")

        with Vertical(id="run-view"):
            yield Label("工作流执行", id="run-title")
            yield Static("准备开始", id="run-summary")
            yield ProgressBar(total=len(STAGES), show_eta=False, id="progress")
            yield DataTable(id="steps", cursor_type="row")
            yield RichLog(
                id="log",
                wrap=True,
                auto_scroll=True,
                markup=False,
                max_lines=LOG_MAX_LINES,
            )
            yield Static("", id="current-output")
            yield Static("", id="completion")
            with Horizontal(id="run-actions"):
                yield Button("返回设置", id="back", disabled=True)
                yield Button("重试失败步骤", id="retry", disabled=True)
                yield Button("取消", id="cancel", variant="error", disabled=True)
                yield Button("退出", id="run-quit")
        yield Footer()

    def on_mount(self) -> None:
        self._ui_thread_id = threading.get_ident()
        self._log_widget = self.query_one("#log", RichLog)
        self._current_output_widget = self.query_one("#current-output", Static)
        table = self.query_one("#steps", DataTable)
        table.add_column("步骤", key="name")
        table.add_column("状态", key="status")
        table.add_column("返回码", key="returncode")
        table.add_column("耗时", width=8, key="elapsed")
        for stage in STAGES:
            table.add_row(STAGE_LABELS[stage], "等待", "—", "—", key=stage.value)
        self.query_one("#run-view").display = False
        self.set_interval(1.0, self._refresh_elapsed)
        self._update_mode()
        if self.initial_project_dir is not None:
            self._load_project(self.initial_project_dir)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "mode":
            self._update_mode()
        elif event.select.id == "project-select" and event.value is not Select.BLANK:
            path = Path(str(event.value))
            self.query_one("#project-path", Input).value = str(path)
            self._load_project(path)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "project-path" and self._mode() == "resume":
            path = Path(event.value) if event.value.strip() else None
            if path is not None and path.is_dir():
                self._load_project(path)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "start":
            self._confirm_start()
        elif button_id in {"setup-quit", "run-quit"}:
            self.action_request_quit()
        elif button_id == "cancel":
            self.action_cancel()
        elif button_id == "retry":
            self._retry()
        elif button_id == "back" and not self.running:
            self.query_one("#run-view").display = False
            self.query_one("#setup").display = True

    def action_cancel(self) -> None:
        if self.running and self.pipeline is not None:
            self.query_one("#cancel", Button).disabled = True
            self.query_one("#run-summary", Static).update("正在取消当前步骤…")
            self.pipeline.cancel()

    def action_request_quit(self) -> None:
        if not self.running:
            self.exit(self.exit_code)
            return
        self.push_screen(
            ConfirmScreen(
                "任务仍在运行", "退出会取消当前子进程。确认退出？", "取消并退出"
            ),
            self._quit_confirmed,
        )

    def _quit_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        if self.pipeline is not None:
            self.pipeline.cancel()
        self.exit(130)

    def _confirm_start(self) -> None:
        try:
            request = self._build_request()
        except PipelineValidationError as exc:
            self.query_one("#validation", Static).update(str(exc))
            return
        self.query_one("#validation", Static).update("")
        project = request.project_dir or preview_project_dir(request.videos_dir)
        summary = (
            f"项目: {project}\n"
            f"B站: {request.bvid or '未提供'}\n"
            f"YouTube: {request.ytid or '未提供'}\n"
            f"参考视频: {request.reference or '沿用项目记录'}\n"
            f"远端: {default_settings().remote.host}:{default_settings().remote.work_dir}\n"
            f"自动 cleanup: {'是' if request.cleanup else '否'}"
        )
        self.request = request
        self.push_screen(
            ConfirmScreen("确认自动执行", summary, "开始"), self._start_confirmed
        )

    def _start_confirmed(self, confirmed: bool | None) -> None:
        if confirmed and self.request is not None:
            self._start_pipeline(self.request)

    def _start_pipeline(self, request: PipelineRequest) -> None:
        self.request = request
        self.pipeline = self.pipeline_factory(request, event_sink=self._receive_event)
        self.exit_code = 0
        self.running = True
        self._started_at = time.monotonic()
        self._clear_run_view()
        self.query_one("#setup").display = False
        self.query_one("#run-view").display = True
        self.query_one("#cancel", Button).disabled = False
        self._run_pipeline()

    @work(thread=True, exclusive=True, exit_on_error=False)
    def _run_pipeline(self, retry: bool = False) -> None:
        if self.pipeline is None:
            return
        try:
            result = self.pipeline.run(retry=retry)
        except PipelineBlockedError as exc:
            self.call_from_thread(self._handle_blocked, str(exc))
            return
        except Exception as exc:
            self.call_from_thread(self._handle_unexpected_error, str(exc))
            return
        self.call_from_thread(self._pipeline_finished, result)

    def _retry(self) -> None:
        if self.pipeline is None or self.running:
            return
        self.running = True
        self.query_one("#retry", Button).disabled = True
        self.query_one("#back", Button).disabled = True
        self.query_one("#cancel", Button).disabled = False
        self._run_pipeline(retry=True)

    def _handle_blocked(self, message: str) -> None:
        self.running = False
        self.exit_code = 2
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#run-summary", Static).update(message)
        self._write_log("stderr", message)
        self.push_screen(
            ConfirmScreen("检测到 running 状态", message, "确认重试"),
            self._running_retry_confirmed,
        )

    def _running_retry_confirmed(self, confirmed: bool | None) -> None:
        if not confirmed or self.request is None:
            self.query_one("#back", Button).disabled = False
            return
        self._start_pipeline(replace(self.request, allow_running_retry=True))

    def _handle_unexpected_error(self, message: str) -> None:
        self.running = False
        self.exit_code = 1
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#back", Button).disabled = False
        self.query_one("#run-summary", Static).update(f"启动失败: {message}")
        self._write_log("stderr", message)

    def _pipeline_finished(self, result: int) -> None:
        self.running = False
        self.exit_code = result
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#back", Button).disabled = False
        self.query_one("#retry", Button).disabled = result == 0
        if (
            result == 0
            and self.pipeline is not None
            and self.pipeline.project is not None
        ):
            project = self.pipeline.project.video_dir
            json_count = len(list(project.glob("*_vmaf.json")))
            self.query_one("#completion", Static).update(
                f"完成：{project} | VMAF JSON: {json_count}\n"
                f"uv run vmaf-viewer --data-dir {project}"
            )
            self.query_one("#run-summary", Static).update("全部步骤已完成")
        elif result == 130:
            self.query_one("#run-summary", Static).update(
                "工作流已取消，可重试当前步骤"
            )
        else:
            self.query_one("#run-summary", Static).update("工作流失败，可重试失败步骤")

    def _receive_event(self, event: PipelineEvent) -> None:
        if event.kind == "process-output" and event.message:
            self._queue_output(event.stream or "stdout", event.message)
            return
        if threading.get_ident() == self._ui_thread_id:
            self._apply_event_after_output(event)
        else:
            self.call_from_thread(self._apply_event_after_output, event)

    def _queue_output(self, stream: str, message: str) -> None:
        with self._output_lock:
            self._pending_output.append((stream, message))
            if self._output_flush_scheduled:
                return
            self._output_flush_scheduled = True
        if threading.get_ident() == self._ui_thread_id:
            self._arm_output_flush()
        else:
            self.call_from_thread(self._arm_output_flush)

    def _arm_output_flush(self) -> None:
        self.set_timer(OUTPUT_FLUSH_INTERVAL_SECONDS, self._flush_queued_output)

    def _flush_queued_output(self) -> None:
        with self._output_lock:
            pending = tuple(self._pending_output)
            self._pending_output.clear()
            self._output_flush_scheduled = False
        for stream, message in pending:
            self._consume_output(stream, message)

    def _apply_event_after_output(self, event: PipelineEvent) -> None:
        self._flush_queued_output()
        self._apply_event(event)

    def _apply_event(self, event: PipelineEvent) -> None:
        if event.kind == "process-output" and event.message:
            self._consume_output(event.stream or "stdout", event.message)
            return
        if event.kind in {"stage-finished", "pipeline-completed", "pipeline-cancelled"}:
            self._flush_partial_output()
        if event.kind == "stage-started" and event.message:
            self._write_log("command", f"> {event.message}")
        if event.kind == "project-selected" and event.project_dir is not None:
            self.query_one("#run-summary", Static).update(f"项目: {event.project_dir}")
        if event.record is not None and event.stage is not None:
            self._update_stage_row(event.stage)
            if event.message and event.kind == "stage-finished":
                self._write_log("stderr", event.message)
            if event.record.warnings:
                for warning in event.record.warnings:
                    self._write_log("stderr", f"warning: {warning}")
        self._update_progress()

    def _consume_output(self, stream: str, text: str) -> None:
        data = self._partial_output.get(stream, "") + text
        parts = re.split(r"(\r\n|\r|\n)", data)
        pending = parts.pop() if parts else ""
        for index in range(0, len(parts), 2):
            line = parts[index]
            separator = parts[index + 1] if index + 1 < len(parts) else ""
            if separator == "\r":
                self._current_output_widget.update(line[-240:])
            else:
                self._write_log(stream, line)
                self._current_output_widget.update("")
        self._partial_output[stream] = pending

    def _flush_partial_output(self) -> None:
        for stream, pending in self._partial_output.items():
            if pending:
                self._write_log(stream, pending)
                self._partial_output[stream] = ""
        self._current_output_widget.update("")

    def _write_log(self, stream: str, line: str) -> None:
        style = {
            "stderr": "yellow",
            "command": "bold cyan",
        }.get(stream, "")
        self._rendered_log.append((style, line))
        self._log_widget.write(Text(line, style=style))

    def _update_stage_row(self, stage: StageName) -> None:
        if self.pipeline is None:
            return
        record = self.pipeline.records[stage]
        table = self.query_one("#steps", DataTable)
        table.update_cell(stage.value, "status", STATUS_LABELS[record.status])
        table.update_cell(
            stage.value,
            "returncode",
            "—" if record.returncode is None else str(record.returncode),
        )
        table.update_cell(
            stage.value,
            "elapsed",
            _format_elapsed(record.elapsed_seconds),
        )

    def _update_progress(self) -> None:
        if self.pipeline is None:
            return
        finished = sum(
            record.status
            in {
                StageStatus.SUCCESS,
                StageStatus.WARNING,
                StageStatus.SKIPPED,
            }
            for record in self.pipeline.records.values()
        )
        self.query_one("#progress", ProgressBar).update(progress=finished)

    def _refresh_elapsed(self) -> None:
        if self.pipeline is None:
            return
        for stage, record in self.pipeline.records.items():
            if record.status == StageStatus.RUNNING and record._started is not None:
                table = self.query_one("#steps", DataTable)
                table.update_cell(
                    stage.value,
                    "elapsed",
                    _format_elapsed(time.monotonic() - record._started),
                )
        if self.running and self._started_at is not None:
            stage = self.pipeline.current_stage
            label = STAGE_LABELS[stage] if stage is not None else "准备"
            self.query_one("#run-summary", Static).update(
                f"{label} | 总耗时 {_format_elapsed(time.monotonic() - self._started_at)}"
            )

    def _build_request(self) -> PipelineRequest:
        mode = self._mode()
        project_dir = None
        if mode == "resume":
            raw_project = self.query_one("#project-path", Input).value.strip()
            if not raw_project:
                raise PipelineValidationError("请选择或输入已有项目目录")
            project_dir = Path(raw_project)
        raw_reference = self.query_one("#reference", Input).value.strip()
        request = PipelineRequest(
            videos_dir=self.videos_dir,
            project_dir=project_dir,
            bvid=self.query_one("#bvid", Input).value.strip() or None,
            ytid=self.query_one("#ytid", Input).value.strip() or None,
            reference=Path(raw_reference) if raw_reference else None,
            cleanup=self.query_one("#cleanup", Switch).value,
        )
        return validate_pipeline_request(request)

    def _update_mode(self) -> None:
        resume = self._mode() == "resume"
        for selector in (
            "#project-select-label",
            "#project-select",
            "#project-path-label",
            "#project-path",
        ):
            self.query_one(selector).display = resume
        if resume:
            raw_path = self.query_one("#project-path", Input).value.strip()
            if raw_path and Path(raw_path).is_dir():
                self._load_project(Path(raw_path))
            self.query_one("#project-preview", Static).update(
                "选择已有项目后将从最早未完成步骤继续"
            )
        else:
            self.query_one("#bvid", Input).disabled = False
            self.query_one("#ytid", Input).disabled = False
            self.query_one("#reference", Input).disabled = False
            self.query_one("#project-preview", Static).update(
                f"预计项目目录: {preview_project_dir(self.videos_dir)}"
            )

    def _load_project(self, path: Path) -> None:
        try:
            defaults = load_resume_defaults(path)
        except PipelineValidationError as exc:
            self.query_one("#validation", Static).update(str(exc))
            return
        bvid_input = self.query_one("#bvid", Input)
        ytid_input = self.query_one("#ytid", Input)
        reference_input = self.query_one("#reference", Input)
        bvid_input.value = defaults.bvid or ""
        ytid_input.value = defaults.ytid or ""
        reference_input.value = str(defaults.reference or "")
        bvid_input.disabled = defaults.bvid is not None
        ytid_input.disabled = defaults.ytid is not None
        reference_input.disabled = defaults.reference is not None
        self.query_one("#validation", Static).update("")

    def _project_options(self) -> list[tuple[str, str]]:
        paths = list(reversed(video_project_dirs(self.videos_dir)))
        if (
            self.initial_project_dir is not None
            and self.initial_project_dir not in paths
        ):
            paths.insert(0, self.initial_project_dir)
        return [(str(path), str(path)) for path in paths]

    def _mode(self) -> str:
        value = self.query_one("#mode", Select).value
        return "resume" if value == "resume" else "new"

    def _clear_run_view(self) -> None:
        self._log_widget.clear()
        self.query_one("#completion", Static).update("")
        self._current_output_widget.update("")
        self._rendered_log.clear()
        self._partial_output = {"stdout": "", "stderr": ""}
        with self._output_lock:
            self._pending_output.clear()
            self._output_flush_scheduled = False
        self.query_one("#retry", Button).disabled = True
        self.query_one("#back", Button).disabled = True
        self.query_one("#progress", ProgressBar).update(progress=0)
        for stage in STAGES:
            self._update_stage_row(stage)


def run_interactive(
    *,
    videos_dir: Path = Path("videos"),
    project_dir: Path | None = None,
    require_tty: bool = True,
) -> int:
    if require_tty and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        print(
            "vmaf-workflow interactive: an interactive terminal is required",
            file=sys.stderr,
        )
        return 2
    result = WorkflowTui(videos_dir=videos_dir, project_dir=project_dir).run()
    return 0 if result is None else int(result)


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

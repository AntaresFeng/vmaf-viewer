from __future__ import annotations

from pathlib import Path
import json
import threading
from types import SimpleNamespace

import pytest
from textual.widgets import DataTable, Input, Select, Static

from vmaf_workflow.pipeline import (
    PipelineEvent,
    STAGES,
    StageRecord,
    StageStatus,
)
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.tui import WorkflowTui


@pytest.mark.asyncio
async def test_tui_rejects_empty_new_project_form(tmp_path: Path) -> None:
    app = WorkflowTui(videos_dir=tmp_path / "videos")

    async with app.run_test(size=(120, 42)) as pilot:
        await pilot.click("#start")
        await pilot.pause()

        assert "至少填写一个" in str(app.query_one("#validation", Static).content)


@pytest.mark.asyncio
async def test_tui_elapsed_column_fits_full_timestamp(tmp_path: Path) -> None:
    app = WorkflowTui(videos_dir=tmp_path / "videos")

    async with app.run_test(size=(80, 24)):
        record = StageRecord(
            STAGES[0],
            status=StageStatus.SUCCESS,
            returncode=0,
            elapsed_seconds=3_661,
        )
        app.pipeline = SimpleNamespace(records={STAGES[0]: record})
        app._update_stage_row(STAGES[0])

        table = app.query_one("#steps", DataTable)
        elapsed_column = table.ordered_columns[3]
        assert elapsed_column.width >= len("01:01:01")
        assert table.get_cell(STAGES[0].value, "elapsed") == "01:01:01"


@pytest.mark.asyncio
async def test_tui_confirms_and_displays_completed_pipeline(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference")
    created = []

    class FakePipeline:
        def __init__(self, request, *, event_sink):
            self.request = request
            self.event_sink = event_sink
            self.project = WorkflowProject(
                tmp_path / "videos" / "video0",
                tmp_path / "videos" / "video0" / ".workflow",
            )
            self.project.video_dir.mkdir(parents=True)
            self.records = {stage: StageRecord(stage) for stage in STAGES}
            self.current_stage = None
            created.append(self)

        def run(self, *, retry=False):
            assert retry is False
            for stage in STAGES:
                record = self.records[stage]
                record.status = StageStatus.RUNNING
                self.current_stage = stage
                self.event_sink(
                    PipelineEvent(
                        "stage-started",
                        stage,
                        record,
                        f"uv run vmaf-workflow {stage.value}",
                    )
                )
                record.status = StageStatus.SUCCESS
                record.returncode = 0
                record.elapsed_seconds = 0.1
                self.event_sink(PipelineEvent("stage-finished", stage, record))
            self.current_stage = None
            return 0

        def cancel(self):
            raise AssertionError("cancel should not be called")

    app = WorkflowTui(
        videos_dir=tmp_path / "videos",
        pipeline_factory=FakePipeline,
    )

    async with app.run_test(size=(120, 42)) as pilot:
        app.query_one("#bvid", Input).value = "BV1xx411c7mD"
        app.query_one("#reference", Input).value = str(reference)
        await pilot.click("#start")
        await pilot.pause()
        await pilot.click("#dialog-confirm")
        await pilot.pause(0.2)

        assert created
        completion = str(app.query_one("#completion", Static).content)
        assert "video0" in completion
        assert "vmaf-viewer" in completion
        assert app.exit_code == 0


@pytest.mark.asyncio
async def test_tui_resume_mode_prefills_bound_project(tmp_path: Path) -> None:
    project_dir = tmp_path / "videos" / "video4"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    reference = project_dir / "reference.mp4"
    reference.write_bytes(b"reference")
    (workflow_dir / "manifest.json").write_text(
        json.dumps(
            {
                "bilibili": {"bvid": "BV1xx411c7mD"},
                "youtube": {
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                },
            }
        ),
        encoding="utf-8",
    )
    (workflow_dir / "media-inventory.json").write_text(
        json.dumps({"reference": "reference.mp4"}),
        encoding="utf-8",
    )
    app = WorkflowTui(
        videos_dir=tmp_path / "videos",
        project_dir=project_dir,
    )

    async with app.run_test(size=(120, 42)):
        assert app.query_one("#mode", Select).value == "resume"
        assert app.query_one("#bvid", Input).value == "BV1xx411c7mD"
        assert app.query_one("#ytid", Input).value.endswith("dQw4w9WgXcQ")
        assert app.query_one("#reference", Input).value == str(reference)
        assert app.query_one("#bvid", Input).disabled is True
        assert app.query_one("#reference", Input).disabled is True


@pytest.mark.asyncio
async def test_tui_cancel_stops_running_pipeline(tmp_path: Path) -> None:
    reference = tmp_path / "reference.mp4"
    reference.write_bytes(b"reference")
    created = []

    class BlockingPipeline:
        def __init__(self, request, *, event_sink):
            self.request = request
            self.event_sink = event_sink
            self.project = WorkflowProject(
                tmp_path / "videos" / "video0",
                tmp_path / "videos" / "video0" / ".workflow",
            )
            self.records = {stage: StageRecord(stage) for stage in STAGES}
            self.current_stage = STAGES[0]
            self.cancelled = threading.Event()
            created.append(self)

        def run(self, *, retry=False):
            record = self.records[self.current_stage]
            record.status = StageStatus.RUNNING
            self.event_sink(
                PipelineEvent("stage-started", self.current_stage, record, "download")
            )
            assert self.cancelled.wait(timeout=5)
            record.status = StageStatus.CANCELLED
            record.returncode = 130
            self.event_sink(
                PipelineEvent("stage-finished", self.current_stage, record, "已取消")
            )
            return 130

        def cancel(self):
            self.cancelled.set()
            self.event_sink(PipelineEvent("cancelling", stage=self.current_stage))

    app = WorkflowTui(
        videos_dir=tmp_path / "videos",
        pipeline_factory=BlockingPipeline,
    )

    async with app.run_test(size=(120, 42)) as pilot:
        app.query_one("#bvid", Input).value = "BV1xx411c7mD"
        app.query_one("#reference", Input).value = str(reference)
        await pilot.click("#start")
        await pilot.pause()
        await pilot.click("#dialog-confirm")
        await pilot.pause(0.1)
        await pilot.click("#cancel")
        await pilot.pause(0.2)

        assert created[0].cancelled.is_set()
        assert app.exit_code == 130
        assert "已取消" in str(app.query_one("#run-summary", Static).content)


@pytest.mark.asyncio
async def test_tui_resume_leaves_unbound_site_editable(tmp_path: Path) -> None:
    project_dir = tmp_path / "videos" / "video5"
    workflow_dir = project_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "manifest.json").write_text(
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
    app = WorkflowTui(
        videos_dir=tmp_path / "videos",
        project_dir=project_dir,
    )

    async with app.run_test(size=(120, 42)):
        assert app.query_one("#bvid", Input).disabled is True
        assert app.query_one("#ytid", Input).disabled is False


@pytest.mark.asyncio
async def test_tui_rejects_empty_resume_project_on_setup_page(tmp_path: Path) -> None:
    project_dir = tmp_path / "videos" / "video6"
    project_dir.mkdir(parents=True)
    app = WorkflowTui(
        videos_dir=tmp_path / "videos",
        project_dir=project_dir,
    )

    async with app.run_test(size=(120, 42)) as pilot:
        app._confirm_start()
        await pilot.pause()

        assert "至少填写一个" in str(app.query_one("#validation", Static).content)


@pytest.mark.asyncio
async def test_tui_batches_output_and_flushes_final_partial_line(tmp_path: Path) -> None:
    app = WorkflowTui(videos_dir=tmp_path / "videos")

    async with app.run_test(size=(120, 42)) as pilot:
        for index in range(10):
            app._receive_event(
                PipelineEvent(
                    "process-output",
                    STAGES[0],
                    message=f"chunk-{index}\n",
                    stream="stdout",
                )
            )
        assert app._output_flush_scheduled is True
        await pilot.pause(0.1)
        assert any("chunk-9" in line for _style, line in app._rendered_log)

        app._receive_event(
            PipelineEvent(
                "process-output",
                STAGES[0],
                message="final-without-newline",
                stream="stdout",
            )
        )
        record = StageRecord(STAGES[0], status=StageStatus.SUCCESS, returncode=0)
        app._receive_event(PipelineEvent("stage-finished", STAGES[0], record))

        assert app._rendered_log[-1][1] == "final-without-newline"


@pytest.mark.asyncio
async def test_tui_log_is_bounded_to_five_thousand_lines(tmp_path: Path) -> None:
    app = WorkflowTui(videos_dir=tmp_path / "videos")

    async with app.run_test(size=(120, 42)):
        for index in range(5_005):
            app._write_log("stdout", f"line-{index}")

        assert len(app._rendered_log) == 5_000
        assert app._rendered_log[0][1] == "line-5"
        assert app._log_widget.max_lines == 5_000

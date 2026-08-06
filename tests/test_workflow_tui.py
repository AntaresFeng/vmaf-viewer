from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Input, Static

from vmaf_workflow.pipeline import (
    PipelineEvent,
    STAGES,
    StageRecord,
    StageStatus,
)
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.tui import WorkflowTui


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

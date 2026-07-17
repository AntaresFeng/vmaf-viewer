from __future__ import annotations

from pathlib import Path

import pytest

from vmaf_workflow.download_state import (
    DownloadStateError,
    invalidate_downstream,
    load_download_manifest,
    merge_download_manifest,
    validate_source_identity,
)
from vmaf_workflow.project import WorkflowProject


def test_load_download_manifest_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_download_manifest(tmp_path / "manifest.json") is None


@pytest.mark.parametrize("content", ["not-json", "[]"])
def test_load_download_manifest_rejects_invalid_object(
    tmp_path: Path,
    content: str,
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DownloadStateError, match="manifest.json"):
        load_download_manifest(path)


def test_validate_source_identity_accepts_missing_and_same_sources() -> None:
    existing = {
        "bilibili": {"bvid": "BV1xx411c7mD"},
        "youtube": {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        },
    }

    validate_source_identity(None, "BV1xx411c7mD", None)
    validate_source_identity(
        existing,
        "BV1xx411c7mD",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )


@pytest.mark.parametrize(
    ("existing", "bvid", "youtube_url", "message"),
    [
        (
            {"bilibili": {"bvid": "BV1xx411c7mD"}},
            "BV1Q541167Qg",
            None,
            "BVID",
        ),
        (
            {
                "youtube": {
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                }
            },
            None,
            "https://www.youtube.com/watch?v=9bZkp7q19f0",
            "YouTube",
        ),
    ],
)
def test_validate_source_identity_rejects_conflicts(
    existing: dict[str, object],
    bvid: str | None,
    youtube_url: str | None,
    message: str,
) -> None:
    with pytest.raises(DownloadStateError, match=message):
        validate_source_identity(existing, bvid, youtube_url)


def test_merge_download_manifest_preserves_unrequested_source_and_history() -> None:
    existing = {
        "created_at": "first",
        "bilibili": {
            "bvid": "BV1xx411c7mD",
            "downloads": [{"status": "downloaded"}],
        },
        "youtube": {"url": None, "downloads": []},
        "commands": [{"command": ["old"]}],
        "keep": "value",
    }
    current = {
        "created_at": "second",
        "bilibili": {"bvid": None, "downloads": []},
        "youtube": {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "downloads": [],
        },
        "commands": [],
        "project_dir": "video0",
        "workflow_dir": "video0/.workflow",
        "config_files": {"bbdown": "bbdown.config"},
        "dry_run": False,
    }

    merged = merge_download_manifest(
        existing,
        current,
        update_bilibili=False,
        update_youtube=True,
    )

    assert merged["bilibili"] == existing["bilibili"]
    assert merged["youtube"] == current["youtube"]
    assert merged["commands"] == existing["commands"]
    assert merged["created_at"] == "first"
    assert merged["updated_at"] == "second"
    assert merged["project_dir"] == "video0"
    assert merged["keep"] == "value"


def test_merge_download_manifest_replaces_requested_source_snapshot() -> None:
    existing = {
        "created_at": "first",
        "bilibili": {
            "bvid": "BV1xx411c7mD",
            "downloads": [{"status": "downloaded", "old": True}],
        },
        "youtube": {"url": None, "downloads": []},
        "commands": [],
    }
    current = {
        "created_at": "second",
        "bilibili": {
            "bvid": "BV1xx411c7mD",
            "downloads": [],
        },
        "youtube": {"url": None, "downloads": []},
        "commands": [],
        "project_dir": "video0",
        "workflow_dir": "video0/.workflow",
        "config_files": {},
        "dry_run": False,
    }

    merged = merge_download_manifest(
        existing,
        current,
        update_bilibili=True,
        update_youtube=False,
    )

    assert merged["bilibili"] == current["bilibili"]
    assert merged["youtube"] == existing["youtube"]


def test_invalidate_downstream_removes_only_managed_reproducible_state(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    project.workflow_dir.mkdir(parents=True)
    managed = (
        project.media_inventory_path,
        project.package_manifest_path,
        project.default_package_path,
        project.remote_plan_path,
        project.remote_plan_script_path,
        project.remote_state_path,
        project.remote_provenance_path,
        project.default_result_archive_path,
    )
    for path in managed:
        path.write_bytes(b"managed")

    media = project.video_dir / "distorted.mp4"
    result = project.video_dir / "distorted_vmaf.json"
    log = project.remote_run_log_path
    custom_package = tmp_path / "custom-inputs.tar"
    media.write_bytes(b"media")
    result.write_text("{}\n", encoding="utf-8")
    log.write_text("log\n", encoding="utf-8")
    custom_package.write_bytes(b"custom")
    manifest = {
        "reference": {"path": "reference.mp4"},
        "media_inventory": str(project.media_inventory_path),
        "package": {"path": str(custom_package)},
        "remote_plan": {"manifest": str(project.remote_plan_path)},
        "results": {"files": [str(result)]},
        "keep": "value",
    }

    invalidate_downstream(project, manifest)

    assert all(not path.exists() for path in managed)
    assert media.is_file()
    assert result.is_file()
    assert log.is_file()
    assert custom_package.is_file()
    assert manifest == {"keep": "value"}


def test_invalidate_downstream_rejects_non_file_collision(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.media_inventory_path.mkdir(parents=True)

    with pytest.raises(DownloadStateError, match="managed downstream path"):
        invalidate_downstream(project, {})


def _project(tmp_path: Path) -> WorkflowProject:
    video_dir = tmp_path / "video0"
    return WorkflowProject(video_dir, video_dir / ".workflow")

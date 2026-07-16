from __future__ import annotations

from pathlib import Path

import pytest

from vmaf_workflow.config import BBDownSettings, YtDlpSettings
from vmaf_workflow.project import (
    WorkflowProject,
    bbdown_config_text,
    create_project,
    next_video_dir,
    normalize_bvid,
    normalize_youtube_url,
    write_text,
    ytdlp_config_text,
)


def test_next_video_dir_uses_next_numeric_suffix_and_ignores_other_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "video0").mkdir()
    (tmp_path / "video2").mkdir()
    (tmp_path / "videoX").mkdir()

    assert next_video_dir(tmp_path) == tmp_path / "video3"


def test_create_project_creates_video0_workflow_and_infojson_dirs(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path / "videos")

    assert project.video_dir == tmp_path / "videos" / "video0"
    assert project.workflow_dir == project.video_dir / ".workflow"
    assert project.workflow_dir.is_dir()
    assert project.ytdlp_infojson_dir.is_dir()


def test_create_project_reuses_explicit_project_dir_without_next_video(
    tmp_path: Path,
) -> None:
    explicit_dir = tmp_path / "reuse-this"
    explicit_dir.mkdir()
    marker = explicit_dir / "already-downloaded.mp4"
    marker.write_text("keep", encoding="utf-8")

    project = create_project(tmp_path / "videos", project_dir=explicit_dir)

    assert project.video_dir == explicit_dir
    assert project.workflow_dir == explicit_dir / ".workflow"
    assert project.workflow_dir.is_dir()
    assert project.ytdlp_infojson_dir.is_dir()
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "videos" / "video0").exists()


def test_normalize_bvid_accepts_id_and_bilibili_url() -> None:
    bvid = "BV1xx411c7mD"

    assert normalize_bvid(bvid) == bvid
    assert normalize_bvid(f"https://www.bilibili.com/video/{bvid}/?p=2") == bvid


def test_normalize_bvid_raises_clear_error_when_missing() -> None:
    with pytest.raises(ValueError, match="BVID"):
        normalize_bvid("https://www.bilibili.com/bangumi/play/ep1")


def test_normalize_youtube_url_accepts_id_short_url_and_watch_url() -> None:
    video_id = "dQw4w9WgXcQ"
    expected = f"https://www.youtube.com/watch?v={video_id}"

    assert normalize_youtube_url(video_id) == expected
    assert normalize_youtube_url(f"https://youtu.be/{video_id}?si=abc") == expected
    assert (
        normalize_youtube_url(f"https://www.youtube.com/watch?v={video_id}&t=4s")
        == expected
    )


@pytest.mark.parametrize("path_prefix", ["shorts", "embed", "live"])
def test_normalize_youtube_url_accepts_path_id_forms(path_prefix: str) -> None:
    video_id = "dQw4w9WgXcQ"

    assert normalize_youtube_url(
        f"https://www.youtube.com/{path_prefix}/{video_id}"
    ) == (f"https://www.youtube.com/watch?v={video_id}")


def test_normalize_youtube_url_raises_when_missing_video_id() -> None:
    with pytest.raises(ValueError, match="YouTube"):
        normalize_youtube_url("https://www.youtube.com/@example")


def test_bbdown_config_text_locks_outputs_to_project_without_quality_or_encoder(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path / "videos")
    settings = BBDownSettings(file_pattern="single", multi_file_pattern="multi")

    text = bbdown_config_text(project, settings)

    assert "--work-dir" in text
    assert str(project.video_dir) in text
    assert "--file-pattern single" in text
    assert "--multi-file-pattern multi" in text
    assert "--video-only" in text
    assert "--skip-subtitle" in text
    assert "--skip-cover" in text
    assert "-q" not in text.split()
    assert "-e" not in text.split()


def test_ytdlp_config_text_locks_outputs_to_project(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path / "videos")
    settings = YtDlpSettings(
        format_selector="bestvideo[height>=1080]",
        output_template="%(id)s-%(format_note)s-%(vcodec)s.%(ext)s",
    )

    text = ytdlp_config_text(project, settings)

    assert "--ignore-config" in text
    assert "-f bestvideo[height>=1080]" in text
    assert "--no-write-subs" in text
    assert "--no-write-thumbnail" in text
    assert "--write-info-json" in text
    assert "--no-clean-infojson" in text
    assert f"-P home:{project.video_dir.as_posix()}" in text
    assert f"-P temp:{(project.video_dir / '.yt-dlp-temp').as_posix()}" in text
    assert "%(id)s-%(format_note)s-%(vcodec)s.%(ext)s" in text
    assert project.ytdlp_infojson_dir.as_posix() in text
    assert (
        f"-o infojson:{project.ytdlp_infojson_dir.as_posix()}/"
        "%(id)s-%(format_note)s-%(vcodec)s"
    ) in text
    assert "%(id)s-%(format_note)s-%(vcodec)s.info.json" not in text
    assert (
        "--print-to-file "
        f"after_video:%()j {project.ytdlp_after_video_jsonl_path.as_posix()}" in text
    )
    for line in text.splitlines():
        if line.startswith(("-P home:", "-P temp:", "-o infojson:", "--print-to-file")):
            assert "\\" not in line


def test_ytdlp_config_text_uses_absolute_paths_for_relative_project() -> None:
    project = WorkflowProject(
        video_dir=Path("videos") / "video0",
        workflow_dir=Path("videos") / "video0" / ".workflow",
    )

    text = ytdlp_config_text(project, YtDlpSettings())

    assert "-P home:videos/video0" not in text
    assert f"-P home:{project.video_dir.resolve().as_posix()}" in text
    assert (
        f"-o infojson:{project.ytdlp_infojson_dir.resolve().as_posix()}/"
        "%(id)s-%(format_note)s-%(vcodec)s"
    ) in text
    assert (
        "--print-to-file "
        f"after_video:%()j {project.ytdlp_after_video_jsonl_path.resolve().as_posix()}"
        in text
    )


def test_workflow_project_paths_and_write_text_create_parent_dirs(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path / "videos")

    assert project.bbdown_config_path == project.workflow_dir / "bbdown.config"
    assert project.ytdlp_config_path == project.workflow_dir / "yt-dlp.conf"
    assert project.ytdlp_preflight_path == (
        project.workflow_dir / "yt-dlp.preflight.raw.json"
    )
    assert project.ytdlp_after_video_jsonl_path == (
        project.workflow_dir / "yt-dlp.after_video.jsonl"
    )
    assert project.manifest_path == project.workflow_dir / "manifest.json"
    assert project.remote_state_path == project.workflow_dir / "remote-state.json"
    assert project.remote_provenance_path == (
        project.workflow_dir / "remote-provenance.json"
    )
    assert project.remote_upload_log_path == (
        project.workflow_dir / "remote-upload.log"
    )
    assert project.remote_run_log_path == project.workflow_dir / "remote-run.log"
    assert project.remote_fetch_log_path == (
        project.workflow_dir / "remote-fetch.log"
    )
    assert project.default_result_archive_path == (
        project.workflow_dir / "video0-json.tar.gz"
    )

    target = tmp_path / "nested" / "utf8.txt"
    write_text(target, "hello\n")

    assert target.read_text(encoding="utf-8") == "hello\n"

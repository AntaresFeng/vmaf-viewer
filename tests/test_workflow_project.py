from __future__ import annotations

from pathlib import Path

from vmaf_workflow.config import YtDlpSettings
from vmaf_workflow.project import (
    create_project,
    next_video_dir,
    normalize_bvid,
    normalize_youtube_url,
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


def test_normalize_bvid_accepts_id_and_bilibili_url() -> None:
    bvid = "BV1xx411c7mD"

    assert normalize_bvid(bvid) == bvid
    assert normalize_bvid(f"https://www.bilibili.com/video/{bvid}/?p=2") == bvid


def test_normalize_youtube_url_accepts_id_short_url_and_watch_url() -> None:
    video_id = "dQw4w9WgXcQ"
    expected = f"https://www.youtube.com/watch?v={video_id}"

    assert normalize_youtube_url(video_id) == expected
    assert normalize_youtube_url(f"https://youtu.be/{video_id}?si=abc") == expected
    assert (
        normalize_youtube_url(f"https://www.youtube.com/watch?v={video_id}&t=4s")
        == expected
    )


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

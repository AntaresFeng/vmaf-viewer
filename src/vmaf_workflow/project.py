from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from vmaf_workflow.config import BBDownSettings, YtDlpSettings


BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")
YOUTUBE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
YOUTUBE_PATH_ID_PREFIXES = {"shorts", "embed", "live"}


@dataclass(frozen=True)
class WorkflowProject:
    video_dir: Path
    workflow_dir: Path

    @property
    def bbdown_config_path(self) -> Path:
        return self.workflow_dir / "bbdown.config"

    @property
    def ytdlp_config_path(self) -> Path:
        return self.workflow_dir / "yt-dlp.conf"

    @property
    def ytdlp_preflight_path(self) -> Path:
        return self.workflow_dir / "yt-dlp.preflight.raw.json"

    @property
    def ytdlp_after_video_jsonl_path(self) -> Path:
        return self.workflow_dir / "yt-dlp.after_video.jsonl"

    @property
    def ytdlp_infojson_dir(self) -> Path:
        return self.workflow_dir / "yt-dlp-infojson"

    @property
    def manifest_path(self) -> Path:
        return self.workflow_dir / "manifest.json"


def next_video_dir(videos_dir: Path) -> Path:
    max_index = -1
    if videos_dir.exists():
        for child in videos_dir.iterdir():
            if not child.is_dir():
                continue
            match = re.fullmatch(r"video(\d+)", child.name)
            if match:
                max_index = max(max_index, int(match.group(1)))
    return videos_dir / f"video{max_index + 1}"


def create_project(videos_dir: Path) -> WorkflowProject:
    videos_dir.mkdir(parents=True, exist_ok=True)
    video_dir = next_video_dir(videos_dir)
    workflow_dir = video_dir / ".workflow"
    project = WorkflowProject(video_dir=video_dir, workflow_dir=workflow_dir)

    video_dir.mkdir(parents=True, exist_ok=False)
    workflow_dir.mkdir(parents=True, exist_ok=True)
    project.ytdlp_infojson_dir.mkdir(parents=True, exist_ok=True)
    return project


def normalize_bvid(value: str) -> str:
    match = BVID_RE.search(value.strip())
    if not match:
        raise ValueError("BVID is required")
    return match.group(0)


def normalize_youtube_url(value: str) -> str:
    stripped = value.strip()
    if YOUTUBE_ID_RE.fullmatch(stripped):
        video_id = stripped
    else:
        parsed = urlparse(stripped)
        host = parsed.netloc.lower()
        if host in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.lstrip("/").split("/", 1)[0]
        elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if not video_id:
                video_id = _youtube_path_video_id(parsed.path)
        else:
            video_id = ""

    if not YOUTUBE_ID_RE.fullmatch(video_id):
        raise ValueError("YouTube video id is required")
    return f"https://www.youtube.com/watch?v={video_id}"


def _youtube_path_video_id(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] in YOUTUBE_PATH_ID_PREFIXES:
        return parts[1]
    return ""


def bbdown_config_text(project: WorkflowProject, settings: BBDownSettings) -> str:
    return "\n".join(
        (
            "--work-dir",
            str(project.video_dir),
            f"--file-pattern {settings.file_pattern}",
            f"--multi-file-pattern {settings.multi_file_pattern}",
            "--video-only",
            "--skip-subtitle",
            "--skip-cover",
            "",
        )
    )


def ytdlp_config_text(project: WorkflowProject, settings: YtDlpSettings) -> str:
    temp_dir = project.video_dir / ".yt-dlp-temp"
    output_template = settings.output_template
    infojson_template = str(
        project.ytdlp_infojson_dir
        / _replace_output_template_ext(output_template, ".info.json")
    )
    return "\n".join(
        (
            "--ignore-config",
            f"-f {settings.format_selector}",
            "--no-write-subs",
            "--no-write-thumbnail",
            "--write-info-json",
            "--no-clean-infojson",
            f"-P home:{project.video_dir}",
            f"-P temp:{temp_dir}",
            f"-o {output_template}",
            f"-o infojson:{infojson_template}",
            (
                "--print-to-file "
                f"after_video:%()j {project.ytdlp_after_video_jsonl_path}"
            ),
            "",
        )
    )


def _replace_output_template_ext(output_template: str, suffix: str) -> str:
    dotted_ext_token = ".%(ext)s"
    if output_template.endswith(dotted_ext_token):
        return f"{output_template[: -len(dotted_ext_token)]}{suffix}"

    ext_token = "%(ext)s"
    if output_template.endswith(ext_token):
        return f"{output_template[: -len(ext_token)]}{suffix}"
    return f"{output_template}{suffix}"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

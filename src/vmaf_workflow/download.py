from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from vmaf_workflow.bbdown import (
    bbdown_info_argv,
    bbdown_interactive_argv,
    build_bilibili_plan,
    find_stream_index,
    parse_bbdown_streams,
)
from vmaf_workflow.config import WorkflowSettings, default_settings
from vmaf_workflow.download_state import (
    DownloadStateError,
    invalidate_downstream,
    load_download_manifest,
    merge_download_manifest,
    validate_source_identity,
)
from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.models import CommandResult, DownloadDecision, Manifest
from vmaf_workflow.project import (
    WorkflowProject,
    bbdown_config_text,
    create_project,
    normalize_bvid,
    normalize_youtube_url,
    project_from_dir,
    write_text,
    ytdlp_config_text,
)
from vmaf_workflow.runner import console_output_encoding
from vmaf_workflow.ytdlp import (
    load_after_video_downloads,
    load_sidecar_downloads,
    parse_ytdlp_preflight,
    ytdlp_download_argv,
    ytdlp_preflight_argv,
)


class DownloadInputError(ValueError):
    pass


@dataclass(frozen=True)
class DownloadOutcome:
    project: WorkflowProject
    manifest: Manifest
    returncode: int
    bvid: str | None
    youtube_url: str | None


def download_sources(
    *,
    runner,
    bvid: str | None = None,
    ytid: str | None = None,
    videos_dir: Path = Path("videos"),
    project_dir: Path | None = None,
    dry_run: bool = False,
    settings: WorkflowSettings | None = None,
) -> DownloadOutcome:
    """Validate sources, create/reuse a project, and run requested downloads."""
    if not bvid and not ytid:
        raise DownloadInputError("at least one of --bvid or --ytid is required")

    active_settings = replace(
        settings or default_settings(),
        videos_dir=videos_dir,
    )
    explicit_project = project_from_dir(project_dir) if project_dir is not None else None
    reuse_project = project_dir is not None and project_dir.exists()

    try:
        normalized_bvid = normalize_bvid(bvid) if bvid else None
        youtube_url = normalize_youtube_url(ytid) if ytid else None
        existing_manifest = (
            load_download_manifest(explicit_project.manifest_path)
            if explicit_project is not None
            else None
        )
        validate_source_identity(existing_manifest, normalized_bvid, youtube_url)
    except (ValueError, DownloadStateError) as exc:
        raise DownloadInputError(str(exc)) from exc

    if dry_run and existing_manifest is not None:
        return DownloadOutcome(
            explicit_project,
            existing_manifest,
            0,
            normalized_bvid,
            youtube_url,
        )

    project = create_project(active_settings.videos_dir, project_dir=project_dir)
    write_text(
        project.bbdown_config_path,
        bbdown_config_text(project, active_settings.bbdown),
    )
    write_text(
        project.ytdlp_config_path,
        ytdlp_config_text(project, active_settings.ytdlp),
    )
    current_manifest = _base_manifest(
        project,
        normalized_bvid,
        youtube_url,
        dry_run,
    )
    try:
        manifest = merge_download_manifest(
            existing_manifest,
            current_manifest,
            update_bilibili=normalized_bvid is not None,
            update_youtube=youtube_url is not None,
        )
        if reuse_project and not dry_run:
            invalidate_downstream(project, manifest)
    except DownloadStateError as exc:
        raise DownloadInputError(str(exc)) from exc

    write_manifest(project.manifest_path, manifest)
    if dry_run:
        return DownloadOutcome(
            project,
            manifest,
            0,
            normalized_bvid,
            youtube_url,
        )

    try:
        returncode = _run_downloads(
            project,
            active_settings,
            normalized_bvid,
            youtube_url,
            runner,
            manifest,
        )
    finally:
        write_manifest(project.manifest_path, manifest)

    return DownloadOutcome(
        project,
        manifest,
        returncode,
        normalized_bvid,
        youtube_url,
    )


def requested_incomplete_sources(manifest: Manifest) -> tuple[bool, bool]:
    """Return whether the bound Bilibili and YouTube sources need a retry."""
    return (
        _source_incomplete(manifest.get("bilibili"), "bvid"),
        _source_incomplete(manifest.get("youtube"), "url"),
    )


def _source_incomplete(source: object, identity_key: str) -> bool:
    if not isinstance(source, dict) or not source.get(identity_key):
        return False
    downloads = source.get("downloads")
    return not isinstance(downloads, list) or not downloads or any(
        not isinstance(item, dict) or item.get("status") != "downloaded"
        for item in downloads
    )


def _base_manifest(
    project: WorkflowProject,
    bvid: str | None,
    youtube_url: str | None,
    dry_run: bool,
) -> Manifest:
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "project_dir": str(project.video_dir),
        "workflow_dir": str(project.workflow_dir),
        "config_files": {
            "bbdown": str(project.bbdown_config_path),
            "yt_dlp": str(project.ytdlp_config_path),
        },
        "bilibili": {
            "bvid": bvid,
            "preflight_streams": [],
            "download_plan": [],
            "downloads": [],
        },
        "youtube": {
            "url": youtube_url,
            "preflight_raw_json": None,
            "preflight_streams": [],
            "download_plan": [],
            "downloads": [],
        },
        "commands": [],
    }


def _append_command(manifest: Manifest, result: CommandResult) -> None:
    manifest["commands"].append(result.to_manifest())


def _run_downloads(
    project: WorkflowProject,
    settings: WorkflowSettings,
    bvid: str | None,
    youtube_url: str | None,
    runner,
    manifest: Manifest,
) -> int:
    exit_code = 0
    if bvid:
        exit_code = max(
            exit_code,
            _run_bilibili_downloads(project, settings, bvid, runner, manifest),
        )
    if youtube_url:
        exit_code = max(
            exit_code,
            _run_youtube_downloads(project, settings, youtube_url, runner, manifest),
        )
    return exit_code


def _run_bilibili_downloads(
    project: WorkflowProject,
    settings: WorkflowSettings,
    bvid: str,
    runner,
    manifest: Manifest,
) -> int:
    output_encoding = console_output_encoding()
    info_result = runner.run(
        bbdown_info_argv(settings.bbdown.exe_path, bvid, project.bbdown_config_path),
        output_encoding=output_encoding,
    )
    _append_command(manifest, info_result)
    if info_result.returncode != 0:
        manifest["bilibili"]["skipped"] = {"bbdown_preflight_failed": []}
        return 1

    streams = parse_bbdown_streams(info_result.stdout)
    plan, skipped = build_bilibili_plan(streams)
    manifest["bilibili"]["preflight_streams"] = [
        stream.to_manifest() for stream in streams
    ]
    manifest["bilibili"]["download_plan"] = [
        stream.to_manifest() for stream in plan
    ]
    manifest["bilibili"]["skipped"] = skipped

    exit_code = 0
    fresh_streams = []
    if plan:
        fresh_result = runner.run(
            bbdown_info_argv(settings.bbdown.exe_path, bvid, project.bbdown_config_path),
            output_encoding=output_encoding,
        )
        _append_command(manifest, fresh_result)
        if fresh_result.returncode != 0:
            for stream in plan:
                manifest["bilibili"]["downloads"].append(
                    DownloadDecision(
                        downloader="bbdown",
                        stream=stream,
                        status="skipped",
                        reason="bbdown_refresh_failed_before_download",
                    ).to_manifest()
                )
            return 1
        fresh_streams = parse_bbdown_streams(fresh_result.stdout)

    for stream in plan:
        selected_index = find_stream_index(stream, fresh_streams)
        if selected_index is None:
            manifest["bilibili"]["downloads"].append(
                DownloadDecision(
                    downloader="bbdown",
                    stream=stream,
                    status="skipped",
                    reason="planned_stream_not_found_before_download",
                ).to_manifest()
            )
            exit_code = 1
            continue

        download_result = runner.run(
            bbdown_interactive_argv(
                settings.bbdown.exe_path,
                bvid,
                project.bbdown_config_path,
            ),
            stdin=f"{selected_index}\n",
            output_encoding=output_encoding,
        )
        _append_command(manifest, download_result)
        manifest["bilibili"]["downloads"].append(
            DownloadDecision(
                downloader="bbdown",
                stream=stream,
                status="downloaded" if download_result.returncode == 0 else "failed",
                command=download_result,
            ).to_manifest()
        )
        if download_result.returncode != 0:
            exit_code = 1
    return exit_code


def _run_youtube_downloads(
    project: WorkflowProject,
    settings: WorkflowSettings,
    youtube_url: str,
    runner,
    manifest: Manifest,
) -> int:
    preflight_result = runner.run(
        ytdlp_preflight_argv(settings.ytdlp.exe_path, youtube_url)
    )
    _append_command(manifest, preflight_result)
    if preflight_result.returncode != 0:
        manifest["youtube"]["downloads"].append(
            DownloadDecision(
                downloader="yt-dlp",
                status="skipped",
                reason="youtube_preflight_failed",
                command=preflight_result,
            ).to_manifest()
        )
        return 1

    project.ytdlp_preflight_path.write_text(preflight_result.stdout, encoding="utf-8")
    manifest["youtube"]["preflight_raw_json"] = str(project.ytdlp_preflight_path)
    try:
        raw_ytdlp = json.loads(preflight_result.stdout)
    except json.JSONDecodeError:
        raw_ytdlp = None

    if not isinstance(raw_ytdlp, dict):
        manifest["youtube"]["downloads"].append(
            DownloadDecision(
                downloader="yt-dlp",
                status="failed",
                reason="youtube_preflight_json_invalid",
                command=preflight_result,
            ).to_manifest()
        )
        return 1
    selected_streams, requested_streams = parse_ytdlp_preflight(raw_ytdlp)
    manifest["youtube"]["preflight_streams"] = [
        stream.to_manifest() for stream in selected_streams
    ]
    manifest["youtube"]["download_plan"] = [
        stream.to_manifest() for stream in requested_streams
    ]

    download_result = runner.run(
        ytdlp_download_argv(
            settings.ytdlp.exe_path,
            project.ytdlp_config_path,
            youtube_url,
        )
    )
    _append_command(manifest, download_result)
    actual_downloads = load_after_video_downloads(
        project.ytdlp_after_video_jsonl_path
    ) or load_sidecar_downloads(project.ytdlp_infojson_dir)
    if not actual_downloads:
        manifest["youtube"]["downloads"].append(
            DownloadDecision(
                downloader="yt-dlp",
                status="failed",
                reason=(
                    "youtube_download_metadata_missing"
                    if download_result.returncode == 0
                    else "youtube_download_failed"
                ),
                command=download_result,
            ).to_manifest()
        )
        return 1

    manifest["youtube"]["downloads"] = [
        DownloadDecision(
            downloader="yt-dlp",
            stream=stream,
            status="downloaded" if download_result.returncode == 0 else "failed",
            command=download_result,
        ).to_manifest()
        for stream in actual_downloads
    ]
    return 0 if download_result.returncode == 0 else 1

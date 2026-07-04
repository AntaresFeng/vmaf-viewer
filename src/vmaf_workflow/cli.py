from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from vmaf_workflow.bbdown import (
    bbdown_info_argv,
    bbdown_interactive_argv,
    build_bilibili_plan,
    find_stream_index,
    parse_bbdown_streams,
)
from vmaf_workflow.config import default_settings
from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.models import CommandResult, DownloadDecision, Manifest
from vmaf_workflow.packager import PackageError, package_project
from vmaf_workflow.prepare import PrepareError, prepare_project
from vmaf_workflow.project import (
    WorkflowProject,
    bbdown_config_text,
    create_project,
    normalize_bvid,
    normalize_youtube_url,
    write_text,
    ytdlp_config_text,
)
from vmaf_workflow.runner import SubprocessRunner
from vmaf_workflow.ytdlp import (
    load_after_video_downloads,
    load_sidecar_downloads,
    parse_ytdlp_preflight,
    ytdlp_download_argv,
    ytdlp_preflight_argv,
)


def main(argv: Sequence[str] | None = None, runner=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "download":
        return _download(args, runner or SubprocessRunner())
    if args.command == "prepare":
        return _prepare(args)
    if args.command == "package":
        return _package(args)

    parser.error("a command is required")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vmaf-workflow")
    subparsers = parser.add_subparsers(dest="command")

    download = subparsers.add_parser("download")
    download.add_argument("--bvid")
    download.add_argument("--ytid")
    download.add_argument(
        "--videos-dir",
        default="videos",
        type=Path,
    )
    download.add_argument(
        "--project-dir",
        type=Path,
    )
    download.add_argument("--dry-run", action="store_true")

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--project-dir", type=Path)
    prepare.add_argument("--reference", type=Path)

    package = subparsers.add_parser("package")
    package.add_argument("--project-dir", type=Path)
    package.add_argument("--output", type=Path)

    return parser


def _package(args: argparse.Namespace) -> int:
    if args.project_dir is None:
        print("vmaf-workflow package: --project-dir is required", file=sys.stderr)
        return 2

    project = WorkflowProject(
        video_dir=args.project_dir,
        workflow_dir=args.project_dir / ".workflow",
    )
    try:
        package_project(project, args.output)
    except PackageError as exc:
        print(f"vmaf-workflow package: {exc}", file=sys.stderr)
        return 2
    return 0


def _prepare(args: argparse.Namespace) -> int:
    if args.project_dir is None:
        print("vmaf-workflow prepare: --project-dir is required", file=sys.stderr)
        return 2
    if args.reference is None:
        print("vmaf-workflow prepare: --reference is required", file=sys.stderr)
        return 2

    project = create_project(args.project_dir.parent, project_dir=args.project_dir)
    try:
        prepare_project(project, args.reference)
    except PrepareError as exc:
        print(f"vmaf-workflow prepare: {exc}", file=sys.stderr)
        return 2
    return 0


def _download(args: argparse.Namespace, runner) -> int:
    if not args.bvid and not args.ytid:
        print(
            "vmaf-workflow download: at least one of --bvid or --ytid is required",
            file=sys.stderr,
        )
        return 2

    settings = replace(default_settings(), videos_dir=args.videos_dir)

    try:
        bvid = normalize_bvid(args.bvid) if args.bvid else None
        youtube_url = normalize_youtube_url(args.ytid) if args.ytid else None
    except ValueError as exc:
        print(f"vmaf-workflow download: {exc}", file=sys.stderr)
        return 2

    project = create_project(settings.videos_dir, project_dir=args.project_dir)

    write_text(project.bbdown_config_path, bbdown_config_text(project, settings.bbdown))
    write_text(project.ytdlp_config_path, ytdlp_config_text(project, settings.ytdlp))
    if args.dry_run:
        write_manifest(
            project.manifest_path, _base_manifest(project, bvid, youtube_url, True)
        )
        return 0

    manifest = _base_manifest(project, bvid, youtube_url, False)
    exit_code = _run_downloads(project, settings, bvid, youtube_url, runner, manifest)
    write_manifest(project.manifest_path, manifest)
    return exit_code


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
    settings,
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
    settings,
    bvid: str,
    runner,
    manifest: Manifest,
) -> int:
    info_result = runner.run(
        bbdown_info_argv(settings.bbdown.exe_path, bvid, project.bbdown_config_path)
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
    manifest["bilibili"]["download_plan"] = [stream.to_manifest() for stream in plan]
    manifest["bilibili"]["skipped"] = skipped

    exit_code = 0
    fresh_streams = []
    if plan:
        fresh_result = runner.run(
            bbdown_info_argv(settings.bbdown.exe_path, bvid, project.bbdown_config_path)
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
                settings.bbdown.exe_path, bvid, project.bbdown_config_path
            ),
            stdin=f"{selected_index}\n",
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
    settings,
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
            settings.ytdlp.exe_path, project.ytdlp_config_path, youtube_url
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


if __name__ == "__main__":
    raise SystemExit(main())

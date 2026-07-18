from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from vmaf_workflow.cleanup import (
    CleanupExecutionError,
    CleanupStateError,
    cleanup_project,
)
from vmaf_workflow.config import default_settings
from vmaf_workflow.download import DownloadInputError, download_sources
from vmaf_workflow.packager import PackageError, package_project
from vmaf_workflow.prepare import PrepareError, prepare_project
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
from vmaf_workflow.project import (
    WorkflowProject,
    create_project,
    project_from_dir,
)
from vmaf_workflow.runner import SubprocessRunner
from vmaf_workflow.status import WorkflowStatusError, inspect_workflow_status


def main(argv: Sequence[str] | None = None, runner=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_runner = runner or SubprocessRunner()

    if args.command == "download":
        return _download(args, command_runner)
    if args.command == "prepare":
        return _prepare(args)
    if args.command == "package":
        return _package(args)
    if args.command == "remote-plan":
        return _remote_plan(args)
    if args.command == "upload":
        return _upload(args, command_runner)
    if args.command == "run":
        return _run_remote(args, command_runner)
    if args.command == "fetch-results":
        return _fetch_results(args, command_runner)
    if args.command == "cleanup":
        return _cleanup(args)
    if args.command == "status":
        return _status(args)
    if args.command in {"interactive", "auto"}:
        return _interactive(args)

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

    remote_plan = subparsers.add_parser("remote-plan")
    remote_plan.add_argument("--project-dir", type=Path)
    remote_plan.add_argument("--easyvmaf-repo", type=Path)

    upload = subparsers.add_parser("upload")
    upload.add_argument("--project-dir", type=Path)
    upload.add_argument("--host")
    upload.add_argument("--remote-dir", type=PurePosixPath)

    run = subparsers.add_parser("run")
    run.add_argument("--project-dir", type=Path)

    fetch_results_parser = subparsers.add_parser("fetch-results")
    fetch_results_parser.add_argument("--project-dir", type=Path)

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--project-dir", type=Path)

    status = subparsers.add_parser("status")
    status.add_argument("--project-dir", type=Path)

    interactive = subparsers.add_parser("interactive", aliases=["auto"])
    interactive.add_argument("--videos-dir", default="videos", type=Path)
    interactive.add_argument("--project-dir", type=Path)

    return parser


def _interactive(args: argparse.Namespace) -> int:
    from vmaf_workflow.tui import run_interactive

    return run_interactive(
        videos_dir=args.videos_dir,
        project_dir=args.project_dir,
    )


def _status(args: argparse.Namespace) -> int:
    if args.project_dir is None:
        print("vmaf-workflow status: --project-dir is required", file=sys.stderr)
        return 2
    project = _explicit_project(args.project_dir)
    try:
        status = inspect_workflow_status(project)
    except WorkflowStatusError as exc:
        print(f"vmaf-workflow status: {exc}", file=sys.stderr)
        return 2

    print(f"project: {status.project}")
    print(f"stage: {status.stage}")
    print(f"state: {status.state}")
    if status.missing_artifacts:
        print("missing artifacts:")
        for artifact in status.missing_artifacts:
            print(f"  - {artifact}")
    else:
        print("missing artifacts: none")
    print(f"next command: {status.next_command}")
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    if args.project_dir is None:
        print("vmaf-workflow cleanup: --project-dir is required", file=sys.stderr)
        return 2
    project = _explicit_project(args.project_dir)
    try:
        state = cleanup_project(project)
    except CleanupStateError as exc:
        print(f"vmaf-workflow cleanup: {exc}", file=sys.stderr)
        return 2
    except CleanupExecutionError as exc:
        print(f"vmaf-workflow cleanup: {exc}", file=sys.stderr)
        return 1

    reclaimed_bytes = state["cleanup"]["last_reclaimed_bytes"]
    print(f"cleanup completed: {reclaimed_bytes} bytes reclaimed")
    return 0


def _upload(args: argparse.Namespace, runner) -> int:
    if args.project_dir is None:
        print("vmaf-workflow upload: --project-dir is required", file=sys.stderr)
        return 2
    project = _explicit_project(args.project_dir)
    settings = default_settings().remote.with_target(
        host=args.host,
        work_dir=args.remote_dir,
    )
    try:
        upload_project(project, settings, runner)
    except (RemoteWorkflowError, RemoteTargetError) as exc:
        print(f"vmaf-workflow upload: {exc}", file=sys.stderr)
        return 2
    except RemoteRunInterrupted:
        print("vmaf-workflow upload: interrupted", file=sys.stderr)
        return 130
    except RemoteCommandError as exc:
        print(f"vmaf-workflow upload: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_remote(args: argparse.Namespace, runner) -> int:
    if args.project_dir is None:
        print("vmaf-workflow run: --project-dir is required", file=sys.stderr)
        return 2
    project = _explicit_project(args.project_dir)
    try:
        run_remote_project(project, default_settings().remote, runner)
    except (RemoteWorkflowError, RemoteTargetError) as exc:
        print(f"vmaf-workflow run: {exc}", file=sys.stderr)
        return 2
    except RemoteRunInterrupted:
        print("vmaf-workflow run: interrupted", file=sys.stderr)
        return 130
    except RemoteCommandError as exc:
        print(f"vmaf-workflow run: {exc}", file=sys.stderr)
        return 1
    return 0


def _fetch_results(args: argparse.Namespace, runner) -> int:
    if args.project_dir is None:
        print(
            "vmaf-workflow fetch-results: --project-dir is required",
            file=sys.stderr,
        )
        return 2
    project = _explicit_project(args.project_dir)
    try:
        fetch_results(project, default_settings().remote, runner)
    except (RemoteWorkflowError, RemoteTargetError) as exc:
        print(f"vmaf-workflow fetch-results: {exc}", file=sys.stderr)
        return 2
    except RemoteRunInterrupted:
        print("vmaf-workflow fetch-results: interrupted", file=sys.stderr)
        return 130
    except RemoteCommandError as exc:
        print(f"vmaf-workflow fetch-results: {exc}", file=sys.stderr)
        return 1
    return 0


def _explicit_project(project_dir: Path) -> WorkflowProject:
    return project_from_dir(project_dir)


def _remote_plan(args: argparse.Namespace) -> int:
    if args.project_dir is None:
        print("vmaf-workflow remote-plan: --project-dir is required", file=sys.stderr)
        return 2

    project = _explicit_project(args.project_dir)
    settings = default_settings().easyvmaf
    if args.easyvmaf_repo is not None:
        settings = settings.with_repo_dir(args.easyvmaf_repo)
    try:
        plan = write_remote_plan(project, settings)
    except RemotePlanError as exc:
        print(f"vmaf-workflow remote-plan: {exc}", file=sys.stderr)
        return 2
    for warning in plan.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _package(args: argparse.Namespace) -> int:
    if args.project_dir is None:
        print("vmaf-workflow package: --project-dir is required", file=sys.stderr)
        return 2

    project = _explicit_project(args.project_dir)
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
    try:
        outcome = download_sources(
            runner=runner,
            bvid=args.bvid,
            ytid=args.ytid,
            videos_dir=args.videos_dir,
            project_dir=args.project_dir,
            dry_run=args.dry_run,
        )
    except DownloadInputError as exc:
        print(f"vmaf-workflow download: {exc}", file=sys.stderr)
        return 2
    return outcome.returncode


if __name__ == "__main__":
    raise SystemExit(main())

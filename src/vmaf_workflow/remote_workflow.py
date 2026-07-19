from __future__ import annotations

import json
import shutil
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from vmaf_workflow.config import RemoteSettings
from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_plan import (
    REMOTE_PLAN_SCHEMA_VERSION,
    RESULT_PROVENANCE_NAME,
    WATERMARK_MAPPING_CONTRACT,
    RemotePlanError,
    validate_package_inputs,
)
from vmaf_workflow.remote_state import (
    RemoteStateError,
    load_remote_state,
    sha256_file,
    utc_now,
    write_remote_state,
)
from vmaf_workflow.remote_transport import RemoteTransport, RemoteTransportError


class RemoteWorkflowError(ValueError):
    pass


class RemoteCommandError(RuntimeError):
    pass


class RemoteRunInterrupted(KeyboardInterrupt):
    pass


def upload_project(
    project: WorkflowProject,
    settings: RemoteSettings,
    runner,
    transport: RemoteTransport | None = None,
) -> dict[str, Any]:
    inventory = _load_json_object(
        project.media_inventory_path,
        "media-inventory.json",
    )
    package_manifest = _load_json_object(
        project.package_manifest_path,
        "package-manifest.json",
    )
    try:
        package_name = validate_package_inputs(
            project,
            inventory,
            package_manifest,
        )
    except RemotePlanError as exc:
        raise RemoteWorkflowError(str(exc)) from exc
    plan = _load_json_object(project.remote_plan_path, "remote-plan.json")
    if not project.remote_plan_script_path.is_file():
        raise RemoteWorkflowError(
            f"remote-plan.sh is required: {project.remote_plan_script_path}"
        )
    if plan.get("package_archive") != package_name:
        raise RemoteWorkflowError(
            "remote plan package does not match package manifest; rerun remote-plan"
        )
    _validate_plan_against_inventory(plan, inventory)
    environment_argument = _required_string(
        plan,
        "environment_preflight_argument",
        "remote-plan.json",
    )
    preflight_argument = _required_string(
        plan,
        "preflight_argument",
        "remote-plan.json",
    )
    result_provenance = _required_string(
        plan,
        "result_provenance",
        "remote-plan.json",
    )
    if result_provenance != RESULT_PROVENANCE_NAME:
        raise RemoteWorkflowError(
            "remote-plan.json result_provenance is unsupported"
        )
    package_path = _resolve_package_path(project, package_manifest, package_name)
    package_sha256 = sha256_file(package_path)
    script_sha256 = sha256_file(project.remote_plan_script_path)
    plan_sha256 = sha256_file(project.remote_plan_path)
    provenance = {
        "schema_version": 1,
        "project": project.video_dir.name,
        "plan_sha256": plan_sha256,
        "package_sha256": package_sha256,
        "script_sha256": script_sha256,
        "score_scope": plan.get("score_scope"),
        "content_exclusions": plan.get("content_exclusions", []),
    }
    write_manifest(project.remote_provenance_path, provenance)
    provenance_sha256 = sha256_file(project.remote_provenance_path)

    target_settings = settings.with_target(
        work_dir=(
            settings.work_dir
            / project.video_dir.name
            / plan_sha256
        )
    )
    active_transport = transport or RemoteTransport(target_settings, runner)
    remote_script_path = (
        target_settings.work_dir / project.remote_plan_script_path.name
    )
    remote_package_path = target_settings.work_dir / package_name
    remote_provenance_path = (
        target_settings.work_dir / result_provenance
    )
    started_at = utc_now()
    state: dict[str, Any] = {
        "schema_version": 1,
        "project": project.video_dir.name,
        "updated_at": started_at,
        "remote": {
            "host": target_settings.host,
            "base_work_dir": settings.work_dir.as_posix(),
            "work_dir": target_settings.work_dir.as_posix(),
        },
        "plan": {
            "path": str(project.remote_plan_path),
            "created_at": plan.get("created_at"),
            "sha256": plan_sha256,
            "score_scope": plan.get("score_scope"),
            "content_exclusions": plan.get("content_exclusions", []),
        },
        "upload": {
            "status": "running",
            "started_at": started_at,
            "log": str(project.remote_upload_log_path),
            "package": _artifact_state(
                package_path,
                remote_package_path,
                package_sha256,
            ),
            "script": _artifact_state(
                project.remote_plan_script_path,
                remote_script_path,
                script_sha256,
            ),
            "provenance": _artifact_state(
                project.remote_provenance_path,
                remote_provenance_path,
                provenance_sha256,
            ),
        },
    }
    project.remote_upload_log_path.parent.mkdir(parents=True, exist_ok=True)
    project.remote_upload_log_path.write_text("", encoding="utf-8")
    write_remote_state(project.remote_state_path, state)
    _update_manifest_state_pointer(project)

    stage = "create-work-dir"
    try:
        active_transport.ensure_work_dir(project.remote_upload_log_path)

        stage = "upload-script"
        script_transferred = active_transport.upload_atomic(
            project.remote_plan_script_path,
            remote_script_path,
            script_sha256,
            project.remote_upload_log_path,
        )
        state["upload"]["script"]["transferred"] = script_transferred

        stage = "upload-provenance"
        provenance_transferred = active_transport.upload_atomic(
            project.remote_provenance_path,
            remote_provenance_path,
            provenance_sha256,
            project.remote_upload_log_path,
        )
        state["upload"]["provenance"]["transferred"] = provenance_transferred

        stage = "environment-preflight"
        environment_returncode = active_transport.stream_script(
            remote_script_path,
            environment_argument,
            project.remote_upload_log_path,
        )
        state["upload"]["environment_preflight"] = {
            "returncode": environment_returncode
        }
        if environment_returncode != 0:
            raise RemoteCommandError(
                f"environment preflight failed with exit code "
                f"{environment_returncode}"
            )

        stage = "upload-package"
        package_transferred = active_transport.upload_atomic(
            package_path,
            remote_package_path,
            package_sha256,
            project.remote_upload_log_path,
        )
        state["upload"]["package"]["transferred"] = package_transferred

        stage = "verify-hashes"
        _require_remote_hash(
            active_transport,
            remote_script_path,
            script_sha256,
            project.remote_upload_log_path,
        )
        _require_remote_hash(
            active_transport,
            remote_package_path,
            package_sha256,
            project.remote_upload_log_path,
        )
        _require_remote_hash(
            active_transport,
            remote_provenance_path,
            provenance_sha256,
            project.remote_upload_log_path,
        )

        stage = "package-preflight"
        preflight_returncode = active_transport.stream_script(
            remote_script_path,
            preflight_argument,
            project.remote_upload_log_path,
        )
        state["upload"]["package_preflight"] = {
            "returncode": preflight_returncode
        }
        if preflight_returncode != 0:
            raise RemoteCommandError(
                f"package preflight failed with exit code {preflight_returncode}"
            )
    except (RemoteTransportError, RemoteCommandError) as exc:
        state["upload"].update(
            {
                "status": "failed",
                "stage": stage,
                "completed_at": utc_now(),
                "error": str(exc),
            }
        )
        write_remote_state(project.remote_state_path, state)
        if isinstance(exc, RemoteCommandError):
            raise
        raise RemoteCommandError(str(exc)) from exc
    except KeyboardInterrupt as exc:
        state["upload"].update(
            {
                "status": "interrupted",
                "stage": stage,
                "completed_at": utc_now(),
                "returncode": 130,
            }
        )
        write_remote_state(project.remote_state_path, state)
        raise RemoteRunInterrupted() from exc

    state["upload"].update(
        {
            "status": "completed",
            "completed_at": utc_now(),
        }
    )
    write_remote_state(project.remote_state_path, state)
    return state


def run_remote_project(
    project: WorkflowProject,
    base_settings: RemoteSettings,
    runner,
    transport: RemoteTransport | None = None,
) -> dict[str, Any]:
    state, plan, target_settings, active_transport = _load_remote_context(
        project,
        base_settings,
        runner,
        transport,
    )
    script_remote_path = _state_remote_path(
        state,
        "upload",
        "script",
        "remote_path",
    )
    preflight_argument = _required_string(
        plan,
        "preflight_argument",
        "remote-plan.json",
    )
    result_archive = _required_string(
        plan,
        "result_archive",
        "remote-plan.json",
    )
    result_remote_path = target_settings.work_dir / result_archive
    project.remote_run_log_path.parent.mkdir(parents=True, exist_ok=True)
    project.remote_run_log_path.write_text("", encoding="utf-8")
    started_at = utc_now()
    state["run"] = {
        "status": "running",
        "started_at": started_at,
        "log": str(project.remote_run_log_path),
    }
    state.pop("fetch", None)
    write_remote_state(project.remote_state_path, state)

    stage = "verify-inputs"
    try:
        _require_uploaded_artifact_hash(
            state,
            active_transport,
            "script",
            project.remote_run_log_path,
        )
        _require_uploaded_artifact_hash(
            state,
            active_transport,
            "package",
            project.remote_run_log_path,
        )
        _require_uploaded_artifact_hash(
            state,
            active_transport,
            "provenance",
            project.remote_run_log_path,
        )

        stage = "preflight"
        preflight_returncode = active_transport.stream_script(
            script_remote_path,
            preflight_argument,
            project.remote_run_log_path,
            append=False,
        )
        state["run"]["preflight"] = {"returncode": preflight_returncode}
        if preflight_returncode != 0:
            raise RemoteCommandError(
                f"remote preflight failed with exit code {preflight_returncode}"
            )

        stage = "run"
        returncode = active_transport.stream_run(
            script_remote_path,
            project.remote_run_log_path,
            append=True,
        )
        state["run"]["returncode"] = returncode
        if returncode != 0:
            raise RemoteCommandError(
                f"remote run failed with exit code {returncode}"
            )

        stage = "result-hash"
        result_sha256 = active_transport.remote_sha256(
            result_remote_path,
            project.remote_run_log_path,
        )
        if result_sha256 is None:
            raise RemoteCommandError(
                f"remote result archive is missing: {result_remote_path}"
            )
    except KeyboardInterrupt as exc:
        state["run"].update(
            {
                "status": "interrupted",
                "stage": stage,
                "completed_at": utc_now(),
                "returncode": 130,
            }
        )
        write_remote_state(project.remote_state_path, state)
        raise RemoteRunInterrupted() from exc
    except (RemoteTransportError, RemoteCommandError) as exc:
        state["run"].update(
            {
                "status": "failed",
                "stage": stage,
                "completed_at": utc_now(),
                "error": str(exc),
            }
        )
        write_remote_state(project.remote_state_path, state)
        if isinstance(exc, RemoteCommandError):
            raise
        raise RemoteCommandError(str(exc)) from exc

    state["run"].update(
        {
            "status": "completed",
            "completed_at": utc_now(),
            "returncode": 0,
            "result": {
                "remote_path": result_remote_path.as_posix(),
                "sha256": result_sha256,
            },
        }
    )
    write_remote_state(project.remote_state_path, state)
    return state


def fetch_results(
    project: WorkflowProject,
    base_settings: RemoteSettings,
    runner,
    transport: RemoteTransport | None = None,
) -> dict[str, Any]:
    state, plan, target_settings, active_transport = _load_remote_context(
        project,
        base_settings,
        runner,
        transport,
    )
    result_archive = _required_string(
        plan,
        "result_archive",
        "remote-plan.json",
    )
    expected_results = _expected_result_paths(plan, project)
    result_provenance = _required_string(
        plan,
        "result_provenance",
        "remote-plan.json",
    )
    run_state = state.get("run")
    if isinstance(run_state, dict) and run_state.get("status") == "completed":
        result_state = run_state.get("result")
        if not isinstance(result_state, dict):
            raise RemoteWorkflowError("completed remote run has no result state")
        remote_path = _pure_posix_path(
            result_state.get("remote_path"),
            "remote run result path",
        )
        tracked_sha256 = result_state.get("sha256")
        if not isinstance(tracked_sha256, str):
            raise RemoteWorkflowError("completed remote run has no result SHA-256")
        source = "workflow-run"
    else:
        remote_path = target_settings.work_dir / result_archive
        tracked_sha256 = None
        source = "existing-remote"

    project.remote_fetch_log_path.parent.mkdir(parents=True, exist_ok=True)
    project.remote_fetch_log_path.write_text("", encoding="utf-8")
    started_at = utc_now()
    state["fetch"] = {
        "status": "running",
        "started_at": started_at,
        "log": str(project.remote_fetch_log_path),
        "source": source,
    }
    write_remote_state(project.remote_state_path, state)

    temp_archive = project.workflow_dir / (
        f".{result_archive}.download-{uuid.uuid4().hex}"
    )
    staging_dir = project.workflow_dir / (
        f".results-staging-{uuid.uuid4().hex}"
    )
    stage = "remote-hash"
    try:
        remote_sha256 = active_transport.remote_sha256(
            remote_path,
            project.remote_fetch_log_path,
        )
        if remote_sha256 is None:
            raise RemoteCommandError(
                f"remote result archive is missing: {remote_path}"
            )
        if tracked_sha256 is not None and remote_sha256 != tracked_sha256:
            raise RemoteCommandError(
                "remote result SHA-256 differs from completed run"
            )

        stage = "download"
        active_transport.download(
            remote_path,
            temp_archive,
            project.remote_fetch_log_path,
        )
        local_sha256 = sha256_file(temp_archive)
        if local_sha256 != remote_sha256:
            raise RemoteCommandError("downloaded result SHA-256 mismatch")

        stage = "validate"
        validated_files = _read_validated_results(
            temp_archive,
            expected_results,
            project,
            result_provenance,
            state,
        )

        stage = "install"
        installed_paths = _install_results_transactionally(
            project,
            validated_files,
            temp_archive,
            staging_dir,
        )
    except KeyboardInterrupt as exc:
        state["fetch"].update(
            {
                "status": "interrupted",
                "stage": stage,
                "completed_at": utc_now(),
                "returncode": 130,
            }
        )
        write_remote_state(project.remote_state_path, state)
        if temp_archive.exists():
            temp_archive.unlink()
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise RemoteRunInterrupted() from exc
    except (
        OSError,
        tarfile.TarError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RemoteTransportError,
        RemoteCommandError,
        RemoteWorkflowError,
    ) as exc:
        state["fetch"].update(
            {
                "status": "failed",
                "stage": stage,
                "completed_at": utc_now(),
                "error": str(exc),
            }
        )
        write_remote_state(project.remote_state_path, state)
        if temp_archive.exists():
            temp_archive.unlink()
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        if isinstance(exc, RemoteWorkflowError):
            raise
        if isinstance(exc, RemoteCommandError):
            raise
        if isinstance(exc, RemoteTransportError):
            raise RemoteCommandError(str(exc)) from exc
        raise RemoteWorkflowError(str(exc)) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

    validated_at = utc_now()
    state["fetch"].update(
        {
            "status": "completed",
            "completed_at": validated_at,
            "source": source,
            "archive": {
                "remote_path": remote_path.as_posix(),
                "local_path": str(project.default_result_archive_path),
                "sha256": remote_sha256,
                "size_bytes": project.default_result_archive_path.stat().st_size,
            },
            "files": [
                {
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                }
                for path in installed_paths
            ],
        }
    )
    write_remote_state(project.remote_state_path, state)
    _update_manifest_results(project, installed_paths, validated_at)
    return state


def _artifact_state(
    local_path: Path,
    remote_path: PurePosixPath,
    sha256: str,
) -> dict[str, Any]:
    return {
        "local_path": str(local_path),
        "remote_path": remote_path.as_posix(),
        "size_bytes": local_path.stat().st_size,
        "sha256": sha256,
        "transferred": False,
    }


def _load_remote_context(
    project: WorkflowProject,
    base_settings: RemoteSettings,
    runner,
    transport: RemoteTransport | None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    RemoteSettings,
    RemoteTransport,
]:
    try:
        state = load_remote_state(project.remote_state_path)
    except RemoteStateError as exc:
        raise RemoteWorkflowError(str(exc)) from exc
    if state.get("project") != project.video_dir.name:
        raise RemoteWorkflowError("remote state project does not match project dir")
    upload_state = state.get("upload")
    if not isinstance(upload_state, dict) or upload_state.get("status") != "completed":
        raise RemoteWorkflowError("remote upload must be completed first")

    plan = _load_json_object(project.remote_plan_path, "remote-plan.json")
    plan_state = state.get("plan")
    if not isinstance(plan_state, dict):
        raise RemoteWorkflowError("remote state has no plan")
    current_plan_sha256 = sha256_file(project.remote_plan_path)
    if plan_state.get("sha256") != current_plan_sha256:
        raise RemoteWorkflowError("remote plan changed; rerun upload")

    remote_state = state.get("remote")
    if not isinstance(remote_state, dict):
        raise RemoteWorkflowError("remote state has no target")
    host = remote_state.get("host")
    work_dir = remote_state.get("work_dir")
    if not isinstance(host, str):
        raise RemoteWorkflowError("remote state host is invalid")
    target_work_dir = _pure_posix_path(work_dir, "remote work directory")
    target_settings = base_settings.with_target(
        host=host,
        work_dir=target_work_dir,
    )
    active_transport = transport or RemoteTransport(target_settings, runner)
    return state, plan, target_settings, active_transport


def _expected_result_paths(
    plan: dict[str, Any],
    project: WorkflowProject,
) -> list[str]:
    raw_results = plan.get("expected_results")
    if not isinstance(raw_results, list) or not raw_results:
        raise RemoteWorkflowError(
            "remote-plan.json must contain expected_results"
        )
    results = []
    for raw_path in raw_results:
        if not isinstance(raw_path, str):
            raise RemoteWorkflowError("expected result paths must be strings")
        pure_path = PurePosixPath(raw_path)
        if (
            pure_path.is_absolute()
            or "\\" in raw_path
            or ".." in pure_path.parts
            or len(pure_path.parts) < 2
            or pure_path.parts[0] != project.video_dir.name
            or pure_path.suffix.lower() != ".json"
        ):
            raise RemoteWorkflowError(f"invalid expected result path: {raw_path}")
        results.append(raw_path)
    if len(results) != len(set(results)):
        raise RemoteWorkflowError("expected result paths must be unique")
    return results


def _validate_plan_against_inventory(
    plan: dict[str, Any],
    inventory: dict[str, Any],
) -> None:
    if plan.get("schema_version") != REMOTE_PLAN_SCHEMA_VERSION:
        raise RemoteWorkflowError(
            "remote plan schema is unsupported; rerun remote-plan"
        )
    if plan.get("watermark_mapping_contract") != WATERMARK_MAPPING_CONTRACT:
        raise RemoteWorkflowError(
            "remote plan watermark mapping contract is unsupported; "
            "rerun remote-plan"
        )
    inventory_files = inventory.get("files")
    if not isinstance(inventory_files, list):
        raise RemoteWorkflowError(
            "media-inventory.json must contain a files list"
        )
    reference_paths = [
        entry.get("path")
        for entry in inventory_files
        if isinstance(entry, dict) and entry.get("role") == "reference"
    ]
    distorted_paths = [
        entry.get("path")
        for entry in inventory_files
        if isinstance(entry, dict) and entry.get("role") == "distorted"
    ]
    if len(reference_paths) != 1 or not isinstance(reference_paths[0], str):
        raise RemoteWorkflowError(
            "media-inventory.json must contain one reference"
        )
    if not distorted_paths or not all(
        isinstance(path, str) for path in distorted_paths
    ):
        raise RemoteWorkflowError(
            "media-inventory.json must contain distorted files"
        )

    plan_reference = plan.get("reference")
    if (
        not isinstance(plan_reference, dict)
        or plan_reference.get("path") != reference_paths[0]
    ):
        raise RemoteWorkflowError(
            "remote plan reference does not match media inventory; "
            "rerun remote-plan"
        )
    commands = plan.get("commands")
    if not isinstance(commands, list):
        raise RemoteWorkflowError("remote-plan.json must contain commands")

    command_distorted_paths = []
    command_results = []
    for command in commands:
        if not isinstance(command, dict):
            raise RemoteWorkflowError("remote plan commands must be objects")
        distorted = command.get("distorted")
        reference = command.get("reference")
        expected_result = command.get("expected_result")
        if not isinstance(distorted, dict) or not isinstance(
            distorted.get("path"), str
        ):
            raise RemoteWorkflowError(
                "remote plan command distorted path is invalid"
            )
        if (
            not isinstance(reference, dict)
            or reference.get("path") != reference_paths[0]
        ):
            raise RemoteWorkflowError(
                "remote plan command reference does not match inventory"
            )
        if not isinstance(expected_result, str):
            raise RemoteWorkflowError(
                "remote plan command expected_result is invalid"
            )
        command_distorted_paths.append(distorted["path"])
        command_results.append(expected_result)

    if command_distorted_paths != distorted_paths:
        raise RemoteWorkflowError(
            "remote plan distorted files do not match media inventory; "
            "rerun remote-plan"
        )
    if plan.get("expected_results") != command_results:
        raise RemoteWorkflowError(
            "remote plan expected_results do not match commands; "
            "rerun remote-plan"
        )
    inventory_exclusions = inventory.get("content_exclusions", [])
    if plan.get("content_exclusions", []) != inventory_exclusions:
        raise RemoteWorkflowError(
            "remote plan content exclusions do not match media inventory; "
            "rerun remote-plan"
        )
    expected_scope = (
        "content_excluding_regions" if inventory_exclusions else "full_frame"
    )
    if plan.get("score_scope") != expected_scope:
        raise RemoteWorkflowError(
            "remote plan score scope does not match media inventory; "
            "rerun remote-plan"
        )


def _read_validated_results(
    archive_path: Path,
    expected_results: list[str],
    project: WorkflowProject,
    result_provenance: str,
    state: dict[str, Any],
) -> list[tuple[Path, bytes]]:
    expected_set = {*expected_results, result_provenance}
    validated = []
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        member_names = [member.name for member in members]
        if len(member_names) != len(set(member_names)):
            raise RemoteWorkflowError(
                "result archive contains duplicate members"
            )
        if set(member_names) != expected_set:
            if result_provenance not in member_names:
                raise RemoteWorkflowError(
                    "result archive is missing provenance"
                )
            raise RemoteWorkflowError(
                "result archive members do not match remote plan"
            )
        members_by_name = {member.name: member for member in members}
        provenance_member = members_by_name[result_provenance]
        if not provenance_member.isfile():
            raise RemoteWorkflowError(
                "result provenance is not a regular file"
            )
        provenance_file = archive.extractfile(provenance_member)
        if provenance_file is None:
            raise RemoteWorkflowError("result provenance cannot be read")
        provenance = json.loads(
            provenance_file.read().decode("utf-8")
        )
        _validate_result_provenance(provenance, state)
        for expected_path in expected_results:
            member = members_by_name[expected_path]
            if not member.isfile():
                raise RemoteWorkflowError(
                    f"result archive member is not a regular file: {expected_path}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RemoteWorkflowError(
                    f"result archive member cannot be read: {expected_path}"
                )
            content = extracted.read()
            parsed = json.loads(content.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise RemoteWorkflowError(
                    f"result JSON must be an object: {expected_path}"
                )
            pure_path = PurePosixPath(expected_path)
            relative_path = PurePosixPath(*pure_path.parts[1:])
            destination = project.video_dir.joinpath(*relative_path.parts)
            validated.append((destination, content))
    return validated


def _validate_result_provenance(
    provenance: Any,
    state: dict[str, Any],
) -> None:
    if not isinstance(provenance, dict):
        raise RemoteWorkflowError("result provenance must be a JSON object")
    if provenance.get("schema_version") != 1:
        raise RemoteWorkflowError(
            "result provenance schema_version must be 1"
        )
    if provenance.get("project") != state.get("project"):
        raise RemoteWorkflowError("result provenance project does not match")
    plan_state = state.get("plan")
    upload_state = state.get("upload")
    if not isinstance(plan_state, dict) or not isinstance(upload_state, dict):
        raise RemoteWorkflowError("remote state provenance inputs are invalid")
    if provenance.get("plan_sha256") != plan_state.get("sha256"):
        raise RemoteWorkflowError(
            "result provenance plan SHA-256 does not match"
        )
    if provenance.get("score_scope") != plan_state.get("score_scope"):
        raise RemoteWorkflowError(
            "result provenance score scope does not match remote state"
        )
    if provenance.get("content_exclusions") != plan_state.get(
        "content_exclusions"
    ):
        raise RemoteWorkflowError(
            "result provenance exclusions do not match remote state"
        )
    for artifact, key in (
        ("package", "package_sha256"),
        ("script", "script_sha256"),
    ):
        artifact_state = upload_state.get(artifact)
        if (
            not isinstance(artifact_state, dict)
            or provenance.get(key) != artifact_state.get("sha256")
        ):
            raise RemoteWorkflowError(
                f"result provenance {artifact} SHA-256 does not match"
            )


def _install_results_transactionally(
    project: WorkflowProject,
    validated_files: list[tuple[Path, bytes]],
    temp_archive: Path,
    staging_dir: Path,
) -> list[Path]:
    staging_dir.mkdir(parents=True)
    staged_files: list[tuple[Path, Path]] = []
    for destination, content in validated_files:
        relative_destination = destination.relative_to(project.video_dir)
        staged_path = staging_dir / relative_destination
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_bytes(content)
        staged_files.append((staged_path, destination))

    backup_dir = project.workflow_dir / (
        f".results-backup-{uuid.uuid4().hex}"
    )
    backup_dir.mkdir(parents=True)
    backup_targets = [
        destination for _staged_path, destination in staged_files
    ]
    backup_targets.append(project.default_result_archive_path)
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for index, destination in enumerate(backup_targets):
            if not destination.exists():
                continue
            backup_path = backup_dir / f"{index}-{destination.name}"
            destination.replace(backup_path)
            backups.append((backup_path, destination))

        for staged_path, destination in staged_files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged_path.replace(destination)
            installed.append(destination)

        temp_archive.replace(project.default_result_archive_path)
        installed.append(project.default_result_archive_path)
    except BaseException as exc:
        rollback_errors = []
        for installed_path in reversed(installed):
            try:
                if installed_path.exists():
                    installed_path.unlink()
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        for backup_path, destination in reversed(backups):
            try:
                if backup_path.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    backup_path.replace(destination)
            except OSError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            raise RemoteWorkflowError(
                f"{exc}; result rollback failed: "
                + "; ".join(rollback_errors)
            ) from exc
        raise
    finally:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

    return [destination for _staged_path, destination in staged_files]


def _require_remote_hash(
    transport,
    remote_path: PurePosixPath,
    expected_sha256: str,
    log_path: Path,
) -> None:
    actual_sha256 = transport.remote_sha256(remote_path, log_path)
    if actual_sha256 != expected_sha256:
        raise RemoteCommandError(f"remote SHA-256 mismatch: {remote_path}")


def _require_uploaded_artifact_hash(
    state: dict[str, Any],
    transport,
    artifact: str,
    log_path: Path,
) -> None:
    artifact_state = state.get("upload", {}).get(artifact)
    if not isinstance(artifact_state, dict):
        raise RemoteWorkflowError(
            f"remote state has no upload.{artifact} section"
        )
    remote_path = _pure_posix_path(
        artifact_state.get("remote_path"),
        f"remote state upload.{artifact}.remote_path",
    )
    expected_sha256 = artifact_state.get("sha256")
    if not isinstance(expected_sha256, str):
        raise RemoteWorkflowError(
            f"remote state upload.{artifact}.sha256 is invalid"
        )
    _require_remote_hash(
        transport,
        remote_path,
        expected_sha256,
        log_path,
    )


def _resolve_package_path(
    project: WorkflowProject,
    package_manifest: dict[str, Any],
    package_name: str,
) -> Path:
    raw_path = package_manifest.get("archive_path")
    if not isinstance(raw_path, str):
        raise RemoteWorkflowError("package-manifest.json must contain archive_path")
    package_path = Path(raw_path)
    if package_path.is_file():
        return package_path
    fallback = project.workflow_dir / package_name
    if fallback.is_file():
        return fallback
    raise RemoteWorkflowError(f"package archive is required: {raw_path}")


def _load_json_object(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise RemoteWorkflowError(f"{name} is required: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RemoteWorkflowError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise RemoteWorkflowError(f"{name} must be a JSON object: {path}")
    return data


def _required_string(
    data: dict[str, Any],
    key: str,
    source_name: str,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RemoteWorkflowError(f"{source_name} must contain {key}")
    return value


def _state_remote_path(
    state: dict[str, Any],
    section: str,
    artifact: str,
    key: str,
) -> PurePosixPath:
    section_state = state.get(section)
    if not isinstance(section_state, dict):
        raise RemoteWorkflowError(f"remote state has no {section} section")
    artifact_state = section_state.get(artifact)
    if not isinstance(artifact_state, dict):
        raise RemoteWorkflowError(
            f"remote state has no {section}.{artifact} section"
        )
    return _pure_posix_path(
        artifact_state.get(key),
        f"remote state {section}.{artifact}.{key}",
    )


def _pure_posix_path(value: Any, name: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise RemoteWorkflowError(f"{name} is invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise RemoteWorkflowError(f"{name} is invalid: {value}")
    return path


def _update_manifest_state_pointer(project: WorkflowProject) -> None:
    manifest = _load_optional_json_object(project.manifest_path)
    manifest["project_dir"] = str(project.video_dir)
    manifest["workflow_dir"] = str(project.workflow_dir)
    manifest["remote_workflow"] = {
        "state": str(project.remote_state_path),
    }
    write_manifest(project.manifest_path, manifest)


def _update_manifest_results(
    project: WorkflowProject,
    installed_paths: list[Path],
    validated_at: str,
) -> None:
    manifest = _load_optional_json_object(project.manifest_path)
    manifest["project_dir"] = str(project.video_dir)
    manifest["workflow_dir"] = str(project.workflow_dir)
    manifest["results"] = {
        "archive": str(project.default_result_archive_path),
        "files": [str(path) for path in installed_paths],
        "validated_at": validated_at,
    }
    write_manifest(project.manifest_path, manifest)


def _load_optional_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RemoteWorkflowError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise RemoteWorkflowError(f"manifest must be a JSON object: {path}")
    return data
